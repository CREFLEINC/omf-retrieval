"""Fail-closed contracts for the approved immutable search policy."""

from contextlib import contextmanager
from uuid import UUID

import pytest
from pydantic import ValidationError

from omf_retrieval.application.admin.tokens import (
    AuthenticatedClient,
    AuthorizedSource,
)
from omf_retrieval.application.search.policy import retrieval_config_snapshot
from omf_retrieval.domain.models import EmbeddingDescriptor
from omf_retrieval.infrastructure.database import search as postgres_search
from omf_retrieval.settings import Settings

AUTHORIZED = AuthorizedSource(
    AuthenticatedClient(UUID(int=1), "agent", "0123456789abcdef"), "omf"
)
DESCRIPTOR = EmbeddingDescriptor("model", "revision", 2)
KEYWORD_FLOOR = 0.03658536400000001
VECTOR_FLOOR = 0.48344050397156374


def _retrieval_config(**changes: object) -> dict[str, object]:
    config: dict[str, object] = {
        "k": 60,
        "keyword_weight": 1.0,
        "vector_weight": 1.0,
        "keyword_similarity_floor": KEYWORD_FLOOR,
        "vector_similarity_floor": VECTOR_FLOOR,
        "evidence_floor_status": "calibrated",
    }
    config.update(changes)
    return config


class _MappingResult:
    def __init__(self, row: dict[str, object]) -> None:
        self._row = row

    def mappings(self) -> "_MappingResult":
        return self

    def one_or_none(self) -> dict[str, object]:
        return self._row


class _ActiveSession:
    def __init__(
        self,
        rrf_config: dict[str, object],
        document_changes: dict[str, object] | None = None,
    ) -> None:
        self._rrf_config = rrf_config
        self._document_changes = document_changes or {}

    def scalar(self, _statement: object) -> bool:
        return True

    def execute(self, _statement: object) -> _MappingResult:
        row: dict[str, object] = {
            "run_id": UUID(int=2),
            "commit_sha": "a" * 40,
            "model_name": DESCRIPTOR.model_name,
            "model_revision": DESCRIPTOR.revision,
            "dimension": DESCRIPTOR.dimension,
            "provider": "sentence-transformers",
            "normalize_embeddings": True,
            "rrf_config": self._rrf_config,
        }
        row.update(self._document_changes)
        return _MappingResult(row)


class _Transactions:
    def __init__(
        self,
        rrf_config: dict[str, object],
        document_changes: dict[str, object] | None = None,
    ) -> None:
        self._session = _ActiveSession(rrf_config, document_changes)

    @contextmanager
    def begin(self) -> object:
        yield self._session


def _repository(
    rrf_config: dict[str, object],
    document_changes: dict[str, object] | None = None,
) -> postgres_search.PostgresHybridSearchRepository:
    return postgres_search.PostgresHybridSearchRepository(
        _Transactions(rrf_config, document_changes),  # type: ignore[arg-type]
        embedding_config_hash="a" * 64,
        embedding_provider="sentence-transformers",
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("keyword_candidate_limit", 49),
        ("vector_candidate_limit", 51),
        ("rrf_k", 61),
        ("keyword_weight", 0.5),
        ("vector_weight", 2.0),
    ],
)
def test_settings_accepts_valid_identity_bearing_search_policy(
    field: str, value: object
) -> None:
    settings = Settings(environment="test", **{field: value})

    assert settings.search_policy_snapshot().as_config()[field] == value


def test_active_index_query_loads_only_document_embedding_compatibility() -> None:
    sql = " ".join(str(postgres_search._ACTIVE_INDEX_SQL).lower().split())
    assert "rrf_config" not in sql
    assert "provider" in sql and "normalize_embeddings" in sql


def test_approved_search_policy_defaults_are_accepted() -> None:
    settings = Settings(environment="test")
    assert (
        settings.keyword_candidate_limit,
        settings.vector_candidate_limit,
        settings.rrf_k,
        settings.keyword_weight,
        settings.vector_weight,
    ) == (50, 50, 60, 1.0, 1.0)
    assert (
        settings.keyword_similarity_floor,
        settings.vector_similarity_floor,
        settings.evidence_floor_status,
    ) == (KEYWORD_FLOOR, VECTOR_FLOOR, "calibrated")


def test_calibrated_floor_values_are_exactly_identity_bearing() -> None:
    settings = Settings(
        environment="test",
        keyword_similarity_floor=KEYWORD_FLOOR,
        vector_similarity_floor=VECTOR_FLOOR,
        evidence_floor_status="calibrated",
    )

    assert retrieval_config_snapshot(settings) == _retrieval_config()


def test_evidence_floors_load_from_explicit_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OMF_RETRIEVAL_ENVIRONMENT", "test")
    monkeypatch.setenv("OMF_RETRIEVAL_KEYWORD_SIMILARITY_FLOOR", "0.03658536400000001")
    monkeypatch.setenv("OMF_RETRIEVAL_VECTOR_SIMILARITY_FLOOR", "0.48344050397156374")
    monkeypatch.setenv("OMF_RETRIEVAL_EVIDENCE_FLOOR_STATUS", "calibrated")

    settings = Settings()

    assert retrieval_config_snapshot(settings) == _retrieval_config()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("keyword_similarity_floor", -0.01),
        ("keyword_similarity_floor", 1.01),
        ("keyword_similarity_floor", float("nan")),
        ("vector_similarity_floor", float("inf")),
        ("vector_similarity_floor", 1),
        ("vector_similarity_floor", True),
    ],
)
def test_settings_reject_invalid_evidence_floor(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        Settings(environment="test", **{field: value})


@pytest.mark.parametrize(
    ("environment_name", "value"),
    [
        ("OMF_RETRIEVAL_KEYWORD_CANDIDATE_LIMIT", "49"),
        ("OMF_RETRIEVAL_VECTOR_CANDIDATE_LIMIT", "51"),
        ("OMF_RETRIEVAL_RRF_K", "61"),
        ("OMF_RETRIEVAL_KEYWORD_WEIGHT", "0.5"),
        ("OMF_RETRIEVAL_VECTOR_WEIGHT", "2.0"),
    ],
)
def test_environment_can_select_a_distinct_valid_search_policy(
    monkeypatch: pytest.MonkeyPatch,
    environment_name: str,
    value: str,
) -> None:
    monkeypatch.setenv("OMF_RETRIEVAL_ENVIRONMENT", "test")
    monkeypatch.setenv(environment_name, value)
    settings = Settings()
    field = environment_name.removeprefix("OMF_RETRIEVAL_").lower()
    assert str(settings.search_policy_snapshot().as_config()[field]) == value


@pytest.mark.parametrize(
    "persisted",
    [
        {},
        _retrieval_config(k=61),
        _retrieval_config(keyword_similarity_floor=0.2),
        _retrieval_config(vector_similarity_floor=1),
        _retrieval_config(keyword_similarity_floor=float("nan")),
        _retrieval_config(evidence_floor_status="calibration_pending"),
        {**_retrieval_config(), "unexpected": True},
    ],
)
def test_persisted_retrieval_policy_mismatch_fails_closed_safely(
    persisted: dict[str, object],
) -> None:
    with pytest.raises(postgres_search.SearchUnavailableError) as error:
        postgres_search.validate_persisted_retrieval_config(
            persisted, expected=_retrieval_config()
        )
    assert str(error.value) == "Search service is unavailable."
    assert all(str(value) not in str(error.value) for value in persisted.values())


def test_persisted_retrieval_policy_exact_match_is_accepted() -> None:
    postgres_search.validate_persisted_retrieval_config(
        _retrieval_config(), expected=_retrieval_config()
    )


def test_pending_policy_is_accepted_only_by_internal_calibration_path() -> None:
    pending = _retrieval_config(evidence_floor_status="calibration_pending")

    postgres_search.validate_persisted_retrieval_config(
        pending,
        expected=pending,
        require_calibrated=False,
    )
    with pytest.raises(postgres_search.SearchUnavailableError):
        postgres_search.validate_persisted_retrieval_config(
            pending,
            expected=pending,
        )


def test_matching_active_persisted_policy_allows_search_and_readiness() -> None:
    repository = _repository(_retrieval_config())
    assert repository.active_index(
        AUTHORIZED, DESCRIPTOR, normalize_embeddings=True
    ).run_id == UUID(int=2)
    assert (
        repository.is_ready(AUTHORIZED, DESCRIPTOR, normalize_embeddings=True) is True
    )


@pytest.mark.parametrize(
    "persisted",
    [
        {},
        _retrieval_config(k=61),
        _retrieval_config(keyword_similarity_floor=0.2),
        _retrieval_config(vector_similarity_floor=0.6),
        _retrieval_config(evidence_floor_status="calibration_pending"),
    ],
)
def test_legacy_retrieval_snapshot_difference_does_not_block_active_index(
    persisted: dict[str, object],
) -> None:
    repository = _repository(persisted)
    assert repository.active_index(
        AUTHORIZED, DESCRIPTOR, normalize_embeddings=True
    ).run_id == UUID(int=2)
    assert (
        repository.is_ready(AUTHORIZED, DESCRIPTOR, normalize_embeddings=True) is True
    )


@pytest.mark.parametrize(
    "change",
    [
        {"provider": "other"},
        {"model_name": "other"},
        {"model_revision": "other"},
        {"dimension": 3},
        {"normalize_embeddings": False},
    ],
)
def test_active_document_embedding_incompatibility_fails_closed(
    change: dict[str, object],
) -> None:
    repository = _repository(_retrieval_config(), change)

    with pytest.raises(postgres_search.SearchUnavailableError):
        repository.active_index(AUTHORIZED, DESCRIPTOR, normalize_embeddings=True)
    assert (
        repository.is_ready(AUTHORIZED, DESCRIPTOR, normalize_embeddings=True) is False
    )
