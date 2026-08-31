"""Application search service contracts with deterministic fakes."""

import inspect
from typing import Literal
from uuid import UUID

import pytest

from omf_retrieval.application.admin.tokens import (
    AuthenticatedClient,
    AuthorizedSource,
)
from omf_retrieval.application.search import (
    ActiveIndex,
    Candidate,
    CandidateBatch,
    NoActiveIndexError,
    Origin,
    ScoredCandidate,
    SearchPolicyManifest,
    SearchResult,
    SearchService,
    SearchUnavailableError,
    validated_search_policy_snapshot,
)
from omf_retrieval.domain.models import EmbeddingDescriptor
from omf_retrieval.settings import Settings

AUTHORIZED = AuthorizedSource(
    AuthenticatedClient(UUID(int=99), "agent", "0123456789abcdef"), "omf"
)
ACTIVE = ActiveIndex(UUID(int=88), "a" * 40)
CANDIDATE = Candidate(
    chunk_id=UUID(int=1),
    parent_id=UUID(int=2),
    heading_path=("정책",),
    excerpt="직접 근거",
    line_start=10,
    line_end=12,
    origins=(Origin("design/wiki/policy.md", "b" * 64),),
)
DEFAULT_BATCH = CandidateBatch(ACTIVE, (ScoredCandidate(CANDIDATE, 0.5),), ())


def _candidate(identity: int, parent_identity: int) -> Candidate:
    return Candidate(
        chunk_id=UUID(int=identity),
        parent_id=UUID(int=parent_identity),
        heading_path=(f"정책 {parent_identity}",),
        excerpt=f"근거 {identity}",
        line_start=identity,
        line_end=identity,
        origins=(Origin(f"design/wiki/policy-{identity}.md", f"{identity:064x}"),),
    )


def _manifest(settings: Settings, policy_id: int = 7) -> SearchPolicyManifest:
    snapshot = settings.search_policy_snapshot()
    return SearchPolicyManifest(UUID(int=policy_id), snapshot.config_hash, snapshot)


class FakeProvider:
    descriptor = EmbeddingDescriptor("model", "revision", 2)

    def __init__(self, failure: Exception | None = None) -> None:
        self.failure = failure
        self.queries: list[str] = []

    def embed_query(self, query: str) -> tuple[float, ...]:
        self.queries.append(query)
        if self.failure is not None:
            raise self.failure
        return (0.0, 1.0)

    def is_ready(self) -> bool:
        return self.failure is None


class FakeRepository:
    def __init__(
        self,
        batch: CandidateBatch | Exception = DEFAULT_BATCH,
    ) -> None:
        self.batch = batch
        self.calls: list[tuple[object, ...]] = []

    def active_index(
        self,
        authorized: AuthorizedSource,
        descriptor: EmbeddingDescriptor,
        *,
        normalize_embeddings: bool,
    ) -> ActiveIndex:
        if isinstance(self.batch, Exception):
            raise self.batch
        assert authorized == AUTHORIZED and descriptor.dimension == 2
        assert normalize_embeddings is True
        return self.batch.index

    def retrieve(
        self,
        authorized: AuthorizedSource,
        query: str,
        query_vector: tuple[float, ...],
        descriptor: EmbeddingDescriptor,
        *,
        keyword_limit: int,
        vector_limit: int,
        normalize_embeddings: bool,
        keyword_similarity_floor: float,
        vector_similarity_floor: float,
    ) -> CandidateBatch:
        self.calls.append(
            (
                authorized,
                query,
                query_vector,
                descriptor,
                keyword_limit,
                vector_limit,
                normalize_embeddings,
                keyword_similarity_floor,
                vector_similarity_floor,
            )
        )
        if isinstance(self.batch, Exception):
            raise self.batch
        return self.batch

    def is_ready(
        self,
        authorized: AuthorizedSource,
        descriptor: EmbeddingDescriptor,
        *,
        normalize_embeddings: bool,
    ) -> bool:
        return (
            self.batch is not None
            and authorized == AUTHORIZED
            and descriptor.dimension == 2
            and normalize_embeddings is True
        )


def _service(
    repository: FakeRepository | None = None,
    provider: FakeProvider | None = None,
) -> SearchService:
    settings = Settings(
        environment="test",
        embedding_model_name="model",
        embedding_model_revision="revision",
        embedding_dimension=2,
        keyword_similarity_floor=0.25,
        vector_similarity_floor=0.5,
        evidence_floor_status="calibrated",
    )
    return SearchService(
        repository=repository or FakeRepository(),
        embeddings=provider or FakeProvider(),
        settings=settings,
        policy_manifest=_manifest(settings),
    )


def _search_with_relevance(
    service: SearchService,
    relevance_level: Literal["default", "strict"],
    *,
    limit: int,
) -> SearchResult:
    return service.search(
        AUTHORIZED,
        "관련 질문",
        limit=limit,
        relevance_level=relevance_level,
    )


def test_search_service_requires_one_startup_resolved_policy_manifest() -> None:
    parameters = inspect.signature(SearchService).parameters

    assert "policy_manifest" in parameters


def test_valid_search_returns_complete_ranked_evidence_and_fixed_policy() -> None:
    repository = FakeRepository()
    provider = FakeProvider()

    result = _service(repository, provider).search(AUTHORIZED, " 정책 원문 ", limit=5)

    assert (result.status, result.index) == ("ok", ACTIVE)
    assert result.search_policy.policy_id == UUID(int=7)
    assert len(result.evidence_items) == 1
    assert result.evidence_items[0].origins[0].source_path == "design/wiki/policy.md"
    assert provider.queries == ["정책 원문"]
    assert repository.calls[0][-5:] == (50, 50, True, 0.25, 0.5)


def test_no_candidates_returns_successful_no_evidence_with_active_coordinate() -> None:
    batch = CandidateBatch(ACTIVE, (), ())

    result = _service(FakeRepository(batch)).search(AUTHORIZED, "없는 질문", limit=5)

    assert result.status == "no_evidence"
    assert result.index == ACTIVE
    assert result.evidence_items == ()


@pytest.mark.parametrize(
    "batch",
    [
        CandidateBatch(ACTIVE, (ScoredCandidate(CANDIDATE, 0.249999),), ()),
        CandidateBatch(ACTIVE, (), (ScoredCandidate(CANDIDATE, 0.499999),)),
        CandidateBatch(
            ACTIVE,
            (ScoredCandidate(CANDIDATE, 0.249999),),
            (ScoredCandidate(CANDIDATE, 0.499999),),
        ),
    ],
)
def test_candidates_below_their_lane_floor_do_not_create_evidence(
    batch: CandidateBatch,
) -> None:
    result = _service(FakeRepository(batch)).search(AUTHORIZED, "무관 질문", limit=5)

    assert result.status == "no_evidence"
    assert result.evidence_items == ()


@pytest.mark.parametrize(
    "batch",
    [
        CandidateBatch(ACTIVE, (ScoredCandidate(CANDIDATE, 0.25),), ()),
        CandidateBatch(ACTIVE, (), (ScoredCandidate(CANDIDATE, 0.5),)),
        CandidateBatch(ACTIVE, (ScoredCandidate(CANDIDATE, 0.75),), ()),
        CandidateBatch(ACTIVE, (), (ScoredCandidate(CANDIDATE, 0.75),)),
    ],
)
def test_candidates_at_or_above_either_lane_floor_are_retained(
    batch: CandidateBatch,
) -> None:
    result = _service(FakeRepository(batch)).search(AUTHORIZED, "관련 질문", limit=5)

    assert result.status == "ok"
    assert len(result.evidence_items) == 1


def test_default_relevance_matches_omitted_union_behavior() -> None:
    keyword_only = _candidate(3, 30)
    vector_only = _candidate(4, 40)
    batch = CandidateBatch(
        ACTIVE,
        (ScoredCandidate(keyword_only, 0.75),),
        (ScoredCandidate(vector_only, 0.75),),
    )
    service = _service(FakeRepository(batch))

    omitted = service.search(AUTHORIZED, "관련 질문", limit=5)
    explicit = _search_with_relevance(service, "default", limit=5)

    parameter = inspect.signature(service.search).parameters.get("relevance_level")
    assert parameter is not None and parameter.default == "default"
    assert explicit == omitted
    assert len(explicit.evidence_items) == 2


def test_strict_relevance_retains_only_candidates_present_in_both_lanes() -> None:
    shared = _candidate(3, 30)
    keyword_only = _candidate(4, 40)
    vector_only = _candidate(5, 50)
    batch = CandidateBatch(
        ACTIVE,
        (
            ScoredCandidate(keyword_only, 0.9),
            ScoredCandidate(shared, 0.8),
        ),
        (
            ScoredCandidate(vector_only, 0.9),
            ScoredCandidate(shared, 0.8),
        ),
    )

    result = _search_with_relevance(_service(FakeRepository(batch)), "strict", limit=5)

    assert result.status == "ok"
    assert len(result.evidence_items) == 1
    assert result.evidence_items[0].parent_id == shared.parent_id
    assert result.evidence_items[0].matches[0].keyword_rank == 1
    assert result.evidence_items[0].matches[0].vector_rank == 1


def test_strict_relevance_applies_limit_after_intersection_filter() -> None:
    first_shared = _candidate(3, 30)
    second_shared = _candidate(4, 40)
    keyword_only = _candidate(5, 50)
    batch = CandidateBatch(
        ACTIVE,
        (
            ScoredCandidate(keyword_only, 0.95),
            ScoredCandidate(first_shared, 0.9),
            ScoredCandidate(second_shared, 0.8),
        ),
        (
            ScoredCandidate(first_shared, 0.9),
            ScoredCandidate(second_shared, 0.8),
        ),
    )

    result = _search_with_relevance(_service(FakeRepository(batch)), "strict", limit=1)

    assert len(result.evidence_items) == 1
    assert result.evidence_items[0].parent_id == first_shared.parent_id


def test_strict_relevance_without_shared_candidates_returns_no_evidence() -> None:
    keyword_only = _candidate(3, 30)
    vector_only = _candidate(4, 40)
    batch = CandidateBatch(
        ACTIVE,
        (ScoredCandidate(keyword_only, 0.75),),
        (ScoredCandidate(vector_only, 0.75),),
    )

    result = _search_with_relevance(_service(FakeRepository(batch)), "strict", limit=5)

    assert result.status == "no_evidence"
    assert result.evidence_items == ()


def test_strict_relevance_intersects_candidates_after_lane_floors() -> None:
    shared_before_floors = _candidate(3, 30)
    batch = CandidateBatch(
        ACTIVE,
        (ScoredCandidate(shared_before_floors, 0.249999),),
        (ScoredCandidate(shared_before_floors, 0.75),),
    )

    result = _search_with_relevance(_service(FakeRepository(batch)), "strict", limit=5)

    assert result.status == "no_evidence"
    assert result.evidence_items == ()


def test_calibration_pending_fails_search_and_readiness_closed() -> None:
    repository = FakeRepository()
    settings = Settings(
        environment="test",
        embedding_model_name="model",
        embedding_model_revision="revision",
        embedding_dimension=2,
        keyword_similarity_floor=1.0,
        vector_similarity_floor=1.0,
        evidence_floor_status="calibration_pending",
    )
    service = SearchService(
        repository=repository,
        embeddings=FakeProvider(),
        settings=settings,
        policy_manifest=_manifest(settings),
    )

    with pytest.raises(SearchUnavailableError):
        service.search(AUTHORIZED, "질문", limit=5)
    assert service.is_ready(AUTHORIZED) is False
    assert repository.calls == []


@pytest.mark.parametrize("query", ["", "   "])
def test_blank_query_is_rejected_before_embedding(query: str) -> None:
    provider = FakeProvider()
    with pytest.raises(ValueError, match="query"):
        _service(provider=provider).search(AUTHORIZED, query, limit=5)
    assert provider.queries == []


@pytest.mark.parametrize("limit", [0, 21, True])
def test_invalid_limit_is_rejected(limit: object) -> None:
    with pytest.raises(ValueError, match="limit"):
        _service().search(AUTHORIZED, "질문", limit=limit)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "failure",
    [NoActiveIndexError(), SearchUnavailableError(), RuntimeError("private-host")],
)
def test_repository_failures_are_safe_and_precede_embedding(
    failure: Exception,
) -> None:
    provider = FakeProvider()
    expected = (
        type(failure)
        if isinstance(failure, (NoActiveIndexError, SearchUnavailableError))
        else SearchUnavailableError
    )
    with pytest.raises(expected):
        _service(FakeRepository(failure), provider).search(AUTHORIZED, "질문", limit=5)
    assert provider.queries == []


def test_embedding_failure_becomes_safe_unavailable_error() -> None:
    with pytest.raises(SearchUnavailableError) as error:
        _service(provider=FakeProvider(RuntimeError("private-model-path"))).search(
            AUTHORIZED, "질문", limit=5
        )
    assert "private" not in str(error.value)


def test_readiness_requires_database_active_index_and_model() -> None:
    service = _service()
    assert service.is_ready(AUTHORIZED) is True
    assert (
        _service(provider=FakeProvider(RuntimeError("down"))).is_ready(AUTHORIZED)
        is False
    )


def test_floor_change_reuses_active_run_and_changes_only_policy_result() -> None:
    batch = CandidateBatch(ACTIVE, (), (ScoredCandidate(CANDIDATE, 0.6),))
    low_settings = Settings(
        environment="test",
        embedding_model_name="model",
        embedding_model_revision="revision",
        embedding_dimension=2,
        vector_similarity_floor=0.5,
    )
    high_settings = low_settings.model_copy(update={"vector_similarity_floor": 0.7})
    repository = FakeRepository(batch)
    low = SearchService(
        repository=repository,
        embeddings=FakeProvider(),
        settings=low_settings,
        policy_manifest=_manifest(low_settings, 11),
    ).search(AUTHORIZED, "질문", limit=5)
    high = SearchService(
        repository=repository,
        embeddings=FakeProvider(),
        settings=high_settings,
        policy_manifest=_manifest(high_settings, 12),
    ).search(AUTHORIZED, "질문", limit=5)

    assert (low.status, high.status) == ("ok", "no_evidence")
    assert low.index == high.index == ACTIVE
    assert low.search_policy.policy_id != high.search_policy.policy_id
    assert low.search_policy.config_hash != high.search_policy.config_hash


def test_resolved_manifest_supplies_candidates_floors_and_rrf_behavior() -> None:
    settings = Settings(
        environment="test",
        embedding_model_name="model",
        embedding_model_revision="revision",
        embedding_dimension=2,
        query_instruction="A different instruction: {query}",
        keyword_candidate_limit=40,
        vector_candidate_limit=45,
        rrf_k=70,
        keyword_weight=2.0,
        vector_weight=3.0,
        keyword_similarity_floor=0.4,
        vector_similarity_floor=0.8,
    )
    repository = FakeRepository()
    result = SearchService(
        repository=repository,
        embeddings=FakeProvider(),
        settings=settings,
        policy_manifest=_manifest(settings),
    ).search(AUTHORIZED, "질문", limit=5)

    assert repository.calls[0][-5:] == (40, 45, True, 0.4, 0.8)
    assert result.evidence_items[0].score == pytest.approx(2.0 / 71.0)


@pytest.mark.parametrize(
    "change",
    [
        {"query_embedding_model_name": "other"},
        {"query_embedding_revision": "other"},
        {"query_embedding_dimension": 3},
    ],
)
def test_query_embedding_descriptor_mismatch_fails_safely(
    change: dict[str, object],
) -> None:
    settings = Settings(
        environment="test",
        embedding_model_name="model",
        embedding_model_revision="revision",
        embedding_dimension=2,
    )
    config = settings.search_policy_snapshot().as_config()
    config.update(change)
    snapshot = validated_search_policy_snapshot(config)
    manifest = SearchPolicyManifest(UUID(int=21), snapshot.config_hash, snapshot)
    service = SearchService(
        repository=FakeRepository(),
        embeddings=FakeProvider(),
        settings=settings,
        policy_manifest=manifest,
    )

    with pytest.raises(
        SearchUnavailableError, match="^Search service is unavailable.$"
    ):
        service.search(AUTHORIZED, "질문", limit=5)
    assert service.is_ready(AUTHORIZED) is False


def test_runtime_settings_drift_from_resolved_manifest_fails_closed() -> None:
    settings = Settings(
        environment="test",
        embedding_model_name="model",
        embedding_model_revision="revision",
        embedding_dimension=2,
    )
    configured = settings.search_policy_snapshot()
    changed = validated_search_policy_snapshot({**configured.as_config(), "rrf_k": 61})
    service = SearchService(
        repository=FakeRepository(),
        embeddings=FakeProvider(),
        settings=settings,
        policy_manifest=SearchPolicyManifest(
            UUID(int=31), changed.config_hash, changed
        ),
    )

    with pytest.raises(SearchUnavailableError):
        service.search(AUTHORIZED, "질문", limit=5)
    assert service.is_ready(AUTHORIZED) is False
