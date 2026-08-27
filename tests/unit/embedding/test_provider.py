"""Unit tests for the framework-free embedding provider contract."""

import importlib.util
from collections.abc import Sequence
from dataclasses import FrozenInstanceError
from inspect import signature
from typing import get_type_hints

import pytest

from omf_retrieval.domain.models import EmbeddingDescriptor
from omf_retrieval.infrastructure import embedding
from omf_retrieval.infrastructure.embedding.provider import (
    EmbeddingBatch,
    EmbeddingConfigSnapshot,
    EmbeddingProvider,
    EmbeddingVector,
)


def test_embedding_provider_module_exists() -> None:
    """Removing the approved provider boundary makes the contract unavailable."""
    module_spec = importlib.util.find_spec(
        "omf_retrieval.infrastructure.embedding.provider"
    )

    assert module_spec is not None


def test_provider_exports_an_immutable_full_embedding_identity() -> None:
    """Rollback needs every document and query behavior field, not only the model."""
    from omf_retrieval.infrastructure.embedding import provider as provider_module

    snapshot_type = getattr(provider_module, "EmbeddingConfigSnapshot", None)

    assert snapshot_type is not None


def test_embedding_package_exports_the_provider_contract() -> None:
    """Dropping a public export makes the infrastructure boundary unusable."""
    assert getattr(embedding, "EmbeddingProvider", None) is EmbeddingProvider
    assert (
        getattr(embedding, "EmbeddingConfigSnapshot", None) is EmbeddingConfigSnapshot
    )
    assert getattr(embedding, "EmbeddingVector", None) == tuple[float, ...]
    assert getattr(embedding, "EmbeddingBatch", None) == tuple[tuple[float, ...], ...]


def test_embedding_provider_declares_the_approved_immutable_signatures() -> None:
    """Mutable results or signature drift violate the indexing boundary."""
    descriptor_getter = EmbeddingProvider.descriptor.fget
    assert descriptor_getter is not None
    assert get_type_hints(descriptor_getter) == {"return": EmbeddingDescriptor}
    config_getter = EmbeddingProvider.embedding_config_snapshot.fget
    assert config_getter is not None
    assert get_type_hints(config_getter) == {"return": EmbeddingConfigSnapshot}
    assert list(signature(EmbeddingProvider.embed_query).parameters) == [
        "self",
        "query",
    ]
    assert get_type_hints(EmbeddingProvider.embed_query) == {
        "query": str,
        "return": tuple[float, ...],
    }
    assert list(signature(EmbeddingProvider.embed_documents).parameters) == [
        "self",
        "documents",
    ]
    assert get_type_hints(EmbeddingProvider.embed_documents) == {
        "documents": Sequence[str],
        "return": tuple[tuple[float, ...], ...],
    }
    assert get_type_hints(EmbeddingProvider.is_ready) == {"return": bool}


def test_embedding_config_snapshot_is_frozen_and_returns_fresh_json_values() -> None:
    """A caller cannot mutate the provider identity used by rollback."""
    snapshot = _DeterministicProvider().embedding_config_snapshot
    first = snapshot.as_config()
    first["document"]["model_name"] = "changed"  # type: ignore[index]

    with pytest.raises(FrozenInstanceError):
        snapshot.model_name = "changed"  # type: ignore[misc]

    assert snapshot.as_config()["document"]["model_name"] == "test/model"  # type: ignore[index]


class _DeterministicProvider:
    """Minimal fake used only to prove structural protocol conformance."""

    @property
    def descriptor(self) -> EmbeddingDescriptor:
        """Return the fixed fake descriptor."""
        return EmbeddingDescriptor(
            model_name="test/model",
            revision="fixed-revision",
            dimension=2,
        )

    @property
    def embedding_config_snapshot(self) -> EmbeddingConfigSnapshot:
        """Return the fixed fake's complete immutable behavior identity."""
        return EmbeddingConfigSnapshot(
            provider="fake",
            model_name="test/model",
            revision="fixed-revision",
            dimension=2,
            normalize_embeddings=True,
            library_name="fake-library",
            library_version="1.0",
            query_instruction="Query: {query}",
        )

    def embed_query(self, query: str) -> EmbeddingVector:
        """Return a deterministic immutable query vector."""
        return (float(len(query)), 1.0)

    def embed_documents(self, documents: Sequence[str]) -> EmbeddingBatch:
        """Return one immutable vector per document in input order."""
        return tuple((float(len(document)), 0.0) for document in documents)

    def is_ready(self) -> bool:
        """Keep the local fake ready without external dependencies."""
        return True


def test_runtime_protocol_accepts_a_structurally_conforming_provider() -> None:
    """Removing runtime structural support breaks dependency injection checks."""
    provider = _DeterministicProvider()

    assert isinstance(provider, EmbeddingProvider)
    assert provider.descriptor.dimension == 2
    assert provider.embed_query("질의") == (2.0, 1.0)
    assert provider.embed_documents(("가", "나다")) == ((1.0, 0.0), (2.0, 0.0))
    assert provider.embed_documents(()) == ()
    assert provider.is_ready() is True
