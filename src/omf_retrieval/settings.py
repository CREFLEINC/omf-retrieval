"""Validated runtime settings for retrieval services."""

import os
import stat
from collections.abc import Callable
from dataclasses import dataclass, field
from math import isfinite
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import (
    Field,
    PrivateAttr,
    SecretStr,
    StrictFloat,
    StrictInt,
    field_validator,
    model_validator,
)
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

_SettingsSource = PydanticBaseSettingsSource | Callable[[], dict[str, Any]]
_AUDIT_KEY_READ_CHUNK_BYTES = 64 * 1024
_MAX_AUDIT_HMAC_KEY_BYTES = 65_536
DEFAULT_AUDIT_HMAC_KEY_FILE = Path("/opt/omf-retrieval/secrets/audit_hmac_key")
MVP_KEYWORD_CANDIDATE_LIMIT = 50
MVP_VECTOR_CANDIDATE_LIMIT = 50
MVP_RRF_K = 60
MVP_KEYWORD_WEIGHT = 1.0
MVP_VECTOR_WEIGHT = 1.0
MVP_KEYWORD_SIMILARITY_FLOOR = 0.03658536400000001
MVP_VECTOR_SIMILARITY_FLOOR = 0.48344050397156374


class AuditHmacKeyFileError(ValueError):
    """Report one safe audit-key readiness failure without file contents."""

    code = "audit_hmac_key_unavailable"

    def __init__(self) -> None:
        super().__init__("Audit HMAC key file is unavailable.")


@dataclass(frozen=True, slots=True)
class AuditHmacKey:
    """Hold byte-exact audit HMAC key material with a redacted representation."""

    _value: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if type(self._value) is not bytes or not self._value:
            raise AuditHmacKeyFileError

    def as_bytes(self) -> bytes:
        """Return the same immutable, unnormalized key bytes."""
        return self._value


@dataclass(frozen=True, slots=True)
class _AuditKeyFileIdentity:
    """Bind cached bytes to one validated open-file identity."""

    device: int
    inode: int
    mode: int
    size: int
    modified_ns: int
    changed_ns: int


@dataclass(frozen=True, slots=True)
class _LoadedAuditHmacKey:
    """Carry one key with its lexical coordinate and open-file identity."""

    key: AuditHmacKey
    path: Path
    identity: _AuditKeyFileIdentity


class Settings(BaseSettings):
    """Provide validated configuration from ``OMF_RETRIEVAL_`` variables.

    Secret files are publicly represented only by paths. Production validation
    loads the audit HMAC key into a private redacted wrapper so configuration
    representations cannot expose its contents.

    Raises:
        ValueError: If retrieval limits or production safeguards are invalid.
    """

    model_config = SettingsConfigDict(
        env_prefix="OMF_RETRIEVAL_",
        extra="forbid",
        hide_input_in_errors=True,
    )

    environment: Literal["development", "test", "production"] = "development"
    embedding_model_name: str = "Qwen/Qwen3-Embedding-0.6B"
    embedding_model_revision: str = "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
    embedding_dimension: int = 1024
    embedding_device: str = "cpu"
    embedding_cache_dir: Path | None = None
    embedding_batch_size: StrictInt = Field(default=32, ge=1, le=256)
    query_instruction: str = (
        "Instruct: Retrieve passages from Korean internal software design documents "
        "that provide the requirements, policies, API definitions, data models, or "
        "decisions needed to answer the query.\nQuery: {query}"
    )
    keyword_candidate_limit: int = 50
    vector_candidate_limit: int = 50
    rrf_k: int = 60
    keyword_weight: float = 1.0
    vector_weight: float = 1.0
    keyword_similarity_floor: StrictFloat = MVP_KEYWORD_SIMILARITY_FLOOR
    vector_similarity_floor: StrictFloat = MVP_VECTOR_SIMILARITY_FLOOR
    evidence_floor_status: Literal["calibration_pending", "calibrated"] = "calibrated"
    search_default_limit: int = 5
    search_max_limit: int = 20
    parent_context_max_tokens: int = 1200
    postgres_password_file: Path | None = None
    audit_hmac_key_file: Path | None = DEFAULT_AUDIT_HMAC_KEY_FILE
    api_token: SecretStr | None = Field(default=None, repr=False)
    _audit_hmac_key: AuditHmacKey | None = PrivateAttr(default=None)
    _audit_hmac_key_path: Path | None = PrivateAttr(default=None)
    _audit_hmac_key_identity: _AuditKeyFileIdentity | None = PrivateAttr(default=None)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[_SettingsSource, ...]:
        """Normalize safe batch values before Pydantic builds validation errors.

        Args:
            settings_cls: Settings model type supplied by Pydantic Settings.
            init_settings: Direct constructor-value source.
            env_settings: Prefixed operating-system environment source.
            dotenv_settings: Optional dotenv source retained in default order.
            file_secret_settings: Optional secret-directory source.

        Returns:
            Default-priority sources with safe direct input and env batch parsing.
        """

        def embedding_init_settings() -> dict[str, Any]:
            values = init_settings()
            if (
                "embedding_batch_size" in values
                and type(values["embedding_batch_size"]) is not int
            ):
                values["embedding_batch_size"] = None
            for field_name in (
                "keyword_similarity_floor",
                "vector_similarity_floor",
            ):
                if field_name in values and type(values[field_name]) is not float:
                    values[field_name] = None
            return values

        def embedding_environment_settings() -> dict[str, Any]:
            values = env_settings()
            batch_size = values.get("embedding_batch_size")
            if (
                isinstance(batch_size, str)
                and batch_size.isascii()
                and batch_size.isdecimal()
            ):
                values["embedding_batch_size"] = int(batch_size)
            for field_name in (
                "keyword_similarity_floor",
                "vector_similarity_floor",
            ):
                floor = values.get(field_name)
                if isinstance(floor, str):
                    try:
                        values[field_name] = float(floor)
                    except ValueError:
                        values[field_name] = None
            return values

        return (
            embedding_init_settings,
            embedding_environment_settings,
            dotenv_settings,
            file_secret_settings,
        )

    @field_validator("embedding_batch_size", mode="before")
    @classmethod
    def require_exact_embedding_batch_size(cls, value: object) -> int:
        """Reject values that are not exact built-in integers.

        Args:
            value: Materialized setting value from the selected settings source.

        Returns:
            The exact built-in integer supplied at the settings boundary.

        Raises:
            ValueError: If the value is a coercible type or an int subclass.
        """
        if type(value) is not int:
            raise ValueError("embedding_batch_size must be an exact integer")
        return value

    @field_validator(
        "keyword_similarity_floor",
        "vector_similarity_floor",
        mode="before",
    )
    @classmethod
    def require_exact_evidence_floor(cls, value: object) -> float:
        """Reject direct values that are not exact built-in floats."""
        if type(value) is not float:
            raise ValueError("evidence floors must be exact floats")
        return value

    @model_validator(mode="after")
    def validate_runtime_contract(self) -> Self:
        """Validate retrieval bounds and production-only safeguards.

        Returns:
            This validated settings object.

        Raises:
            ValueError: If a bound, device, or required secret path is invalid.
        """
        self._require_positive_limits()
        self._require_fixed_mvp_search_policy()
        self._require_valid_evidence_floors()
        if self.search_default_limit > self.search_max_limit:
            raise ValueError("search_default_limit must not exceed search_max_limit")
        if self.environment == "production":
            self._require_production_safeguards()
        return self

    def _require_fixed_mvp_search_policy(self) -> None:
        """Reject configuration that would silently change approved retrieval."""
        if (
            self.keyword_candidate_limit != MVP_KEYWORD_CANDIDATE_LIMIT
            or self.vector_candidate_limit != MVP_VECTOR_CANDIDATE_LIMIT
            or self.rrf_k != MVP_RRF_K
            or self.keyword_weight != MVP_KEYWORD_WEIGHT
            or self.vector_weight != MVP_VECTOR_WEIGHT
        ):
            raise ValueError("MVP search policy is fixed")

    def _require_positive_limits(self) -> None:
        """Require every approved dimension and limit to be positive."""
        values = {
            "embedding_dimension": self.embedding_dimension,
            "keyword_candidate_limit": self.keyword_candidate_limit,
            "vector_candidate_limit": self.vector_candidate_limit,
            "rrf_k": self.rrf_k,
            "search_default_limit": self.search_default_limit,
            "search_max_limit": self.search_max_limit,
            "parent_context_max_tokens": self.parent_context_max_tokens,
        }
        for field_name, value in values.items():
            if value <= 0:
                raise ValueError(f"{field_name} must be positive")

    def _require_valid_evidence_floors(self) -> None:
        """Require finite raw-similarity floors in the approved closed range."""
        for field_name, value in (
            ("keyword_similarity_floor", self.keyword_similarity_floor),
            ("vector_similarity_floor", self.vector_similarity_floor),
        ):
            if not isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{field_name} must be finite and between 0 and 1")

    def _require_production_safeguards(self) -> None:
        """Require the approved GPU, cache path, and existing secret files."""
        if self.embedding_device != "cuda:0":
            raise ValueError("production embedding_device must be cuda:0")
        if self.embedding_cache_dir is None:
            raise ValueError("production embedding_cache_dir must be explicit")
        if (
            self.postgres_password_file is None
            or not self.postgres_password_file.is_file()
        ):
            raise ValueError("production postgres_password_file must be a regular file")
        self._cache_audit_hmac_key(_read_audit_hmac_key(self.audit_hmac_key_file))

    def load_audit_hmac_key(self) -> AuditHmacKey:
        """Return validated audit key material bound to the configured file identity.

        The file is interpreted as opaque bytes. In particular, terminal LF and
        CRLF bytes are part of the HMAC key. Repeated access revalidates the path
        identity without copying the secret and reloads a valid replacement.

        Returns:
            A redacted wrapper around the exact immutable file bytes.

        Raises:
            AuditHmacKeyFileError: If the configured path is not a safe, non-empty
                POSIX regular file with exact mode ``0600``.
        """
        configured_path = _canonical_audit_key_path(self.audit_hmac_key_file)
        if (
            self._audit_hmac_key is not None
            and self._audit_hmac_key_path == configured_path
            and self._audit_hmac_key_identity is not None
            and _read_audit_key_identity(configured_path)
            == self._audit_hmac_key_identity
        ):
            return self._audit_hmac_key
        self._cache_audit_hmac_key(_read_audit_hmac_key(configured_path))
        assert self._audit_hmac_key is not None
        return self._audit_hmac_key

    def _cache_audit_hmac_key(self, loaded: _LoadedAuditHmacKey) -> None:
        """Update all private cache coordinates as one logical operation."""
        self._audit_hmac_key = loaded.key
        self._audit_hmac_key_path = loaded.path
        self._audit_hmac_key_identity = loaded.identity


def _canonical_audit_key_path(path: object) -> Path:
    """Normalize lexical aliases without following a final symlink."""
    if not isinstance(path, Path):
        raise AuditHmacKeyFileError
    try:
        return Path(os.path.abspath(os.fspath(path)))
    except (OSError, TypeError, ValueError):
        raise AuditHmacKeyFileError from None


def _read_audit_hmac_key(path: Path | None) -> _LoadedAuditHmacKey:
    """Open, validate, and read one byte-exact POSIX secret without path races."""
    canonical_path = _canonical_audit_key_path(path)
    if not hasattr(os, "O_NOFOLLOW"):
        raise AuditHmacKeyFileError
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | os.O_NOFOLLOW
    try:
        descriptor = os.open(canonical_path, flags)
    except OSError:
        raise AuditHmacKeyFileError from None
    confirmation_descriptor = -1
    try:
        before = os.fstat(descriptor)
        before_identity = _validated_audit_key_identity(before)
        remaining = _MAX_AUDIT_HMAC_KEY_BYTES + 1
        chunks: list[bytes] = []
        while remaining > 0:
            chunk = os.read(
                descriptor,
                min(_AUDIT_KEY_READ_CHUNK_BYTES, remaining),
            )
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        value = b"".join(chunks)
        after = os.fstat(descriptor)
        after_identity = _validated_audit_key_identity(after)
        first_read_is_stable = (
            len(value) == before.st_size and before_identity == after_identity
        )
        try:
            confirmation_descriptor = os.open(canonical_path, flags)
        except OSError:
            raise AuditHmacKeyFileError from None
        confirmed_identity = _validated_audit_key_identity(
            os.fstat(confirmation_descriptor)
        )
        if not first_read_is_stable or confirmed_identity != after_identity:
            raise AuditHmacKeyFileError
        return _LoadedAuditHmacKey(
            AuditHmacKey(value), canonical_path, confirmed_identity
        )
    except AuditHmacKeyFileError:
        raise
    except OSError:
        raise AuditHmacKeyFileError from None
    finally:
        if confirmation_descriptor >= 0:
            try:
                os.close(confirmation_descriptor)
            except OSError:
                pass
        try:
            os.close(descriptor)
        except OSError:
            pass


def _read_audit_key_identity(path: Path) -> _AuditKeyFileIdentity:
    """Revalidate a cached key's current path without copying secret bytes."""
    if not hasattr(os, "O_NOFOLLOW"):
        raise AuditHmacKeyFileError
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise AuditHmacKeyFileError from None
    try:
        return _validated_audit_key_identity(os.fstat(descriptor))
    except AuditHmacKeyFileError:
        raise
    except OSError:
        raise AuditHmacKeyFileError from None
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass


def _validated_audit_key_identity(metadata: os.stat_result) -> _AuditKeyFileIdentity:
    """Return safe metadata only for an approved audit-key file."""
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or not 0 < metadata.st_size <= _MAX_AUDIT_HMAC_KEY_BYTES
    ):
        raise AuditHmacKeyFileError
    return _AuditKeyFileIdentity(
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )
