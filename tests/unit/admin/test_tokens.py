"""Unit tests for API client secrets, authorization, and query HMAC."""

import base64
import hashlib
import hmac
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from omf_retrieval.application.admin.service import (
    ClientAccessService,
    ClientAdminService,
)
from omf_retrieval.application.admin.tokens import (
    AuthenticationError,
    AuthorizedSource,
    ClientView,
    KeyIdCollision,
    SourceAccessError,
    StoredClient,
    TokenIssuanceError,
    audit_query_hmac,
    parse_token,
)

NOW = datetime(2026, 8, 24, tzinfo=UTC)


class FakeRepository:
    def __init__(self) -> None:
        self.clients: dict[str, StoredClient] = {}
        self.grants: dict[str, set[str]] = {}
        self.occupied_key_ids: set[str] = set()
        self.save_attempts: list[StoredClient] = []

    @property
    def saved(self) -> StoredClient | None:
        return next(reversed(self.clients.values()), None)

    @saved.setter
    def saved(self, client: StoredClient) -> None:
        self.clients[client.key_id] = client

    def save_client(self, client: StoredClient, source_keys: frozenset[str]) -> None:
        self.save_attempts.append(client)
        if client.key_id in self.occupied_key_ids:
            raise KeyIdCollision
        self.occupied_key_ids.add(client.key_id)
        self.clients[client.key_id] = client
        self.grants[client.key_id] = set(source_keys)

    def list_clients(self) -> tuple[ClientView, ...]:
        return tuple(
            ClientView(
                client.id,
                client.name,
                client.key_id,
                client.status,
                frozenset(self.grants[client.key_id]),
            )
            for client in self.clients.values()
        )

    def revoke_client(self, key_id: str) -> bool:
        if key_id not in self.clients:
            return False
        self.clients[key_id] = replace(self.clients[key_id], status="revoked")
        return True

    def find_client_by_key_id(self, key_id: str) -> StoredClient | None:
        return self.clients.get(key_id)

    def has_source_grant(self, client_id: UUID, source_key: str) -> bool:
        return any(
            client.id == client_id and source_key in self.grants[key_id]
            for key_id, client in self.clients.items()
        )


class TruthyGrant:
    def __bool__(self) -> bool:
        return True


class EqualToTrueGrant(TruthyGrant):
    def __eq__(self, other: object) -> bool:
        return other is True


def fixed_random(size: int) -> bytes:
    return {8: bytes.fromhex("0123456789abcdef"), 32: bytes(range(32))}[size]


class SequenceRandom:
    """Return deterministic, distinct key/secret pairs for retry tests."""

    def __init__(self) -> None:
        self._values = iter(
            value
            for attempt in range(1, 5)
            for value in (
                attempt.to_bytes(8, "big"),
                bytes([attempt]) * 32,
            )
        )
        self.calls: list[int] = []

    def __call__(self, size: int) -> bytes:
        value = next(self._values)
        assert len(value) == size
        self.calls.append(size)
        return value


def services() -> tuple[FakeRepository, ClientAdminService, ClientAccessService]:
    repository = FakeRepository()
    return (
        repository,
        ClientAdminService(repository, random_bytes=fixed_random, clock=lambda: NOW),
        ClientAccessService(repository, clock=lambda: NOW),
    )


def test_issued_token_is_shown_once_and_only_hash_is_persisted() -> None:
    repository, admin, _access = services()

    issued = admin.create_client("agent-a", {"omf"})

    assert issued.token == (
        "omfr_0123456789abcdef."
        + base64.urlsafe_b64encode(bytes(range(32))).rstrip(b"=").decode("ascii")
    )
    assert repository.saved is not None
    assert repository.saved.token_hash == hashlib.sha256(issued.secret).digest()
    assert issued.secret not in repr(repository.saved).encode()
    assert issued.token not in repr(issued)
    assert issued.secret.hex() not in repr(issued)


def test_valid_token_authorizes_granted_source_before_search() -> None:
    _repository, admin, access = services()
    issued = admin.create_client("agent-a", {"omf"})
    calls = []

    result = access.execute_authorized(
        issued.token, "omf", lambda context: calls.append(context) or "ok"
    )

    assert result == "ok"
    assert len(calls) == 1
    assert type(calls[0]) is AuthorizedSource
    assert getattr(calls[0], "client", None) == access.authenticate(issued.token)
    assert getattr(calls[0], "source_key", None) == "omf"


def test_authorized_source_context_is_immutable() -> None:
    _repository, admin, access = services()
    issued = admin.create_client("agent-a", {"omf"})

    def mutate_source(context: object) -> None:
        context.source_key = "other"  # type: ignore[attr-defined]

    with pytest.raises((AttributeError, TypeError)):
        access.execute_authorized(issued.token, "omf", mutate_source)


def test_authorized_operation_exception_propagates_after_exact_context_delivery() -> (
    None
):
    _repository, admin, access = services()
    issued = admin.create_client("agent-a", {"omf"})
    seen = []
    expected = RuntimeError("search failed")

    def fail(context: object) -> None:
        seen.append(context)
        raise expected

    with pytest.raises(RuntimeError) as error:
        access.execute_authorized(issued.token, "omf", fail)

    assert error.value is expected
    assert len(seen) == 1
    assert seen[0].source_key == "omf"


@pytest.mark.parametrize(
    "variant",
    ["malformed", "unknown", "wrong-secret", "disabled", "revoked", "expired"],
)
def test_all_invalid_token_states_expose_the_same_401(variant: str) -> None:
    repository, admin, access = services()
    issued = admin.create_client("agent-a", {"omf"})
    assert repository.saved is not None
    token = issued.token
    if variant == "malformed":
        token = "not-a-token"
    elif variant == "unknown":
        token = "omfr_ffffffffffffffff." + token.split(".", 1)[1]
    elif variant == "wrong-secret":
        encoded = base64.urlsafe_b64encode(bytes([255]) * 32).rstrip(b"=").decode()
        token = token.split(".", 1)[0] + "." + encoded
    elif variant == "expired":
        repository.saved = replace(
            repository.saved, expires_at=NOW - timedelta(seconds=1)
        )
    else:
        repository.saved = replace(repository.saved, status=variant)

    with pytest.raises(AuthenticationError) as error:
        access.execute_authorized(token, "omf", lambda _client: pytest.fail("searched"))

    assert (error.value.status_code, error.value.code, str(error.value)) == (
        401,
        "invalid_token",
        "Authentication failed.",
    )


@pytest.mark.parametrize("source_key", ["other-existing", "missing"])
def test_missing_and_unauthorized_source_share_one_403_and_never_search(
    source_key: str,
) -> None:
    _repository, admin, access = services()
    issued = admin.create_client("agent-a", {"omf"})

    with pytest.raises(SourceAccessError) as error:
        access.execute_authorized(
            issued.token, source_key, lambda _client: pytest.fail("searched")
        )

    assert (error.value.status_code, error.value.code, str(error.value)) == (
        403,
        "source_access_denied",
        "Source access denied.",
    )


@pytest.mark.parametrize(
    "grant_result",
    [False, None, 0, 1, "granted", object(), TruthyGrant(), EqualToTrueGrant()],
    ids=[
        "false",
        "none",
        "zero",
        "one",
        "string",
        "object",
        "truthy",
        "equal-to-true",
    ],
)
def test_only_exact_true_grant_allows_operation(grant_result: object) -> None:
    repository, admin, access = services()
    issued = admin.create_client("agent-a", {"omf"})
    calls = 0

    def return_grant(_client_id: UUID, _source_key: str) -> object:
        return grant_result

    def operation(_context: object) -> None:
        nonlocal calls
        calls += 1

    repository.has_source_grant = return_grant  # type: ignore[method-assign]

    with pytest.raises(SourceAccessError) as error:
        access.execute_authorized(issued.token, "omf", operation)

    assert (error.value.status_code, error.value.code, str(error.value)) == (
        403,
        "source_access_denied",
        "Source access denied.",
    )
    assert calls == 0


def test_grant_repository_error_propagates_and_never_invokes_operation() -> None:
    repository, admin, access = services()
    issued = admin.create_client("agent-a", {"omf"})
    failure = RuntimeError("grant lookup failed")
    calls = 0

    def fail_grant(_client_id: UUID, _source_key: str) -> bool:
        raise failure

    def operation(_context: object) -> None:
        nonlocal calls
        calls += 1

    repository.has_source_grant = fail_grant  # type: ignore[method-assign]

    with pytest.raises(RuntimeError) as error:
        access.execute_authorized(issued.token, "omf", operation)

    assert error.value is failure
    assert calls == 0


def test_list_and_revoke_do_not_reveal_credentials_and_allow_parallel_tokens() -> None:
    repository = FakeRepository()
    counter = 0

    def unique_random(size: int) -> bytes:
        nonlocal counter
        counter += 1
        return bytes([counter]) * size

    admin = ClientAdminService(
        repository, random_bytes=unique_random, clock=lambda: NOW
    )
    first = admin.create_client("agent-a", {"omf"})
    second = admin.create_client("agent-b", {"omf"})
    listed = admin.list_clients()

    assert all(
        "token" not in repr(client) and "secret" not in repr(client)
        for client in listed
    )
    assert second.key_id in {client.key_id for client in listed}
    admin.revoke_client(second.key_id)
    assert repository.clients[second.key_id].status == "revoked"
    assert repository.clients[first.key_id].status == "active"
    assert first.token != second.token


def test_query_hmac_uses_exact_utf8_without_normalization() -> None:
    key = b"audit-key"
    composed = "caf\u00e9  질문\n"
    decomposed = "cafe\u0301  질문\n"

    assert (
        audit_query_hmac(key, composed)
        == hmac.new(key, composed.encode("utf-8"), hashlib.sha256).digest()
    )
    assert audit_query_hmac(key, composed) != audit_query_hmac(key, decomposed)


@pytest.mark.parametrize("query", ["", "  \t\n", "질문 🌱"])
def test_query_hmac_allows_exact_empty_whitespace_and_unicode_queries(
    query: str,
) -> None:
    key = b"k"

    assert (
        audit_query_hmac(key, query)
        == hmac.new(key, query.encode("utf-8"), hashlib.sha256).digest()
    )


@pytest.mark.parametrize(
    ("key", "query"),
    [
        (b"", "q"),
        (bytearray(b"key"), "q"),
        (memoryview(b"key"), "q"),
        ("key", "q"),
        (None, "q"),
        (b"key", b"q"),
    ],
)
def test_query_hmac_rejects_wrong_types(key: object, query: object) -> None:
    with pytest.raises(ValueError) as error:
        audit_query_hmac(key, query)  # type: ignore[arg-type]

    assert str(error.value) == "Audit HMAC inputs are invalid."
    assert "key" not in repr(error.value).lower()
    assert "query" not in repr(error.value).lower()


def test_two_key_id_collisions_create_fresh_credentials_then_succeed() -> None:
    repository = FakeRepository()
    repository.occupied_key_ids.update({"0000000000000001", "0000000000000002"})
    random_bytes = SequenceRandom()
    admin = ClientAdminService(repository, random_bytes=random_bytes, clock=lambda: NOW)

    issued = admin.create_client("agent-a", {"omf"})

    attempts = repository.save_attempts
    assert [client.key_id for client in attempts] == [
        "0000000000000001",
        "0000000000000002",
        "0000000000000003",
    ]
    assert len({client.token_hash for client in attempts}) == 3
    assert len({client.id for client in attempts}) == 3
    assert random_bytes.calls == [8, 32, 8, 32, 8, 32]
    assert issued.key_id == attempts[2].key_id
    assert issued.client_id == attempts[2].id
    assert issued.token == (
        "omfr_0000000000000003."
        + base64.urlsafe_b64encode(bytes([3]) * 32).rstrip(b"=").decode("ascii")
    )
    assert repository.saved == attempts[2]


def test_three_key_id_collisions_raise_one_safe_error() -> None:
    repository = FakeRepository()
    repository.occupied_key_ids.update(
        {
            "0000000000000001",
            "0000000000000002",
            "0000000000000003",
        }
    )
    random_bytes = SequenceRandom()
    admin = ClientAdminService(repository, random_bytes=random_bytes, clock=lambda: NOW)

    with pytest.raises(TokenIssuanceError) as error:
        admin.create_client("agent-a", {"omf"})

    assert str(error.value) == "Client token could not be issued."
    assert "omfr_" not in repr(error.value)
    assert [client.key_id for client in repository.save_attempts] == [
        "0000000000000001",
        "0000000000000002",
        "0000000000000003",
    ]
    assert random_bytes.calls == [8, 32, 8, 32, 8, 32]
    assert repository.clients == {}


@pytest.mark.parametrize("initial_status", ["active", "disabled", "revoked"])
def test_revoke_known_statuses_are_idempotent_and_return_true(
    initial_status: str,
) -> None:
    repository, admin, _access = services()
    issued = admin.create_client("agent-a", {"omf"})
    assert repository.saved is not None
    repository.saved = replace(repository.saved, status=initial_status)

    assert admin.revoke_client(issued.key_id) is True
    assert admin.revoke_client(issued.key_id) is True

    assert repository.saved is not None and repository.saved.status == "revoked"


def test_revoke_unknown_key_id_returns_false_without_creating_state() -> None:
    repository, admin, _access = services()

    assert admin.revoke_client("ffffffffffffffff") is False
    assert repository.clients == {}


@pytest.mark.parametrize(
    "malformed",
    [
        "{valid}=",
        "{noncanonical}",
        "omfr_0123456789ABCDEF.{secret}",
        "omfr_0123456789abcdef.{short}",
        "omfr_0123456789abcdef.{standard_slash}",
        "omfr_0123456789abcdef.{standard_plus}",
        " {valid}",
        "omfr_0123456789abcé.{secret}",
    ],
)
def test_token_parser_strictly_rejects_noncanonical_boundaries(malformed: str) -> None:
    _repository, admin, _access = services()
    valid = admin.create_client("agent-a", {"omf"}).token
    secret = valid.split(".", 1)[1]
    values = {
        "valid": valid,
        "secret": secret,
        "noncanonical": valid[:-1] + ("9" if valid[-1] == "8" else "B"),
        "short": base64.urlsafe_b64encode(bytes(31)).rstrip(b"=").decode(),
        "standard_slash": base64.b64encode(bytes([255]) * 32).rstrip(b"=").decode(),
        "standard_plus": base64.b64encode(bytes([251]) * 32).rstrip(b"=").decode(),
    }

    with pytest.raises(AuthenticationError):
        parse_token(malformed.format(**values))
