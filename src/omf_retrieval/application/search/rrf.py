"""Deterministic application-side Reciprocal Rank Fusion."""

from dataclasses import dataclass

from omf_retrieval.application.search.ports import Candidate


@dataclass(frozen=True, slots=True)
class FusedCandidate:
    """One child with its independent ranks and combined score."""

    candidate: Candidate
    keyword_rank: int | None
    vector_rank: int | None
    score: float


def reciprocal_rank_fusion(
    *,
    keyword: tuple[Candidate, ...],
    vector: tuple[Candidate, ...],
    k: int,
    keyword_weight: float,
    vector_weight: float,
) -> tuple[FusedCandidate, ...]:
    """Fuse ordered candidates with one-based ranks and a UUID tie breaker."""
    if (
        type(keyword) is not tuple
        or type(vector) is not tuple
        or type(k) is not int
        or k <= 0
        or type(keyword_weight) is not float
        or type(vector_weight) is not float
        or keyword_weight <= 0
        or vector_weight <= 0
    ):
        raise ValueError("Invalid RRF policy")
    candidates: dict[object, Candidate] = {}
    keyword_ranks: dict[object, int] = {}
    vector_ranks: dict[object, int] = {}
    for rank, candidate in enumerate(keyword, start=1):
        _remember_candidate(candidates, candidate)
        keyword_ranks[candidate.chunk_id] = rank
    for rank, candidate in enumerate(vector, start=1):
        _remember_candidate(candidates, candidate)
        vector_ranks[candidate.chunk_id] = rank

    fused = tuple(
        FusedCandidate(
            candidate=candidate,
            keyword_rank=keyword_ranks.get(chunk_id),
            vector_rank=vector_ranks.get(chunk_id),
            score=(
                keyword_weight / (k + keyword_ranks[chunk_id])
                if chunk_id in keyword_ranks
                else 0.0
            )
            + (
                vector_weight / (k + vector_ranks[chunk_id])
                if chunk_id in vector_ranks
                else 0.0
            ),
        )
        for chunk_id, candidate in candidates.items()
    )
    return tuple(
        sorted(
            fused,
            key=lambda item: (-item.score, item.candidate.chunk_id.int),
        )
    )


def _remember_candidate(
    candidates: dict[object, Candidate], candidate: Candidate
) -> None:
    if type(candidate) is not Candidate:
        raise ValueError("Invalid RRF candidate")
    existing = candidates.setdefault(candidate.chunk_id, candidate)
    if existing != candidate:
        raise ValueError("Candidate metadata changed between retrieval lanes")


__all__ = ["FusedCandidate", "reciprocal_rank_fusion"]
