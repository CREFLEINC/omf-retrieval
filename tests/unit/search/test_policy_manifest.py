"""Canonical identity and validation tests for immutable search policies."""

from copy import deepcopy
from dataclasses import replace
from importlib import import_module
from uuid import UUID

import pytest

from omf_retrieval.application.indexing.hashing import config_hash
from omf_retrieval.settings import Settings

policy_module = import_module("omf_retrieval.application.search.policy")
_DEFAULT = object()


def _policy_config(**changes: object) -> dict[str, object]:
    config: dict[str, object] = {
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
    config.update(changes)
    return config


def _snapshot(config: object = _DEFAULT) -> object:
    factory = getattr(policy_module, "validated_search_policy_snapshot", None)
    assert callable(factory), "search policy snapshot factory is not implemented"
    return factory(_policy_config() if config is _DEFAULT else config)


def test_settings_builds_the_exact_runtime_search_policy_snapshot() -> None:
    settings = Settings(
        environment="test",
        embedding_model_name="query-model",
        embedding_model_revision="query-revision",
        embedding_dimension=12,
        query_instruction="Retrieve: {query}",
        keyword_similarity_floor=0.25,
        vector_similarity_floor=0.75,
    )

    factory = getattr(settings, "search_policy_snapshot", None)
    assert callable(factory), "Settings search policy snapshot is not implemented"
    snapshot = factory()
    assert snapshot.as_config() == _policy_config(
        query_embedding_model_name="query-model",
        query_embedding_revision="query-revision",
        query_embedding_dimension=12,
        query_instruction="Retrieve: {query}",
        keyword_similarity_floor=0.25,
        vector_similarity_floor=0.75,
    )


def test_canonical_policy_identity_is_stable_across_input_key_order() -> None:
    """JSON key order cannot change one policy identity."""
    first_config = _policy_config()
    second_config = dict(reversed(tuple(first_config.items())))

    first = _snapshot(first_config)
    second = _snapshot(second_config)

    assert first == second
    assert first.canonical_json == second.canonical_json
    assert first.config_hash == second.config_hash
    assert first.config_hash == config_hash(first_config)
    assert first.as_config() == first_config


def test_floor_change_creates_a_distinct_policy_without_mutating_original() -> None:
    """A query-time floor is identity-bearing but snapshots stay immutable."""
    original = _snapshot()
    changed = _snapshot(_policy_config(vector_similarity_floor=0.6))

    assert changed.config_hash != original.config_hash
    assert original.as_config()["vector_similarity_floor"] == 0.48344050397156374
    with pytest.raises((AttributeError, TypeError)):
        original.vector_similarity_floor = 0.6


@pytest.mark.parametrize(
    "floor_name",
    ["keyword_similarity_floor", "vector_similarity_floor"],
)
def test_signed_zero_floor_canonicalizes_to_positive_zero(floor_name: str) -> None:
    """JSONB round trips cannot change a signed-zero policy identity."""
    negative = _snapshot(_policy_config(**{floor_name: -0.0}))
    positive = _snapshot(_policy_config(**{floor_name: 0.0}))

    assert negative == positive
    assert negative.config_hash == positive.config_hash
    assert negative.as_config()[floor_name] == 0.0
    assert str(negative.as_config()[floor_name]) == "0.0"


def test_direct_snapshot_construction_cannot_bypass_exact_validation() -> None:
    """Frozen values must also validate construction paths outside the factory."""
    snapshot = _snapshot()

    with pytest.raises(ValueError, match="^Search policy snapshot is invalid$"):
        replace(snapshot, keyword_weight=1)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.pop("query_instruction"),
        lambda value: value.update({"extra": "value"}),
        lambda value: value.update({"query_embedding_dimension": True}),
        lambda value: value.update({"query_embedding_dimension": 0}),
        lambda value: value.update({"query_embedding_normalize_embeddings": 1}),
        lambda value: value.update({"query_instruction": "  "}),
        lambda value: value.update({"keyword_candidate_limit": True}),
        lambda value: value.update({"vector_candidate_limit": 0}),
        lambda value: value.update({"rrf_k": 1.0}),
        lambda value: value.update({"keyword_weight": 1}),
        lambda value: value.update({"vector_weight": float("nan")}),
        lambda value: value.update({"keyword_similarity_floor": float("inf")}),
        lambda value: value.update({"vector_similarity_floor": -0.1}),
        lambda value: value.update({"calibration_status": "unknown"}),
    ],
)
def test_policy_snapshot_rejects_missing_extra_wrong_and_nonfinite_values(
    mutate: object,
) -> None:
    """Exact key, type, range, finite, and calibration contracts fail closed."""
    invalid = deepcopy(_policy_config())
    mutate(invalid)  # type: ignore[operator]

    with pytest.raises(ValueError) as error:
        _snapshot(invalid)

    assert str(error.value) == "Search policy snapshot is invalid"
    assert "/" not in str(error.value)


@pytest.mark.parametrize(
    "value",
    [None, 1, "a" * 64, True, UUID(int=1), {}, []],
)
def test_policy_snapshot_requires_an_exact_json_object(value: object) -> None:
    with pytest.raises(ValueError, match="^Search policy snapshot is invalid$"):
        _snapshot(value)


def test_manifest_coordinate_rejects_invalid_hash_and_snapshot_mismatch() -> None:
    """A manifest coordinate cannot bind content to the wrong digest."""
    manifest_type = getattr(policy_module, "SearchPolicyManifest", None)
    assert callable(manifest_type), "search policy manifest type is not implemented"
    snapshot = _snapshot()

    manifest = manifest_type(UUID(int=1), snapshot.config_hash, snapshot)
    assert manifest.policy_id == UUID(int=1)

    for invalid_hash in ("x" * 64, "a" * 63, snapshot.config_hash.upper()):
        with pytest.raises(ValueError, match="^Search policy manifest is invalid$"):
            manifest_type(UUID(int=1), invalid_hash, snapshot)

    with pytest.raises(ValueError, match="^Search policy manifest is invalid$"):
        manifest_type(
            UUID(int=1),
            snapshot.config_hash,
            _snapshot(_policy_config(vector_similarity_floor=0.6)),
        )
