"""Framework-free contract for embedding providers."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from omf_retrieval.domain.models import EmbeddingDescriptor

EmbeddingVector = tuple[float, ...]
EmbeddingBatch = tuple[EmbeddingVector, ...]


@dataclass(frozen=True, slots=True)
class EmbeddingConfigSnapshot:
    """Immutable identity of every persisted embedding behavior field."""

    provider: str
    model_name: str
    revision: str
    dimension: int
    normalize_embeddings: bool
    library_name: str
    library_version: str
    query_instruction: str

    def __post_init__(self) -> None:
        """Reject incomplete or coercible identities at the adapter boundary."""
        strings = (
            self.provider,
            self.model_name,
            self.revision,
            self.library_name,
            self.library_version,
            self.query_instruction,
        )
        if any(type(value) is not str or not value.strip() for value in strings):
            raise ValueError("Embedding identity strings must be nonblank and exact")
        if type(self.dimension) is not int or self.dimension <= 0:
            raise ValueError("Embedding dimension must be a positive exact integer")
        if type(self.normalize_embeddings) is not bool:
            raise ValueError("Embedding normalization must be an exact boolean")

    @property
    def descriptor(self) -> EmbeddingDescriptor:
        """Derive the public descriptor from the same immutable snapshot."""
        return EmbeddingDescriptor(self.model_name, self.revision, self.dimension)

    def as_config(self) -> dict[str, object]:
        """Return a fresh canonical JSON-compatible persistence value."""
        return {
            "document": {
                "provider": self.provider,
                "model_name": self.model_name,
                "revision": self.revision,
                "dimension": self.dimension,
                "normalize_embeddings": self.normalize_embeddings,
                "library_name": self.library_name,
                "library_version": self.library_version,
            },
            "query": {"instruction": self.query_instruction},
        }


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Generate immutable query and document embeddings in input order."""

    @property
    def descriptor(self) -> EmbeddingDescriptor:
        """Return the immutable model identity and output dimension."""

    @property
    def embedding_config_snapshot(self) -> EmbeddingConfigSnapshot:
        """Return the immutable full document and query behavior identity."""

    def embed_query(self, query: str) -> EmbeddingVector:
        """Embed one query with provider-specific query handling."""

    def embed_documents(self, documents: Sequence[str]) -> EmbeddingBatch:
        """Embed documents in input order, returning an empty tuple for none."""

    def is_ready(self) -> bool:
        """Return whether this provider can serve embeddings."""
