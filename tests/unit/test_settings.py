"""Unit tests for validated application settings."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from omf_retrieval.settings import Settings


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


def test_settings_read_prefixed_environment_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The OMF_RETRIEVAL_ prefix supplies settings from the environment."""
    monkeypatch.setenv("OMF_RETRIEVAL_EMBEDDING_DEVICE", "cuda:0")
    monkeypatch.setenv("OMF_RETRIEVAL_EMBEDDING_BATCH_SIZE", "64")
    monkeypatch.setenv("OMF_RETRIEVAL_EMBEDDING_CACHE_DIR", "/srv/model-cache")

    settings = Settings()
    assert settings.embedding_device == "cuda:0"
    assert settings.embedding_batch_size == 64
    assert settings.embedding_cache_dir == Path("/srv/model-cache")


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
