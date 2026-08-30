"""Unit contracts for append-only PostgreSQL search policy storage."""

from importlib import import_module
from unittest.mock import Mock
from uuid import UUID

import pytest

from omf_retrieval.application.search.policy import (
    SearchPolicyManifest,
    validated_search_policy_snapshot,
)
from omf_retrieval.infrastructure.database.repository_errors import (
    RepositoryInvariantError,
)

database_module = import_module("omf_retrieval.infrastructure.database")
models_module = import_module("omf_retrieval.infrastructure.database.models")


def _config(**changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "query_embedding_model_name": "Qwen/Qwen3-Embedding-0.6B",
        "query_embedding_revision": "revision-1",
        "query_embedding_dimension": 1024,
        "query_embedding_normalize_embeddings": True,
        "query_instruction": "Instruct: {query}",
        "keyword_candidate_limit": 50,
        "vector_candidate_limit": 50,
        "rrf_k": 60,
        "keyword_weight": 1.0,
        "vector_weight": 1.0,
        "keyword_similarity_floor": 0.03658536400000001,
        "vector_similarity_floor": 0.48344050397156374,
        "calibration_status": "calibrated",
    }
    value.update(changes)
    return value


def _repository_type() -> object:
    repository_type = getattr(database_module, "PostgresSearchPolicyRepository", None)
    assert callable(repository_type), "policy repository is not implemented"
    return repository_type


def _stored_row(policy_id: UUID, config: dict[str, object]) -> object:
    model_type = getattr(models_module, "SearchPolicyManifest", None)
    assert callable(model_type), "policy ORM model is not implemented"
    snapshot = validated_search_policy_snapshot(config)
    return model_type(
        id=policy_id,
        config_hash=snapshot.config_hash,
        snapshot=snapshot.as_config(),
    )


def test_register_twice_resolves_one_policy_identity() -> None:
    """Conflict-safe registration returns one row and one policy ID."""
    snapshot = validated_search_policy_snapshot(_config())
    policy_id = UUID(int=1)
    stored = _stored_row(policy_id, snapshot.as_config())
    session = Mock()
    session.scalar.side_effect = [policy_id, None, stored]
    repository = _repository_type()(session)

    first = repository.register(snapshot)
    second = repository.register(snapshot)

    assert (
        first
        == second
        == SearchPolicyManifest(policy_id, snapshot.config_hash, snapshot)
    )
    assert session.scalar.call_count == 3


def test_floor_change_registers_a_distinct_identity() -> None:
    """Query-time policy changes do not share a manifest coordinate."""
    first = validated_search_policy_snapshot(_config())
    second = validated_search_policy_snapshot(_config(vector_similarity_floor=0.6))
    session = Mock()
    session.scalar.side_effect = [UUID(int=1), UUID(int=2)]
    repository = _repository_type()(session)

    first_manifest = repository.register(first)
    second_manifest = repository.register(second)

    assert first_manifest.policy_id != second_manifest.policy_id
    assert first_manifest.config_hash != second_manifest.config_hash


def test_register_resolves_jsonb_normalized_signed_zero_as_same_policy() -> None:
    """A JSONB positive-zero row resolves a signed-zero registration."""
    signed_zero = validated_search_policy_snapshot(
        _config(keyword_similarity_floor=-0.0, vector_similarity_floor=-0.0)
    )
    stored = _stored_row(
        UUID(int=1),
        _config(keyword_similarity_floor=0.0, vector_similarity_floor=0.0),
    )
    session = Mock()
    session.scalar.side_effect = [None, stored]
    repository = _repository_type()(session)

    manifest = repository.register(signed_zero)

    assert manifest.policy_id == UUID(int=1)
    assert manifest.config_hash == signed_zero.config_hash
    assert manifest.snapshot == signed_zero


def test_resolve_revalidates_hash_and_exact_stored_content() -> None:
    """A stored hash/content mismatch fails with a stable safe error."""
    requested = validated_search_policy_snapshot(_config())
    changed = validated_search_policy_snapshot(_config(vector_similarity_floor=0.6))
    stored = _stored_row(UUID(int=2), changed.as_config())
    stored.config_hash = requested.config_hash
    session = Mock()
    session.scalar.return_value = stored
    repository = _repository_type()(session)

    with pytest.raises(
        RepositoryInvariantError,
        match="^Search policy manifest is inconsistent$",
    ) as error:
        repository.resolve(requested.config_hash)

    assert requested.config_hash not in str(error.value)
    assert "0.6" not in str(error.value)


@pytest.mark.parametrize("digest", ["bad", "A" * 64, True, None])
def test_resolve_rejects_invalid_hash_without_querying_storage(digest: object) -> None:
    session = Mock()
    repository = _repository_type()(session)

    with pytest.raises(
        RepositoryInvariantError,
        match="^Search policy manifest is inconsistent$",
    ):
        repository.resolve(digest)

    session.scalar.assert_not_called()


def test_missing_manifest_fails_with_the_same_safe_error() -> None:
    snapshot = validated_search_policy_snapshot(_config())
    session = Mock()
    session.scalar.return_value = None
    repository = _repository_type()(session)

    with pytest.raises(
        RepositoryInvariantError,
        match="^Search policy manifest is inconsistent$",
    ):
        repository.resolve(snapshot.config_hash)
