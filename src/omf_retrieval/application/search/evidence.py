"""Build immutable parent-grouped evidence from fused child matches."""

from dataclasses import dataclass
from uuid import UUID

from omf_retrieval.application.search.ports import Origin
from omf_retrieval.application.search.rrf import FusedCandidate


@dataclass(frozen=True, slots=True)
class EvidenceMatch:
    """One source-backed matched child and its hybrid ranking details."""

    excerpt: str
    line_start: int
    line_end: int
    keyword_rank: int | None
    vector_rank: int | None
    rrf_score: float


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    """One parent section containing all selected child matches."""

    rank: int
    parent_id: UUID
    heading_path: tuple[str, ...]
    score: float
    matches: tuple[EvidenceMatch, ...]
    origins: tuple[Origin, ...]


def group_evidence(
    fused: tuple[FusedCandidate, ...], *, limit: int
) -> tuple[EvidenceItem, ...]:
    """Group children by parent, rank by the best child, then apply limit."""
    if type(fused) is not tuple or type(limit) is not int or limit <= 0:
        raise ValueError("Invalid evidence grouping input")
    groups: dict[UUID, list[FusedCandidate]] = {}
    for item in fused:
        if type(item) is not FusedCandidate:
            raise ValueError("Invalid fused candidate")
        groups.setdefault(item.candidate.parent_id, []).append(item)

    ordered_groups = sorted(
        groups.items(),
        key=lambda pair: (-pair[1][0].score, pair[0].int),
    )[:limit]
    evidence: list[EvidenceItem] = []
    for rank, (parent_id, items) in enumerate(ordered_groups, start=1):
        ordered_matches = sorted(
            items,
            key=lambda item: (-item.score, item.candidate.chunk_id.int),
        )
        first = ordered_matches[0]
        if any(
            item.candidate.heading_path != first.candidate.heading_path
            for item in ordered_matches
        ):
            raise ValueError("Parent heading metadata is inconsistent")
        origins = tuple(
            sorted(
                {
                    origin
                    for item in ordered_matches
                    for origin in item.candidate.origins
                }
            )
        )
        evidence.append(
            EvidenceItem(
                rank=rank,
                parent_id=parent_id,
                heading_path=first.candidate.heading_path,
                score=first.score,
                matches=tuple(
                    EvidenceMatch(
                        excerpt=item.candidate.excerpt,
                        line_start=item.candidate.line_start,
                        line_end=item.candidate.line_end,
                        keyword_rank=item.keyword_rank,
                        vector_rank=item.vector_rank,
                        rrf_score=item.score,
                    )
                    for item in ordered_matches
                ),
                origins=origins,
            )
        )
    return tuple(evidence)


__all__ = ["EvidenceItem", "EvidenceMatch", "group_evidence"]
