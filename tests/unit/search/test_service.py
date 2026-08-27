"""Application search service contracts with deterministic fakes."""

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
    SearchService,
    SearchUnavailableError,
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
    ) -> ActiveIndex:
        if isinstance(self.batch, Exception):
            raise self.batch
        assert authorized == AUTHORIZED and descriptor.dimension == 2
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
    ) -> bool:
        return (
            self.batch is not None
            and authorized == AUTHORIZED
            and descriptor.dimension == 2
        )


def _service(
    repository: FakeRepository | None = None,
    provider: FakeProvider | None = None,
) -> SearchService:
    return SearchService(
        repository=repository or FakeRepository(),
        embeddings=provider or FakeProvider(),
        settings=Settings(
            environment="test",
            embedding_dimension=2,
            keyword_similarity_floor=0.25,
            vector_similarity_floor=0.5,
            evidence_floor_status="calibrated",
        ),
    )


def test_valid_search_returns_complete_ranked_evidence_and_fixed_policy() -> None:
    repository = FakeRepository()
    provider = FakeProvider()

    result = _service(repository, provider).search(AUTHORIZED, " 정책 원문 ", limit=5)

    assert (result.status, result.index) == ("ok", ACTIVE)
    assert len(result.evidence_items) == 1
    assert result.evidence_items[0].origins[0].source_path == "design/wiki/policy.md"
    assert provider.queries == ["정책 원문"]
    assert repository.calls[0][-4:] == (50, 50, 0.25, 0.5)


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


def test_calibration_pending_fails_search_and_readiness_closed() -> None:
    repository = FakeRepository()
    service = SearchService(
        repository=repository,
        embeddings=FakeProvider(),
        settings=Settings(
            environment="test",
            embedding_dimension=2,
            keyword_similarity_floor=1.0,
            vector_similarity_floor=1.0,
            evidence_floor_status="calibration_pending",
        ),
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
