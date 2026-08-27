"""Application services for API client administration and source access."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol, TypeVar
from uuid import UUID, uuid4

from omf_retrieval.application.admin.tokens import (
    AuthenticatedClient,
    AuthenticationError,
    AuthorizedSource,
    ClientAdminError,
    ClientView,
    IssuedClient,
    KeyIdCollision,
    SourceAccessError,
    StoredClient,
    TokenIssuanceError,
    issue_token,
    parse_token,
)

T = TypeVar("T")


class ClientRepository(Protocol):
    """Persistence operations required by administration and authorization.

    ``has_source_grant`` is an application-boundary precheck. The Task 11 database
    search adapter must independently enforce the same client/source grant in its
    SQL transaction; receiving an ``AuthorizedSource`` does not replace that check.
    """

    def save_client(
        self, client: StoredClient, source_keys: frozenset[str]
    ) -> None: ...
    def list_clients(self) -> tuple[ClientView, ...]: ...
    def revoke_client(self, key_id: str) -> bool: ...
    def find_client_by_key_id(self, key_id: str) -> StoredClient | None: ...
    def has_source_grant(self, client_id: UUID, source_key: str) -> bool: ...


class ClientAdminService:
    """Provide only the approved create, list, and revoke operations."""

    def __init__(
        self,
        repository: ClientRepository,
        *,
        random_bytes: Callable[[int], bytes] = secrets.token_bytes,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._random_bytes = random_bytes
        self._clock = clock

    def create_client(self, name: str, source_keys: set[str]) -> IssuedClient:
        if type(name) is not str or not name.strip():
            raise ClientAdminError("Client name must be a non-blank string.")
        if type(source_keys) is not set or any(
            type(source) is not str or not source.strip() for source in source_keys
        ):
            raise ClientAdminError("Source grants must be an exact set of names.")
        now = _utc_time(self._clock())
        grants = frozenset(source_keys)
        for _attempt in range(3):
            key_id, token, secret, token_hash = issue_token(self._random_bytes)
            client_id = uuid4()
            try:
                self._repository.save_client(
                    StoredClient(
                        client_id,
                        name,
                        key_id,
                        token_hash,
                        "active",
                        None,
                        now,
                    ),
                    grants,
                )
            except KeyIdCollision:
                continue
            return IssuedClient(client_id, name, key_id, token, secret)
        raise TokenIssuanceError("Client token could not be issued.") from None

    def list_clients(self) -> tuple[ClientView, ...]:
        return self._repository.list_clients()

    def revoke_client(self, key_id: str) -> bool:
        if type(key_id) is not str:
            raise ClientAdminError("Client key ID must be a string.")
        return self._repository.revoke_client(key_id)


class ClientAccessService:
    """Authenticate, authorize, then invoke a search operation in that order."""

    def __init__(
        self,
        repository: ClientRepository,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._clock = clock

    def authenticate(self, token: str) -> AuthenticatedClient:
        try:
            key_id, secret = parse_token(token)
        except AuthenticationError:
            raise AuthenticationError from None
        stored = self._repository.find_client_by_key_id(key_id)
        expected = stored.token_hash if stored is not None else bytes(32)
        matched = hmac.compare_digest(hashlib.sha256(secret).digest(), expected)
        now = _utc_time(self._clock())
        if (
            stored is None
            or not matched
            or stored.status != "active"
            or (
                stored.expires_at is not None
                and _invalid_expiry(stored.expires_at, now)
            )
        ):
            raise AuthenticationError
        return AuthenticatedClient(stored.id, stored.name, stored.key_id)

    def execute_authorized(
        self,
        token: str,
        source_key: str,
        operation: Callable[[AuthorizedSource], T],
    ) -> T:
        client = self.authenticate(token)
        if type(source_key) is not str:
            raise SourceAccessError
        granted = self._repository.has_source_grant(client.client_id, source_key)
        # Fail closed if an adapter violates its bool return contract.
        if granted is not True:
            raise SourceAccessError
        return operation(AuthorizedSource(client, source_key))


def _utc_time(value: datetime) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Clock values must be timezone-aware datetimes.")
    return value.astimezone(UTC)


def _invalid_expiry(expires_at: datetime, now: datetime) -> bool:
    return (
        expires_at.tzinfo is None or expires_at.utcoffset() is None or expires_at <= now
    )
