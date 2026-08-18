"""Validated runtime settings for retrieval services."""

from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Provide validated configuration from ``OMF_RETRIEVAL_`` variables.

    Secret files are represented only by paths. Their contents are deliberately
    not read at this boundary so configuration representations cannot expose
    them.

    Raises:
        ValueError: If retrieval limits or production safeguards are invalid.
    """

    model_config = SettingsConfigDict(env_prefix="OMF_RETRIEVAL_", extra="forbid")

    environment: Literal["development", "test", "production"] = "development"
    embedding_model_name: str = "Qwen/Qwen3-Embedding-0.6B"
    embedding_model_revision: str = "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
    embedding_dimension: int = 1024
    embedding_device: str = "cpu"
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
    search_default_limit: int = 5
    search_max_limit: int = 20
    parent_context_max_tokens: int = 1200
    postgres_password_file: Path | None = None
    audit_hmac_key_file: Path | None = None
    api_token: SecretStr | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def validate_runtime_contract(self) -> Self:
        """Validate retrieval bounds and production-only safeguards.

        Returns:
            This validated settings object.

        Raises:
            ValueError: If a bound, device, or required secret path is invalid.
        """
        self._require_positive_limits()
        if self.search_default_limit > self.search_max_limit:
            raise ValueError("search_default_limit must not exceed search_max_limit")
        if self.environment == "production":
            self._require_production_safeguards()
        return self

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

    def _require_production_safeguards(self) -> None:
        """Require the approved GPU and both existing secret-file paths."""
        if self.embedding_device != "cuda:0":
            raise ValueError("production embedding_device must be cuda:0")
        for field_name, secret_path in {
            "postgres_password_file": self.postgres_password_file,
            "audit_hmac_key_file": self.audit_hmac_key_file,
        }.items():
            if secret_path is None or not secret_path.is_file():
                raise ValueError(f"production {field_name} must be a regular file")
