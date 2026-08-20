"""Framework-free contract for embedding providers."""

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from omf_retrieval.domain.models import EmbeddingDescriptor

EmbeddingVector = tuple[float, ...]
EmbeddingBatch = tuple[EmbeddingVector, ...]


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Generate immutable query and document embeddings in input order."""

    @property
    def descriptor(self) -> EmbeddingDescriptor:
        """Return the immutable model identity and output dimension."""

    def embed_query(self, query: str) -> EmbeddingVector:
        """Embed one query with provider-specific query handling."""

    def embed_documents(self, documents: Sequence[str]) -> EmbeddingBatch:
        """Embed documents in input order, returning an empty tuple for none."""

    def is_ready(self) -> bool:
        """Return whether this provider can serve embeddings."""
