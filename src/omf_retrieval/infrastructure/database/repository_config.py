"""Canonical indexing configuration identities used by repositories."""

from copy import deepcopy
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from omf_retrieval.application.indexing.config_identity import (
    IndexConfigValidationError,
    document_embedding_config_hash,
    full_index_config_hash,
    require_nonblank_string,
    validated_embedding_config,
)
from omf_retrieval.application.indexing.hashing import config_hash
from omf_retrieval.domain.models import EmbeddingDescriptor
from omf_retrieval.infrastructure.database.models import IndexConfig, SourceProfile
from omf_retrieval.infrastructure.source.profiles import SourceProfileConfig


@dataclass(frozen=True, slots=True)
class IndexConfigurationBinding:
    """Stable persistence identities needed to compose one fixed index run."""

    source_profile_id: UUID
    index_config_id: UUID
    commit_sha: str
    embedding_config_hash: str


@dataclass(frozen=True, slots=True)
class EmbeddingAdapterIdentity:
    """Identify document-vector behavior outside the model descriptor."""

    provider: str
    normalize_embeddings: bool
    library_name: str
    library_version: str

    def __post_init__(self) -> None:
        """Require exact immutable adapter behavior identity values."""
        for value in (self.provider, self.library_name, self.library_version):
            require_nonblank_string(value)
        if type(self.normalize_embeddings) is not bool:
            raise IndexConfigValidationError(
                "normalize_embeddings must be an exact boolean"
            )


class PostgresIndexConfigurationRepository:
    """Persist the approved source selection and immutable index configuration."""

    def __init__(self, session: Session) -> None:
        required = ("add", "flush", "scalar")
        if not all(hasattr(session, attribute) for attribute in required):
            raise TypeError("session must provide the SQLAlchemy Session contract")
        self._session = session

    def ensure(
        self,
        *,
        profile: SourceProfileConfig,
        parser_config: dict[str, object],
        chunk_config: dict[str, object],
        tokenizer_config: dict[str, object],
        embedding_config: dict[str, object],
        rrf_config: dict[str, object],
    ) -> IndexConfigurationBinding:
        """Return persisted IDs while adopting the approved source-path contract."""
        if type(profile) is not SourceProfileConfig or profile.commit_sha is None:
            raise IndexConfigValidationError(
                "fixed source profile configuration is required"
            )
        config_hash = full_index_config_hash(
            parser_config=parser_config,
            chunk_config=chunk_config,
            tokenizer_config=tokenizer_config,
            embedding_config=embedding_config,
            rrf_config=rrf_config,
        )
        embedding_hash = document_embedding_config_hash(embedding_config)
        source = self._session.scalar(
            select(SourceProfile)
            .where(SourceProfile.source_key == profile.source_key)
            .with_for_update()
        )
        include_patterns = list(profile.include_patterns)
        exclude_patterns = list(profile.exclude_patterns)
        if source is None:
            source = SourceProfile(
                source_key=profile.source_key,
                include_patterns=include_patterns,
                exclude_patterns=exclude_patterns,
            )
            self._session.add(source)
            self._session.flush()
        elif (
            source.include_patterns != include_patterns
            or source.exclude_patterns != exclude_patterns
        ):
            source.include_patterns = include_patterns
            source.exclude_patterns = exclude_patterns
            self._session.flush()

        stored_config = self._session.scalar(
            select(IndexConfig).where(IndexConfig.config_hash == config_hash)
        )
        snapshots = (
            deepcopy(parser_config),
            deepcopy(chunk_config),
            deepcopy(tokenizer_config),
            deepcopy(embedding_config),
            deepcopy(rrf_config),
        )
        if stored_config is None:
            stored_config = IndexConfig(
                config_hash=config_hash,
                parser_config=snapshots[0],
                chunk_config=snapshots[1],
                tokenizer_config=snapshots[2],
                embedding_config=snapshots[3],
                rrf_config=snapshots[4],
            )
            self._session.add(stored_config)
            self._session.flush()
        elif (
            stored_config.parser_config,
            stored_config.chunk_config,
            stored_config.tokenizer_config,
            stored_config.embedding_config,
            stored_config.rrf_config,
        ) != snapshots:
            raise IndexConfigValidationError(
                "stored IndexConfig snapshots do not match config_hash"
            )
        if type(source.id) is not UUID or type(stored_config.id) is not UUID:
            raise IndexConfigValidationError(
                "persisted index configuration identities are incomplete"
            )
        return IndexConfigurationBinding(
            source_profile_id=source.id,
            index_config_id=stored_config.id,
            commit_sha=profile.commit_sha,
            embedding_config_hash=embedding_hash,
        )


def embedding_config_snapshot(
    descriptor: EmbeddingDescriptor,
    adapter: EmbeddingAdapterIdentity,
    query_instruction: str,
) -> dict[str, object]:
    """Compose the exact approved document/query embedding snapshot."""
    document = _document_snapshot(descriptor, adapter)
    require_nonblank_string(query_instruction)
    return {
        "document": document,
        "query": {"instruction": query_instruction},
    }


def _document_snapshot(
    descriptor: EmbeddingDescriptor,
    adapter: EmbeddingAdapterIdentity,
) -> dict[str, object]:
    if type(descriptor) is not EmbeddingDescriptor:
        raise IndexConfigValidationError(
            "descriptor must use the exact EmbeddingDescriptor contract"
        )
    require_nonblank_string(descriptor.model_name)
    require_nonblank_string(descriptor.revision)
    if type(descriptor.dimension) is not int or descriptor.dimension <= 0:
        raise IndexConfigValidationError("dimension must be a positive exact integer")
    if type(adapter) is not EmbeddingAdapterIdentity:
        raise IndexConfigValidationError(
            "adapter must use the exact EmbeddingAdapterIdentity contract"
        )
    return {
        "provider": adapter.provider,
        "model_name": descriptor.model_name,
        "revision": descriptor.revision,
        "dimension": descriptor.dimension,
        "normalize_embeddings": adapter.normalize_embeddings,
        "library_name": adapter.library_name,
        "library_version": adapter.library_version,
    }


def validate_persisted_index_config(
    *,
    stored_config_hash: object,
    parser_config: object,
    chunk_config: object,
    tokenizer_config: object,
    embedding_config: object,
    rrf_config: object,
    descriptor: EmbeddingDescriptor,
    adapter: EmbeddingAdapterIdentity,
) -> str:
    """Validate a DB snapshot and return its bound document embedding hash."""
    expected_full_hash = full_index_config_hash(
        parser_config=parser_config,
        chunk_config=chunk_config,
        tokenizer_config=tokenizer_config,
        embedding_config=embedding_config,
        rrf_config=rrf_config,
    )
    if type(stored_config_hash) is not str or stored_config_hash != expected_full_hash:
        raise IndexConfigValidationError(
            "stored IndexConfig config_hash does not match canonical snapshots"
        )
    document, _ = validated_embedding_config(embedding_config)
    expected = _document_snapshot(descriptor, adapter)
    if document != expected:
        raise IndexConfigValidationError(
            "embedding descriptor or adapter identity mismatches IndexConfig"
        )
    return config_hash(document)


__all__ = [
    "EmbeddingAdapterIdentity",
    "IndexConfigurationBinding",
    "IndexConfigValidationError",
    "PostgresIndexConfigurationRepository",
    "document_embedding_config_hash",
    "embedding_config_snapshot",
    "full_index_config_hash",
    "validate_persisted_index_config",
]
