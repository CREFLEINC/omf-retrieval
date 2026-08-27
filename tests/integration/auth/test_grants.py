"""PostgreSQL integration contracts for API clients and source grants."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import UTC, datetime, timedelta
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from omf_retrieval.application.admin.service import (
    ClientAccessService,
    ClientAdminService,
)
from omf_retrieval.application.admin.tokens import (
    AuthenticationError,
    KeyIdCollision,
    SourceAccessError,
    StoredClient,
)
from omf_retrieval.infrastructure.database.models import (
    ApiClient,
    ClientSourceGrant,
    SourceProfile,
)
from omf_retrieval.infrastructure.database.repository_auth import (
    ClientPersistenceError,
    PostgresClientRepository,
    has_source_grant_in_session,
)
from omf_retrieval.infrastructure.database.session import (
    create_database_engine,
    create_session_factory,
)

_SUPPORT_SPEC = spec_from_file_location(
    "task10_auth_database_test_utils",
    Path(__file__).parents[1] / "database" / "database_test_utils.py",
)
assert _SUPPORT_SPEC is not None and _SUPPORT_SPEC.loader is not None
_DATABASE_SUPPORT = module_from_spec(_SUPPORT_SPEC)
_SUPPORT_SPEC.loader.exec_module(_DATABASE_SUPPORT)

TABLES = "search_audit_events, client_source_grants, api_clients, source_profiles"
NOW = datetime(2026, 8, 25, 1, 2, 3, tzinfo=UTC)


@pytest.fixture
def auth_sessions(
    request: pytest.FixtureRequest,
) -> Iterator[sessionmaker[Session]]:
    """Yield an isolated session factory against the migrated test schema."""
    engine = create_database_engine(_DATABASE_SUPPORT.test_database_url())
    request.addfinalizer(engine.dispose)
    with engine.begin() as connection:
        _DATABASE_SUPPORT.assert_safe_test_connection(connection)
        connection.execute(text(f"TRUNCATE TABLE {TABLES} CASCADE"))

    def clean() -> None:
        with engine.begin() as connection:
            _DATABASE_SUPPORT.assert_safe_test_connection(connection)
            connection.execute(text(f"TRUNCATE TABLE {TABLES} CASCADE"))

    request.addfinalizer(clean)
    yield create_session_factory(engine)


class _RandomBytes:
    def __init__(self) -> None:
        self.ordinal = 0

    def __call__(self, size: int) -> bytes:
        if size == 8:
            self.ordinal += 1
            return self.ordinal.to_bytes(8, "big")
        return bytes([self.ordinal]) * size


def _seed_sources(sessions: sessionmaker[Session], *keys: str) -> None:
    with sessions.begin() as database_session:
        database_session.add_all(SourceProfile(source_key=key) for key in keys)


def _services(
    sessions: sessionmaker[Session],
) -> tuple[PostgresClientRepository, ClientAdminService, ClientAccessService]:
    repository = PostgresClientRepository(sessions)
    return (
        repository,
        ClientAdminService(repository, random_bytes=_RandomBytes(), clock=lambda: NOW),
        ClientAccessService(repository, clock=lambda: NOW),
    )


def _revoke_with_lock_timeout(
    sessions: sessionmaker[Session], key_id: str, started: Event
) -> bool:
    """Attempt one status update with a bounded PostgreSQL lock wait."""
    with sessions.begin() as database_session:
        database_session.execute(text("SET LOCAL lock_timeout = '3000ms'"))
        started.set()
        return (
            database_session.scalar(
                update(ApiClient)
                .where(ApiClient.key_id == key_id)
                .values(status="revoked")
                .returning(ApiClient.id)
            )
            is not None
        )


def test_token_is_hash_only_and_exact_grant_precedes_operation(
    auth_sessions: sessionmaker[Session],
) -> None:
    _seed_sources(auth_sessions, "omf", "other")
    repository, admin, access = _services(auth_sessions)

    issued = admin.create_client("agent-a", {"omf"})
    called = []
    result = access.execute_authorized(
        issued.token, "omf", lambda context: called.append(context) or "ok"
    )

    with auth_sessions() as database_session:
        stored = database_session.scalar(
            select(ApiClient).where(ApiClient.id == issued.client_id)
        )
        grant_count = database_session.scalar(
            select(func.count()).select_from(ClientSourceGrant)
        )
    assert result == "ok" and len(called) == 1
    assert stored is not None
    assert stored.token_hash == hashlib.sha256(issued.secret).digest()
    assert issued.token not in repr(stored)
    assert issued.secret not in repr(stored).encode()
    assert grant_count == 1
    assert repository.has_source_grant(issued.client_id, "omf") is True
    assert repository.has_source_grant(issued.client_id, "other") is False

    for denied in ("other", "missing"):
        with pytest.raises(SourceAccessError):
            access.execute_authorized(
                issued.token, denied, lambda _context: pytest.fail("searched")
            )


def test_parallel_tokens_list_safely_and_revoke_independently(
    auth_sessions: sessionmaker[Session],
) -> None:
    _seed_sources(auth_sessions, "omf")
    repository = PostgresClientRepository(auth_sessions)
    random_bytes = _RandomBytes()
    admin = ClientAdminService(repository, random_bytes=random_bytes, clock=lambda: NOW)
    first = admin.create_client("agent-a", {"omf"})
    second = admin.create_client("agent-b", {"omf"})

    listed = admin.list_clients()
    assert {view.key_id for view in listed} == {first.key_id, second.key_id}
    assert all(view.status == "active" for view in listed)
    assert all(view.source_keys == frozenset({"omf"}) for view in listed)
    assert first.token not in repr(listed) and second.token not in repr(listed)

    assert admin.revoke_client(first.key_id) is True
    assert admin.revoke_client(first.key_id) is True
    assert repository.has_source_grant(first.client_id, "omf") is False
    assert admin.revoke_client("f" * 16) is False
    statuses = {view.key_id: view.status for view in admin.list_clients()}
    assert statuses == {first.key_id: "revoked", second.key_id: "active"}


@pytest.mark.parametrize("initial_status", ["active", "disabled", "revoked"])
def test_revoke_is_idempotent_for_every_known_status(
    auth_sessions: sessionmaker[Session], initial_status: str
) -> None:
    _seed_sources(auth_sessions, "omf")
    repository, admin, _access = _services(auth_sessions)
    issued = admin.create_client("agent", {"omf"})
    with auth_sessions.begin() as database_session:
        database_session.get(ApiClient, issued.client_id).status = initial_status

    assert repository.revoke_client(issued.key_id) is True
    assert repository.revoke_client(issued.key_id) is True
    assert repository.find_client_by_key_id(issued.key_id).status == "revoked"  # type: ignore[union-attr]


@pytest.mark.parametrize("status", ["disabled", "revoked"])
def test_inactive_lookup_remains_exact_but_authentication_is_one_401(
    auth_sessions: sessionmaker[Session], status: str
) -> None:
    _seed_sources(auth_sessions, "omf")
    repository, admin, access = _services(auth_sessions)
    issued = admin.create_client("agent", {"omf"})
    with auth_sessions.begin() as database_session:
        database_session.get(ApiClient, issued.client_id).status = status

    assert repository.find_client_by_key_id(issued.key_id).status == status  # type: ignore[union-attr]
    assert repository.has_source_grant(issued.client_id, "omf") is False
    with pytest.raises(AuthenticationError):
        access.authenticate(issued.token)


def test_expiry_round_trips_as_utc_for_authentication_policy(
    auth_sessions: sessionmaker[Session],
) -> None:
    _seed_sources(auth_sessions, "omf")
    repository = PostgresClientRepository(auth_sessions)
    client = StoredClient(
        uuid4(), "expired", "a" * 16, b"h" * 32, "active", NOW - timedelta(1), NOW
    )
    repository.save_client(client, frozenset({"omf"}))

    loaded = repository.find_client_by_key_id(client.key_id)
    assert loaded is not None
    assert loaded.created_at == NOW and loaded.created_at.tzinfo is UTC
    assert loaded.expires_at == NOW - timedelta(1)
    assert repository.has_source_grant(client.id, "omf") is False


def test_key_id_collision_is_mapped_but_other_integrity_errors_propagate(
    auth_sessions: sessionmaker[Session],
) -> None:
    _seed_sources(auth_sessions, "omf")
    repository = PostgresClientRepository(auth_sessions)
    first = StoredClient(uuid4(), "first", "b" * 16, b"1" * 32, "active", None, NOW)
    repository.save_client(first, frozenset({"omf"}))

    with pytest.raises(KeyIdCollision):
        repository.save_client(
            StoredClient(
                uuid4(), "collision", first.key_id, b"2" * 32, "active", None, NOW
            ),
            frozenset({"omf"}),
        )
    with pytest.raises(IntegrityError):
        repository.save_client(
            StoredClient(
                first.id, "id collision", "c" * 16, b"3" * 32, "active", None, NOW
            ),
            frozenset({"omf"}),
        )

    with auth_sessions() as database_session:
        assert database_session.scalar(select(func.count()).select_from(ApiClient)) == 1
        assert (
            database_session.scalar(select(func.count()).select_from(ClientSourceGrant))
            == 1
        )


def test_unknown_source_rolls_back_client_and_safe_error_hides_inputs(
    auth_sessions: sessionmaker[Session],
) -> None:
    _seed_sources(auth_sessions, "omf")
    repository = PostgresClientRepository(auth_sessions)
    secret_hash = b"s" * 32
    client = StoredClient(
        uuid4(), "private-client", "d" * 16, secret_hash, "active", None, NOW
    )

    with pytest.raises(ClientPersistenceError) as error:
        repository.save_client(client, frozenset({"omf", "private-source"}))

    assert "private" not in str(error.value)
    assert secret_hash.hex() not in repr(error.value)
    with auth_sessions() as database_session:
        assert database_session.scalar(select(func.count()).select_from(ApiClient)) == 0


def test_grant_constraints_rollback_and_caller_transaction_recheck(
    auth_sessions: sessionmaker[Session],
) -> None:
    _seed_sources(auth_sessions, "omf")
    repository, admin, _access = _services(auth_sessions)
    issued = admin.create_client("agent", {"omf"})

    with auth_sessions.begin() as database_session:
        assert (
            has_source_grant_in_session(database_session, issued.client_id, "omf")
            is True
        )
        assert (
            has_source_grant_in_session(database_session, issued.client_id, "missing")
            is False
        )
    with pytest.raises(IntegrityError), auth_sessions.begin() as database_session:
        source_id = database_session.scalar(
            select(SourceProfile.id).where(SourceProfile.source_key == "omf")
        )
        database_session.add(
            ClientSourceGrant(
                client_id=issued.client_id,
                source_profile_id=source_id,
            )
        )
    with pytest.raises(IntegrityError), auth_sessions.begin() as database_session:
        source_id = database_session.scalar(
            select(SourceProfile.id).where(SourceProfile.source_key == "omf")
        )
        database_session.add(
            ClientSourceGrant(client_id=uuid4(), source_profile_id=source_id)
        )

    assert repository.has_source_grant(issued.client_id, "omf") is True


def test_caller_transaction_lock_blocks_same_client_revoke_only_until_commit(
    auth_sessions: sessionmaker[Session],
) -> None:
    """FOR SHARE protects the authorization decision from a revoke TOCTOU race."""
    _seed_sources(auth_sessions, "omf")
    repository = PostgresClientRepository(auth_sessions)
    random_bytes = _RandomBytes()
    admin = ClientAdminService(repository, random_bytes=random_bytes, clock=lambda: NOW)
    locked = admin.create_client("locked", {"omf"})
    other = admin.create_client("other", {"omf"})
    session_a = auth_sessions()
    transaction_a = session_a.begin()
    executor = ThreadPoolExecutor(max_workers=1)
    blocked_future = None

    try:
        assert has_source_grant_in_session(session_a, locked.client_id, "omf") is True

        other_started = Event()
        other_future = executor.submit(
            _revoke_with_lock_timeout, auth_sessions, other.key_id, other_started
        )
        assert other_started.wait(timeout=1)
        assert other_future.result(timeout=2) is True

        locked_started = Event()
        blocked_future = executor.submit(
            _revoke_with_lock_timeout, auth_sessions, locked.key_id, locked_started
        )
        assert locked_started.wait(timeout=1)
        with pytest.raises(FutureTimeoutError):
            blocked_future.result(timeout=0.2)

        transaction_a.commit()
        assert blocked_future.result(timeout=2) is True
    finally:
        if transaction_a.is_active:
            transaction_a.rollback()
        session_a.close()
        executor.shutdown(wait=True, cancel_futures=True)

    assert repository.has_source_grant(locked.client_id, "omf") is False
    assert repository.has_source_grant(other.client_id, "omf") is False
