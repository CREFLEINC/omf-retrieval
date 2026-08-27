"""Secret-safe API client credential and audit-HMAC contracts."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

_TOKEN_PATTERN = re.compile(r"omfr_([0-9a-f]{16})\.([A-Za-z0-9_-]{43})", re.ASCII)


@dataclass(frozen=True, slots=True)
class StoredClient:
    """Persistence DTO that never includes a recoverable token secret."""

    id: UUID
    name: str
    key_id: str
    token_hash: bytes = field(repr=False)
    status: str
    expires_at: datetime | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class IssuedClient:
    """One-time credential result whose representation redacts raw material."""

    client_id: UUID
    name: str
    key_id: str
    token: str = field(repr=False)
    secret: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class ClientView:
    """Safe administrative view with no token or token hash."""

    client_id: UUID
    name: str
    key_id: str
    status: str
    source_keys: frozenset[str]


@dataclass(frozen=True, slots=True)
class AuthenticatedClient:
    """Safe caller identity passed across the authorization boundary."""

    client_id: UUID
    name: str
    key_id: str


@dataclass(frozen=True, slots=True)
class AuthorizedSource:
    """Immutable identity and exact source proven by the access boundary."""

    client: AuthenticatedClient
    source_key: str


class AuthenticationError(ValueError):
    """Expose one stable 401 contract for every credential failure."""

    status_code = 401
    code = "invalid_token"

    def __init__(self) -> None:
        super().__init__("Authentication failed.")


class SourceAccessError(ValueError):
    """Expose one stable 403 contract for missing and unauthorized sources."""

    status_code = 403
    code = "source_access_denied"

    def __init__(self) -> None:
        super().__init__("Source access denied.")


class ClientAdminError(ValueError):
    """Report a safe administrative validation or lookup failure."""


class KeyIdCollision(RuntimeError):
    """Signal the repository's explicit unique key-ID conflict."""


class TokenIssuanceError(ClientAdminError):
    """Report exhausted key-ID retries without credential material."""


class AuditHmacConfigurationError(ClientAdminError):
    """Reject invalid audit-HMAC inputs without exposing their values."""


def issue_token(random_bytes: object) -> tuple[str, str, bytes, bytes]:
    """Create one 16-character key ID and one 256-bit secret."""
    if not callable(random_bytes):
        raise ClientAdminError("A cryptographic random-byte provider is required.")
    key_bytes = random_bytes(8)
    secret = random_bytes(32)
    if type(key_bytes) is not bytes or len(key_bytes) != 8:
        raise ClientAdminError("The key ID entropy must be exactly 8 bytes.")
    if type(secret) is not bytes or len(secret) != 32:
        raise ClientAdminError("The token secret must be exactly 32 bytes.")
    key_id = key_bytes.hex()
    encoded = base64.urlsafe_b64encode(secret).rstrip(b"=").decode("ascii")
    return key_id, f"omfr_{key_id}.{encoded}", secret, hashlib.sha256(secret).digest()


def parse_token(token: object) -> tuple[str, bytes]:
    """Parse only the canonical token form without echoing rejected input."""
    if type(token) is not str or (match := _TOKEN_PATTERN.fullmatch(token)) is None:
        raise AuthenticationError
    key_id, encoded = match.groups()
    try:
        secret = base64.b64decode(encoded + "=", altchars=b"-_", validate=True)
    except (binascii.Error, ValueError):
        raise AuthenticationError from None
    canonical = base64.urlsafe_b64encode(secret).rstrip(b"=").decode("ascii")
    if len(secret) != 32 or canonical != encoded:
        raise AuthenticationError
    return key_id, secret


def audit_query_hmac(key: bytes, query: str) -> bytes:
    """Hash the exact, unnormalized UTF-8 query with HMAC-SHA256."""
    if type(key) is not bytes or not key or type(query) is not str:
        raise AuditHmacConfigurationError("Audit HMAC inputs are invalid.")
    return hmac.new(key, query.encode("utf-8"), hashlib.sha256).digest()
