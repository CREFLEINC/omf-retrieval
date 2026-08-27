"""Unit tests for validated application settings."""

import copy
import hashlib
import hmac
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from omf_retrieval.application.admin.tokens import audit_query_hmac
from omf_retrieval.settings import Settings

DEFAULT_AUDIT_HMAC_KEY_FILE = Path("/opt/omf-retrieval/secrets/audit_hmac_key")
MAX_AUDIT_HMAC_KEY_BYTES = 65_536


def _write_secret(path: Path, content: bytes, *, mode: int = 0o600) -> Path:
    path.write_bytes(content)
    path.chmod(mode)
    return path


def _production_settings(
    tmp_path: Path,
    *,
    audit_hmac_key_file: Path,
) -> Settings:
    return Settings(
        environment="production",
        embedding_device="cuda:0",
        embedding_cache_dir=tmp_path / "model-cache",
        postgres_password_file=_write_secret(
            tmp_path / "postgres-password", b"postgres-password"
        ),
        audit_hmac_key_file=audit_hmac_key_file,
    )


def test_settings_expose_approved_retrieval_defaults() -> None:
    """Default settings retain the approved embedding and retrieval contract."""
    settings = Settings()

    assert settings.environment == "development"
    assert settings.embedding_model_name == "Qwen/Qwen3-Embedding-0.6B"
    assert (
        settings.embedding_model_revision == "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
    )
    assert settings.embedding_dimension == 1024
    assert settings.embedding_device == "cpu"
    assert settings.embedding_cache_dir is None
    assert settings.embedding_batch_size == 32
    assert settings.query_instruction == (
        "Instruct: Retrieve passages from Korean internal software design documents "
        "that provide the requirements, policies, API definitions, data models, or "
        "decisions needed to answer the query.\nQuery: {query}"
    )
    assert (
        settings.keyword_candidate_limit,
        settings.vector_candidate_limit,
        settings.rrf_k,
        settings.keyword_weight,
        settings.vector_weight,
        settings.search_default_limit,
        settings.search_max_limit,
        settings.parent_context_max_tokens,
    ) == (50, 50, 60, 1.0, 1.0, 5, 20, 1200)
    assert settings.audit_hmac_key_file == DEFAULT_AUDIT_HMAC_KEY_FILE


def test_settings_read_prefixed_environment_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The OMF_RETRIEVAL_ prefix supplies settings from the environment."""
    monkeypatch.setenv("OMF_RETRIEVAL_EMBEDDING_DEVICE", "cuda:0")
    monkeypatch.setenv("OMF_RETRIEVAL_EMBEDDING_BATCH_SIZE", "64")
    monkeypatch.setenv("OMF_RETRIEVAL_EMBEDDING_CACHE_DIR", "/srv/model-cache")
    monkeypatch.setenv(
        "OMF_RETRIEVAL_AUDIT_HMAC_KEY_FILE", "/run/secrets/audit_hmac_key"
    )

    settings = Settings()
    assert settings.embedding_device == "cuda:0"
    assert settings.embedding_batch_size == 64
    assert settings.embedding_cache_dir == Path("/srv/model-cache")
    assert settings.audit_hmac_key_file == Path("/run/secrets/audit_hmac_key")


@pytest.mark.parametrize("batch_size", [1, 32, 256])
def test_embedding_batch_size_accepts_approved_boundaries(batch_size: int) -> None:
    """The inclusive batch-size boundaries remain configurable."""
    configured = Settings(embedding_batch_size=batch_size).embedding_batch_size

    assert type(configured) is int
    assert configured == batch_size


@pytest.mark.parametrize("batch_size", [0, 257, True, 32.0, "32"])
def test_embedding_batch_size_rejects_out_of_range_or_non_exact_ints(
    batch_size: object,
) -> None:
    """Unsafe bounds and coercible non-integers cannot alter inference batching."""
    with pytest.raises(ValidationError):
        Settings(embedding_batch_size=batch_size)  # type: ignore[arg-type]


def test_embedding_batch_size_rejects_an_int_subclass() -> None:
    """An int subclass cannot cross the exact built-in integer boundary."""

    class IntSubclass(int):
        pass

    with pytest.raises(ValidationError):
        Settings(embedding_batch_size=IntSubclass(32))


def test_embedding_batch_size_error_hides_an_int_subclass_repr() -> None:
    """Rejected custom integer representations cannot leak through errors."""
    secret = "raw-batch-secret"

    class SensitiveIntSubclass(int):
        def __repr__(self) -> str:
            return secret

    with pytest.raises(ValidationError) as error_info:
        Settings(embedding_batch_size=SensitiveIntSubclass(32))

    assert secret not in str(error_info.value)
    assert secret not in repr(error_info.value.errors())


@pytest.mark.parametrize(
    ("raw_batch_size", "expected_batch_size"),
    [("1", 1), ("32", 32), ("064", 64), ("256", 256)],
)
def test_embedding_batch_size_accepts_decimal_environment_values(
    monkeypatch: pytest.MonkeyPatch,
    raw_batch_size: str,
    expected_batch_size: int,
) -> None:
    """ASCII decimal environment values become exact built-in integers."""
    monkeypatch.setenv("OMF_RETRIEVAL_EMBEDDING_BATCH_SIZE", raw_batch_size)

    configured = Settings().embedding_batch_size

    assert type(configured) is int
    assert configured == expected_batch_size


@pytest.mark.parametrize(
    "raw_batch_size",
    [" 32", "+32", "-1", "32.0", "３２", "true", "", "0", "257"],
)
def test_embedding_batch_size_rejects_invalid_environment_values(
    monkeypatch: pytest.MonkeyPatch,
    raw_batch_size: str,
) -> None:
    """Non-decimal and out-of-range environment values fail closed."""
    monkeypatch.setenv("OMF_RETRIEVAL_EMBEDDING_BATCH_SIZE", raw_batch_size)

    with pytest.raises(ValidationError):
        Settings()


def test_development_embedding_cache_accepts_none_or_an_explicit_path(
    tmp_path: Path,
) -> None:
    """Local development may use the HF default or an explicit cache location."""
    assert Settings(embedding_cache_dir=None).embedding_cache_dir is None
    assert (
        Settings(embedding_cache_dir=tmp_path / "model-cache").embedding_cache_dir
        == tmp_path / "model-cache"
    )


def test_production_requires_gpu_zero_and_secret_files(tmp_path: Path) -> None:
    """Production rejects a non-approved GPU device and missing secret files."""
    with pytest.raises(ValueError):
        Settings(
            environment="production",
            embedding_device="cpu",
            postgres_password_file=tmp_path / "missing-postgres",
            audit_hmac_key_file=tmp_path / "missing-audit",
        )


def test_production_accepts_gpu_zero_with_regular_secret_files(tmp_path: Path) -> None:
    """Production accepts exact GPU zero when both secret paths are regular files."""
    postgres_password_file = tmp_path / "postgres-password"
    audit_hmac_key_file = tmp_path / "audit-hmac-key"
    postgres_password_file.write_text("postgres-password", encoding="utf-8")
    audit_hmac_key_file.write_text("audit-hmac-key", encoding="utf-8")
    audit_hmac_key_file.chmod(0o600)

    settings = Settings(
        environment="production",
        embedding_device="cuda:0",
        embedding_cache_dir=tmp_path / "model-cache",
        postgres_password_file=postgres_password_file,
        audit_hmac_key_file=audit_hmac_key_file,
    )

    assert settings.postgres_password_file == postgres_password_file
    assert settings.audit_hmac_key_file == audit_hmac_key_file


def test_production_requires_an_explicit_embedding_cache_path(tmp_path: Path) -> None:
    """Production cannot silently depend on the process-global HF cache."""
    postgres_password_file = tmp_path / "postgres-password"
    audit_hmac_key_file = tmp_path / "audit-hmac-key"
    postgres_password_file.write_text("postgres-password", encoding="utf-8")
    audit_hmac_key_file.write_text("audit-hmac-key", encoding="utf-8")
    audit_hmac_key_file.chmod(0o600)

    with pytest.raises(ValueError, match="embedding_cache_dir"):
        Settings(
            environment="production",
            embedding_device="cuda:0",
            postgres_password_file=postgres_password_file,
            audit_hmac_key_file=audit_hmac_key_file,
        )


def test_settings_reject_invalid_retrieval_limits() -> None:
    """Positive limits and ordered search limits are required."""
    with pytest.raises(ValueError):
        Settings(embedding_dimension=0)
    with pytest.raises(ValueError):
        Settings(keyword_candidate_limit=0)
    with pytest.raises(ValueError):
        Settings(search_default_limit=21, search_max_limit=20)


def test_settings_reject_unknown_constructor_fields() -> None:
    """Unknown settings values cannot silently alter runtime configuration."""
    with pytest.raises(ValidationError):
        Settings(unapproved_setting="value")  # type: ignore[call-arg]


def test_settings_repr_redacts_api_token_and_never_contains_secret_file_content(
    tmp_path: Path,
) -> None:
    """Debug representations cannot reveal supplied secret material."""
    postgres_password_file = tmp_path / "postgres-password"
    audit_hmac_key_file = tmp_path / "audit-hmac-key"
    postgres_password_file.write_text("database-password-raw", encoding="utf-8")
    audit_hmac_key_file.write_text("audit-key-raw", encoding="utf-8")
    settings = Settings(
        api_token="api-token-raw",
        postgres_password_file=postgres_password_file,
        audit_hmac_key_file=audit_hmac_key_file,
    )

    representation = repr(settings)
    assert "api-token-raw" not in representation
    assert "database-password-raw" not in representation
    assert "audit-key-raw" not in representation


def test_production_loads_audit_key_bytes_without_any_normalization(
    tmp_path: Path,
) -> None:
    """Every file byte, including NUL and terminal CR/LF, remains key material."""
    raw_key = b"\x00SENSITIVE_MATERIAL-\xed\x95\x9c\xea\xb8\x80\xff\n\r\n"
    key_file = _write_secret(tmp_path / "audit-key", raw_key)

    settings = _production_settings(tmp_path, audit_hmac_key_file=key_file)

    loader = getattr(settings, "load_audit_hmac_key", None)
    assert callable(loader)
    loaded = loader()
    assert loaded.as_bytes() == raw_key
    assert loaded.as_bytes() is loaded.as_bytes()
    assert loader() is loaded
    assert (
        audit_query_hmac(loaded.as_bytes(), "exact query")
        == hmac.new(raw_key, b"exact query", hashlib.sha256).digest()
    )
    assert raw_key.hex() not in repr(loaded)
    assert raw_key.decode("utf-8", errors="ignore") not in repr(loaded)
    assert raw_key.hex() not in repr(settings)
    assert raw_key.decode("utf-8", errors="ignore") not in repr(settings)
    assert "SENSITIVE_MATERIAL" not in str(settings)
    assert "SENSITIVE_MATERIAL" not in repr(settings.model_dump())


@pytest.mark.parametrize(
    "mode",
    [0o000, 0o400, 0o640, 0o644, 0o700],
    ids=["0000", "0400", "0640", "0644", "0700"],
)
def test_production_rejects_audit_key_mode_other_than_exact_0600(
    tmp_path: Path,
    mode: int,
) -> None:
    key_file = _write_secret(tmp_path / "audit-key", b"mode-secret", mode=mode)

    with pytest.raises(ValidationError) as error_info:
        _production_settings(tmp_path, audit_hmac_key_file=key_file)

    assert "mode-secret" not in str(error_info.value)
    assert "mode-secret" not in repr(error_info.value.errors())


@pytest.mark.parametrize("kind", ["missing", "directory", "symlink", "empty"])
def test_production_rejects_unsafe_audit_key_file_kinds(
    tmp_path: Path,
    kind: str,
) -> None:
    key_file = tmp_path / "audit-key"
    if kind == "directory":
        key_file.mkdir(mode=0o600)
    elif kind == "symlink":
        target = _write_secret(tmp_path / "symlink-target", b"symlink-secret")
        key_file.symlink_to(target)
    elif kind == "empty":
        _write_secret(key_file, b"")

    with pytest.raises(ValidationError) as error_info:
        _production_settings(tmp_path, audit_hmac_key_file=key_file)

    rendered = f"{error_info.value!s}\n{error_info.value.errors()!r}"
    assert "symlink-secret" not in rendered


def test_production_rejects_an_unreadable_audit_key_without_leaking_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key_file = _write_secret(tmp_path / "audit-key", b"unreadable-secret")
    real_open = os.open

    def deny_key(path: object, flags: int, *args: object, **kwargs: object) -> int:
        if Path(path) == key_file:  # type: ignore[arg-type]
            raise PermissionError("unreadable-secret")
        return real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("omf_retrieval.settings.os.open", deny_key)

    with pytest.raises(ValidationError) as error_info:
        _production_settings(tmp_path, audit_hmac_key_file=key_file)

    assert "unreadable-secret" not in str(error_info.value)
    assert "unreadable-secret" not in repr(error_info.value.errors())


def test_production_rejects_audit_key_metadata_changed_during_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key_file = _write_secret(tmp_path / "audit-key", b"changing-secret")
    real_read = os.read
    changed = False

    def change_mode_after_read(descriptor: int, size: int) -> bytes:
        nonlocal changed
        content = real_read(descriptor, size)
        if content and not changed:
            changed = True
            key_file.chmod(0o640)
        return content

    monkeypatch.setattr("omf_retrieval.settings.os.read", change_mode_after_read)

    with pytest.raises(ValidationError) as error_info:
        _production_settings(tmp_path, audit_hmac_key_file=key_file)

    assert "changing-secret" not in str(error_info.value)
    assert "changing-secret" not in repr(error_info.value.errors())


@pytest.mark.parametrize(
    ("size", "accepted"),
    [(MAX_AUDIT_HMAC_KEY_BYTES, True), (MAX_AUDIT_HMAC_KEY_BYTES + 1, False)],
    ids=["exact-64-kib", "64-kib-plus-one"],
)
def test_audit_key_file_has_an_inclusive_64_kib_limit(
    tmp_path: Path,
    size: int,
    accepted: bool,
) -> None:
    key_file = _write_secret(tmp_path / "audit-key", b"k" * size)

    if accepted:
        loaded = _production_settings(
            tmp_path, audit_hmac_key_file=key_file
        ).load_audit_hmac_key()
        assert len(loaded.as_bytes()) == MAX_AUDIT_HMAC_KEY_BYTES
    else:
        with pytest.raises(ValidationError):
            _production_settings(tmp_path, audit_hmac_key_file=key_file)


def test_oversize_sparse_audit_key_is_rejected_before_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key_file = _write_secret(tmp_path / "audit-key", b"k")
    with key_file.open("r+b") as stream:
        stream.truncate(MAX_AUDIT_HMAC_KEY_BYTES + 1)

    def forbidden_read(_descriptor: int, _size: int) -> bytes:
        pytest.fail("oversize audit key was read")

    monkeypatch.setattr("omf_retrieval.settings.os.read", forbidden_read)

    with pytest.raises(ValidationError):
        _production_settings(tmp_path, audit_hmac_key_file=key_file)


def test_concurrent_growth_is_bounded_and_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key_file = _write_secret(tmp_path / "audit-key", b"initial")
    real_read = os.read
    returned_sizes: list[int] = []
    appended = False

    def append_large_payload(descriptor: int, size: int) -> bytes:
        nonlocal appended
        content = real_read(descriptor, size)
        returned_sizes.append(len(content))
        if content and not appended:
            appended = True
            with key_file.open("ab") as stream:
                stream.write(b"x" * (MAX_AUDIT_HMAC_KEY_BYTES + 1))
        return content

    monkeypatch.setattr("omf_retrieval.settings.os.read", append_large_payload)

    with pytest.raises(ValidationError):
        _production_settings(tmp_path, audit_hmac_key_file=key_file)

    assert returned_sizes
    assert sum(returned_sizes) <= MAX_AUDIT_HMAC_KEY_BYTES + 1


def test_concurrent_truncation_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key_file = _write_secret(tmp_path / "audit-key", b"truncate-secret")
    real_read = os.read
    truncated = False

    def truncate_after_read(descriptor: int, size: int) -> bytes:
        nonlocal truncated
        content = real_read(descriptor, size)
        if content and not truncated:
            truncated = True
            with key_file.open("r+b") as stream:
                stream.truncate(1)
        return content

    monkeypatch.setattr("omf_retrieval.settings.os.read", truncate_after_read)

    with pytest.raises(ValidationError) as error_info:
        _production_settings(tmp_path, audit_hmac_key_file=key_file)

    assert "truncate-secret" not in str(error_info.value)


@pytest.mark.parametrize("copy_kind", ["direct", "shallow", "deep", "model-copy"])
def test_cached_audit_key_never_survives_a_configured_path_change(
    tmp_path: Path,
    copy_kind: str,
) -> None:
    first_file = _write_secret(tmp_path / "first-key", b"first-secret")
    second_file = _write_secret(tmp_path / "second-key", b"second-secret")
    original = _production_settings(tmp_path, audit_hmac_key_file=first_file)
    assert original.load_audit_hmac_key().as_bytes() == b"first-secret"

    if copy_kind == "direct":
        changed = original
        changed.audit_hmac_key_file = second_file
    elif copy_kind == "shallow":
        changed = copy.copy(original)
        changed.audit_hmac_key_file = second_file
    elif copy_kind == "deep":
        changed = copy.deepcopy(original)
        changed.audit_hmac_key_file = second_file
    else:
        changed = original.model_copy(update={"audit_hmac_key_file": second_file})

    assert changed.load_audit_hmac_key().as_bytes() == b"second-secret"
    assert "second-secret" not in repr(changed)
    assert "second-secret" not in repr(changed.model_dump())


def test_cached_audit_key_rejects_a_path_changed_to_a_symlink(
    tmp_path: Path,
) -> None:
    first_file = _write_secret(tmp_path / "first-key", b"first-secret")
    target = _write_secret(tmp_path / "target-key", b"target-secret")
    symlink = tmp_path / "symlink-key"
    symlink.symlink_to(target)
    settings = _production_settings(tmp_path, audit_hmac_key_file=first_file)

    settings.audit_hmac_key_file = symlink

    with pytest.raises(ValueError) as error_info:
        settings.load_audit_hmac_key()
    assert "first-secret" not in str(error_info.value)
    assert "target-secret" not in str(error_info.value)


def test_cached_audit_key_revalidates_a_replaced_file_at_the_same_path(
    tmp_path: Path,
) -> None:
    key_file = _write_secret(tmp_path / "audit-key", b"first-secret")
    settings = _production_settings(tmp_path, audit_hmac_key_file=key_file)
    assert settings.load_audit_hmac_key().as_bytes() == b"first-secret"
    replacement = _write_secret(tmp_path / "replacement", b"second-secret")
    os.replace(replacement, key_file)

    assert settings.load_audit_hmac_key().as_bytes() == b"second-secret"


def test_lexical_path_alias_reuses_only_the_same_validated_file_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key_file = _write_secret(tmp_path / "audit-key", b"alias-secret")
    (tmp_path / "child").mkdir()
    settings = _production_settings(tmp_path, audit_hmac_key_file=key_file)
    loaded = settings.load_audit_hmac_key()
    settings.audit_hmac_key_file = tmp_path / "child" / ".." / "audit-key"

    def forbidden_read(_descriptor: int, _size: int) -> bytes:
        pytest.fail("an alias of the cached identity copied the key again")

    monkeypatch.setattr("omf_retrieval.settings.os.read", forbidden_read)

    assert settings.load_audit_hmac_key() is loaded


def test_atomic_path_replace_during_read_fails_closed_and_closes_every_fd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key_file = _write_secret(tmp_path / "audit-key", b"first-secret")
    replacement = _write_secret(tmp_path / "replacement", b"second-secret")
    real_open = os.open
    real_read = os.read
    real_close = os.close
    opened: list[int] = []
    closed: list[int] = []
    replaced = False

    def track_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        descriptor = real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]
        if Path(path) == key_file:  # type: ignore[arg-type]
            opened.append(descriptor)
        return descriptor

    def replace_after_read(descriptor: int, size: int) -> bytes:
        nonlocal replaced
        content = real_read(descriptor, size)
        if content and not replaced:
            replaced = True
            os.replace(replacement, key_file)
        return content

    def track_close(descriptor: int) -> None:
        closed.append(descriptor)
        real_close(descriptor)

    monkeypatch.setattr("omf_retrieval.settings.os.open", track_open)
    monkeypatch.setattr("omf_retrieval.settings.os.read", replace_after_read)
    monkeypatch.setattr("omf_retrieval.settings.os.close", track_close)

    with pytest.raises(ValidationError):
        _production_settings(tmp_path, audit_hmac_key_file=key_file)

    assert len(opened) == 2
    assert set(opened).issubset(closed)


def test_nonproduction_does_not_require_or_invent_an_audit_key(tmp_path: Path) -> None:
    missing = tmp_path / "missing-audit-key"
    settings = Settings(environment="development", audit_hmac_key_file=missing)

    assert settings.audit_hmac_key_file == missing
    loader = getattr(settings, "load_audit_hmac_key", None)
    assert callable(loader)
    with pytest.raises(ValueError) as error_info:
        loader()

    assert getattr(error_info.value, "code", None) == "audit_hmac_key_unavailable"
    assert str(missing) not in str(error_info.value)


def test_postgres_password_file_keeps_existing_regular_file_policy(
    tmp_path: Path,
) -> None:
    postgres_password_file = _write_secret(
        tmp_path / "postgres-password", b"postgres", mode=0o644
    )
    audit_key_file = _write_secret(tmp_path / "audit-key", b"audit")

    settings = Settings(
        environment="production",
        embedding_device="cuda:0",
        embedding_cache_dir=tmp_path / "model-cache",
        postgres_password_file=postgres_password_file,
        audit_hmac_key_file=audit_key_file,
    )

    assert settings.postgres_password_file == postgres_password_file
