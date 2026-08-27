"""Unit tests for canonical persisted indexing configuration identities."""

from copy import deepcopy
from importlib import import_module
from uuid import UUID

import pytest

from omf_retrieval.application.indexing.hashing import config_hash
from omf_retrieval.domain.models import EmbeddingDescriptor
from omf_retrieval.infrastructure.database.repository_config import (
    EmbeddingAdapterIdentity,
    IndexConfigValidationError,
    document_embedding_config_hash,
    embedding_config_snapshot,
    full_index_config_hash,
)
from omf_retrieval.infrastructure.source.profiles import SourceProfileConfig

repository_config_module = import_module(
    "omf_retrieval.infrastructure.database.repository_config"
)


def _descriptor() -> EmbeddingDescriptor:
    return EmbeddingDescriptor("test/model", "revision-1", 3)


def _adapter() -> EmbeddingAdapterIdentity:
    return EmbeddingAdapterIdentity(
        provider="sentence-transformers",
        normalize_embeddings=True,
        library_name="sentence-transformers",
        library_version="5.7.0",
    )


def _embedding_config() -> dict[str, object]:
    return embedding_config_snapshot(_descriptor(), _adapter(), "Instruct: {query}")


def _full_hash(
    *,
    embedding_config: object | None = None,
    rrf_config: object | None = None,
) -> str:
    return full_index_config_hash(
        parser_config={"version": "parser-v1"},
        chunk_config={"target_tokens": 400},
        tokenizer_config={"revision": "revision-1"},
        embedding_config=(
            _embedding_config() if embedding_config is None else embedding_config
        ),
        rrf_config={"k": 60} if rrf_config is None else rrf_config,
    )


def test_valid_embedding_snapshot_and_both_hashes_are_canonical() -> None:
    """Approved nested values must map to their exact canonical projections."""
    embedding = _embedding_config()
    expected_document = {
        "provider": "sentence-transformers",
        "model_name": "test/model",
        "revision": "revision-1",
        "dimension": 3,
        "normalize_embeddings": True,
        "library_name": "sentence-transformers",
        "library_version": "5.7.0",
    }

    assert embedding == {
        "document": expected_document,
        "query": {"instruction": "Instruct: {query}"},
    }
    assert document_embedding_config_hash(embedding) == config_hash(expected_document)
    assert _full_hash() == config_hash(
        {
            "parser_config": {"version": "parser-v1"},
            "chunk_config": {"target_tokens": 400},
            "tokenizer_config": {"revision": "revision-1"},
            "embedding_config": embedding,
            "rrf_config": {"k": 60},
        }
    )


def test_query_instruction_and_rrf_only_change_full_hash() -> None:
    """Query and rank behavior must not invalidate document vectors."""
    original = _embedding_config()
    changed_query = deepcopy(original)
    changed_query["query"]["instruction"] = "Changed: {query}"  # type: ignore[index]

    assert _full_hash(embedding_config=changed_query) != _full_hash()
    assert document_embedding_config_hash(changed_query) == (
        document_embedding_config_hash(original)
    )
    assert _full_hash(rrf_config={"k": 61}) != _full_hash()


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("provider", "other-provider"),
        ("model_name", "other/model"),
        ("revision", "revision-2"),
        ("dimension", 4),
        ("normalize_embeddings", False),
        ("library_name", "other-library"),
        ("library_version", "6.0.0"),
    ],
)
def test_each_document_behavior_field_changes_document_hash(
    field: str,
    changed: object,
) -> None:
    """Every approved document-vector behavior field is identity-bearing."""
    original = _embedding_config()
    modified = deepcopy(original)
    modified["document"][field] = changed  # type: ignore[index]

    assert document_embedding_config_hash(modified) != (
        document_embedding_config_hash(original)
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update({"device": "cuda:0"}),
        lambda value: value.update({"cache_dir": "/private/cache"}),
        lambda value: value.update({"batch_size": 32}),
        lambda value: value.update({"unknown": "value"}),
        lambda value: value.pop("query"),
        lambda value: value["document"].pop("provider"),
        lambda value: value["document"].update({"dimension": True}),
        lambda value: value["document"].update({"normalize_embeddings": 1}),
        lambda value: value["query"].update({"instruction": " "}),
    ],
)
def test_runtime_unknown_missing_and_wrong_type_fields_are_rejected(
    mutation: object,
) -> None:
    """Only the approved exact persisted embedding structure is accepted."""
    invalid = deepcopy(_embedding_config())
    mutation(invalid)  # type: ignore[operator]

    with pytest.raises(IndexConfigValidationError):
        document_embedding_config_hash(invalid)


class _ConfigSession:
    def __init__(self) -> None:
        self.source = None
        self.config = None
        self.added: list[object] = []

    def scalar(self, statement: object) -> object:
        sql = str(statement)
        if "FROM source_profiles" in sql:
            return self.source
        if "FROM index_configs" in sql:
            return self.config
        raise AssertionError(sql)

    def add(self, value: object) -> None:
        self.added.append(value)
        if value.__class__.__name__ == "SourceProfile":
            value.id = UUID(int=1)
            self.source = value
        elif value.__class__.__name__ == "IndexConfig":
            value.id = UUID(int=2)
            self.config = value
        else:
            raise AssertionError(type(value))

    def flush(self) -> None:
        return None


def test_configuration_repository_persists_profile_and_reuses_exact_config() -> None:
    """Index composition receives stable source/config IDs without raw ORM setup."""
    session = _ConfigSession()
    repository_type = getattr(
        repository_config_module,
        "PostgresIndexConfigurationRepository",
        None,
    )
    assert callable(repository_type)
    repository = repository_type(session)
    profile = SourceProfileConfig(
        source_key="omf",
        include_patterns=("design/wiki/**/*.md",),
        exclude_patterns=("docs/**",),
        commit_sha="a" * 40,
    )
    embedding = _embedding_config()

    first = repository.ensure(
        profile=profile,
        parser_config={"version": "parser-v1"},
        chunk_config={"target_tokens": 400},
        tokenizer_config={"revision": "revision-1"},
        embedding_config=embedding,
        rrf_config={"k": 60},
    )
    second = repository.ensure(
        profile=profile,
        parser_config={"version": "parser-v1"},
        chunk_config={"target_tokens": 400},
        tokenizer_config={"revision": "revision-1"},
        embedding_config=embedding,
        rrf_config={"k": 60},
    )

    assert first == second
    assert first.source_profile_id == UUID(int=1)
    assert first.index_config_id == UUID(int=2)
    assert first.commit_sha == "a" * 40
    assert first.embedding_config_hash == document_embedding_config_hash(embedding)
    assert len(session.added) == 2


def test_configuration_repository_updates_existing_source_selection_contract() -> None:
    """An upgraded database adopts the approved wiki profile before reindexing."""
    session = _ConfigSession()
    repository_type = getattr(
        repository_config_module,
        "PostgresIndexConfigurationRepository",
        None,
    )
    assert callable(repository_type)
    repository = repository_type(session)
    profile = SourceProfileConfig(
        source_key="omf",
        include_patterns=("design/wiki/**/*.md",),
        exclude_patterns=("docs/**",),
        commit_sha="a" * 40,
    )
    repository.ensure(
        profile=profile,
        parser_config={"version": "parser-v1"},
        chunk_config={"target_tokens": 400},
        tokenizer_config={"revision": "revision-1"},
        embedding_config=_embedding_config(),
        rrf_config={"k": 60},
    )
    assert session.source is not None
    session.source.include_patterns = ["docs/research/**/*.md"]
    session.source.exclude_patterns = ["docs/raw/**"]

    repository.ensure(
        profile=profile,
        parser_config={"version": "parser-v1"},
        chunk_config={"target_tokens": 400},
        tokenizer_config={"revision": "revision-1"},
        embedding_config=_embedding_config(),
        rrf_config={"k": 60},
    )

    assert session.source.include_patterns == ["design/wiki/**/*.md"]
    assert session.source.exclude_patterns == ["docs/**"]
