"""PostgreSQL persistence for API clients and exact source grants."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql import Select

from omf_retrieval.application.admin.tokens import (
    ClientView,
    KeyIdCollision,
    StoredClient,
)
from omf_retrieval.infrastructure.database.models import (
    ApiClient,
    ClientSourceGrant,
    SourceProfile,
)

_KEY_ID_CONSTRAINT = "uq_api_clients_key_id"


class ClientPersistenceError(RuntimeError):
    """Report a safe persistence-contract failure without credential material."""


def source_grant_statement(client_id: UUID, source_key: str) -> Select[tuple[bool]]:
    """Build the locked grant predicate shared with transactional search.

    PostgreSQL updates of the non-key ``status`` column take ``FOR NO KEY
    UPDATE``. ``FOR SHARE`` is the least restrictive row lock that conflicts
    with it, so a successful authorization remains valid until this caller's
    transaction ends without unnecessarily taking ``FOR UPDATE``.
    """
    return (
        select(sa.literal(True))
        .select_from(ClientSourceGrant)
        .join(ApiClient, ApiClient.id == ClientSourceGrant.client_id)
        .join(SourceProfile, SourceProfile.id == ClientSourceGrant.source_profile_id)
        .where(
            ClientSourceGrant.client_id == client_id,
            ApiClient.id == client_id,
            ApiClient.status == "active",
            sa.or_(
                ApiClient.expires_at.is_(None), ApiClient.expires_at > sa.func.now()
            ),
            SourceProfile.source_key == source_key,
        )
        .limit(1)
        .with_for_update(read=True, of=ApiClient)
    )


def has_source_grant_in_session(
    database_session: Session,
    client_id: UUID,
    source_key: str,
) -> bool:
    """Recheck one exact grant inside a caller-owned search transaction."""
    if type(client_id) is not UUID or type(source_key) is not str:
        return False
    return (
        database_session.scalar(source_grant_statement(client_id, source_key)) is True
    )


class PostgresClientRepository:
    """Persist each administration or precheck operation in one transaction."""

    def __init__(self, transactions: sessionmaker[Session]) -> None:
        self._transactions = transactions

    def save_client(
        self,
        client: StoredClient,
        source_keys: frozenset[str],
    ) -> None:
        """Store only a digest and atomically create every requested grant."""
        _validate_client(client, source_keys)
        try:
            with self._transactions.begin() as database_session:
                source_ids = _resolve_source_ids(database_session, source_keys)
                database_session.add(
                    ApiClient(
                        id=client.id,
                        name=client.name,
                        key_id=client.key_id,
                        token_hash=client.token_hash,
                        status=client.status,
                        expires_at=_utc_or_none(client.expires_at),
                        created_at=_utc(client.created_at),
                    )
                )
                database_session.flush()
                database_session.add_all(
                    ClientSourceGrant(
                        client_id=client.id,
                        source_profile_id=source_ids[source_key],
                    )
                    for source_key in sorted(source_keys)
                )
                database_session.flush()
        except IntegrityError as error:
            if _constraint_name(error) == _KEY_ID_CONSTRAINT:
                raise KeyIdCollision from None
            raise

    def list_clients(self) -> tuple[ClientView, ...]:
        """List one unbounded, admin-only MVP snapshot in a set-based query.

        A later operational API must add pagination before exposing this list to
        an unbounded client population; Task 10 deliberately adds no such API.
        """
        statement = (
            select(
                ApiClient.id,
                ApiClient.name,
                ApiClient.key_id,
                ApiClient.status,
                SourceProfile.source_key,
            )
            .outerjoin(
                ClientSourceGrant,
                ClientSourceGrant.client_id == ApiClient.id,
            )
            .outerjoin(
                SourceProfile,
                SourceProfile.id == ClientSourceGrant.source_profile_id,
            )
            .order_by(ApiClient.created_at, ApiClient.id, SourceProfile.source_key)
        )
        with self._transactions.begin() as database_session:
            rows = database_session.execute(statement).all()

        clients: dict[UUID, tuple[str, str, str, set[str]]] = {}
        for client_id, name, key_id, status, source_key in rows:
            values = clients.setdefault(client_id, (name, key_id, status, set()))
            if source_key is not None:
                values[3].add(source_key)
        return tuple(
            ClientView(client_id, name, key_id, status, frozenset(source_keys))
            for client_id, (name, key_id, status, source_keys) in clients.items()
        )

    def revoke_client(self, key_id: str) -> bool:
        """Idempotently revoke any known active, disabled, or revoked client."""
        if type(key_id) is not str:
            return False
        statement = (
            sa.update(ApiClient)
            .where(ApiClient.key_id == key_id)
            .values(status="revoked")
            .returning(ApiClient.id)
        )
        with self._transactions.begin() as database_session:
            return database_session.scalar(statement) is not None

    def find_client_by_key_id(self, key_id: str) -> StoredClient | None:
        """Read the exact digest DTO required for constant-time authentication."""
        if type(key_id) is not str:
            return None
        statement = select(
            ApiClient.id,
            ApiClient.name,
            ApiClient.key_id,
            ApiClient.token_hash,
            ApiClient.status,
            ApiClient.expires_at,
            ApiClient.created_at,
        ).where(ApiClient.key_id == key_id)
        with self._transactions.begin() as database_session:
            row = database_session.execute(statement).one_or_none()
        if row is None:
            return None
        return StoredClient(
            id=row.id,
            name=row.name,
            key_id=row.key_id,
            token_hash=bytes(row.token_hash),
            status=row.status,
            expires_at=_utc_or_none(row.expires_at),
            created_at=_utc(row.created_at),
        )

    def has_source_grant(self, client_id: UUID, source_key: str) -> bool:
        """Fail closed unless one exact client/source grant exists."""
        with self._transactions.begin() as database_session:
            return has_source_grant_in_session(database_session, client_id, source_key)


def _validate_client(client: StoredClient, source_keys: frozenset[str]) -> None:
    if (
        type(client) is not StoredClient
        or type(client.token_hash) is not bytes
        or len(client.token_hash) != 32
        or type(source_keys) is not frozenset
        or any(type(key) is not str or not key.strip() for key in source_keys)
    ):
        raise ClientPersistenceError("Client persistence input is invalid.")
    _utc(client.created_at)
    _utc_or_none(client.expires_at)


def _resolve_source_ids(
    database_session: Session,
    source_keys: frozenset[str],
) -> dict[str, UUID]:
    if not source_keys:
        return {}
    rows = database_session.execute(
        select(SourceProfile.source_key, SourceProfile.id).where(
            SourceProfile.source_key.in_(source_keys)
        )
    ).all()
    resolved = dict(rows)
    if resolved.keys() != source_keys:
        raise ClientPersistenceError("Source grants could not be persisted.")
    return resolved


def _utc(value: datetime) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ClientPersistenceError("Client timestamps are invalid.")
    return value.astimezone(UTC)


def _utc_or_none(value: datetime | None) -> datetime | None:
    return None if value is None else _utc(value)


def _constraint_name(error: IntegrityError) -> str | None:
    diagnostics = getattr(error.orig, "diag", None)
    name = getattr(diagnostics, "constraint_name", None)
    return name if type(name) is str else None


__all__ = [
    "ClientPersistenceError",
    "PostgresClientRepository",
    "has_source_grant_in_session",
    "source_grant_statement",
]
