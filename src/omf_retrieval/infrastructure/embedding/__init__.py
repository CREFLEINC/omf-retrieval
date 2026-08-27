"""Embedding provider contracts and runtime adapters."""

from omf_retrieval.infrastructure.embedding.provider import (
    EmbeddingBatch,
    EmbeddingConfigSnapshot,
    EmbeddingProvider,
    EmbeddingVector,
)
from omf_retrieval.infrastructure.embedding.sentence_transformer import (
    SentenceTransformerEmbeddingProvider,
    SentenceTransformerTokenCounter,
)

__all__ = [
    "EmbeddingBatch",
    "EmbeddingConfigSnapshot",
    "EmbeddingProvider",
    "EmbeddingVector",
    "SentenceTransformerEmbeddingProvider",
    "SentenceTransformerTokenCounter",
]
