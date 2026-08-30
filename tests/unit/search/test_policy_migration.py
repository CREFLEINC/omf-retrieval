"""Frozen legacy backfill and migration graph contracts for search policies."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

from omf_retrieval.application.search.policy import (
    validated_search_policy_snapshot,
)

MIGRATION_PATH = (
    Path(__file__).resolve().parents[3]
    / "migrations/versions/0003_search_policy_manifest.py"
)


def _migration() -> object:
    assert MIGRATION_PATH.is_file(), "search policy migration is not implemented"
    spec = spec_from_file_location("search_policy_manifest_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _embedding_config() -> dict[str, object]:
    return {
        "document": {
            "provider": "sentence-transformers",
            "model_name": "Qwen/Qwen3-Embedding-0.6B",
            "revision": "revision-1",
            "dimension": 1024,
            "normalize_embeddings": True,
            "library_name": "sentence-transformers",
            "library_version": "5.7.0",
        },
        "query": {"instruction": "Instruct: {query}"},
    }


def _retrieval_config() -> dict[str, object]:
    return {
        "k": 60,
        "keyword_weight": 1.0,
        "vector_weight": 1.0,
        "keyword_similarity_floor": 0.03658536400000001,
        "vector_similarity_floor": 0.48344050397156374,
        "evidence_floor_status": "calibrated",
    }


def test_migration_is_the_single_additive_head_after_integrated_0002() -> None:
    migration = _migration()

    assert migration.revision == "0003_search_policy_manifest"
    assert migration.down_revision == "0002_index_run_lifecycle"


def test_frozen_legacy_projection_matches_application_canonical_identity() -> None:
    migration = _migration()
    frozen = getattr(migration, "_frozen_legacy_policy_snapshot", None)
    assert callable(frozen), "frozen legacy projection is not implemented"

    snapshot, digest = frozen(_embedding_config(), _retrieval_config())
    application = validated_search_policy_snapshot(snapshot)

    assert snapshot["keyword_candidate_limit"] == 50
    assert snapshot["vector_candidate_limit"] == 50
    assert snapshot["calibration_status"] == "calibrated"
    assert snapshot == application.as_config()
    assert digest == application.config_hash


@pytest.mark.parametrize(
    "floor_name",
    ["keyword_similarity_floor", "vector_similarity_floor"],
)
def test_frozen_legacy_projection_canonicalizes_signed_zero_floor(
    floor_name: str,
) -> None:
    """Frozen backfill and application policy use one signed-zero identity."""
    migration = _migration()
    negative_retrieval = _retrieval_config()
    negative_retrieval[floor_name] = -0.0
    positive_retrieval = _retrieval_config()
    positive_retrieval[floor_name] = 0.0

    negative, negative_digest = migration._frozen_legacy_policy_snapshot(
        _embedding_config(), negative_retrieval
    )
    positive, positive_digest = migration._frozen_legacy_policy_snapshot(
        _embedding_config(), positive_retrieval
    )
    application = validated_search_policy_snapshot(negative)

    assert negative == positive == application.as_config()
    assert negative_digest == positive_digest == application.config_hash
    assert str(negative[floor_name]) == "0.0"


@pytest.mark.parametrize(
    ("embedding", "retrieval"),
    [
        ({}, _retrieval_config()),
        (_embedding_config(), {}),
        (
            {
                **_embedding_config(),
                "query": {"instruction": "Instruct: {query}", "extra": True},
            },
            _retrieval_config(),
        ),
        (
            _embedding_config(),
            {**_retrieval_config(), "vector_similarity_floor": float("nan")},
        ),
        (
            _embedding_config(),
            {**_retrieval_config(), "keyword_weight": 1},
        ),
    ],
)
def test_frozen_legacy_projection_rejects_incomplete_or_invalid_rows_safely(
    embedding: object,
    retrieval: object,
) -> None:
    migration = _migration()

    with pytest.raises(ValueError, match="^Legacy search policy is invalid$"):
        migration._frozen_legacy_policy_snapshot(embedding, retrieval)


def test_migration_source_declares_append_only_database_enforcement() -> None:
    _migration()
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    assert "CREATE TRIGGER trg_search_policy_manifests_append_only" in source
    assert "BEFORE UPDATE OR DELETE ON search_policy_manifests" in source
    assert "UPDATE index_runs" not in source
    assert "UPDATE index_configs" not in source
