"""Pure unit contracts for RRF and parent evidence grouping."""

from uuid import UUID

import pytest

from omf_retrieval.application.search import (
    Candidate,
    Origin,
    group_evidence,
    reciprocal_rank_fusion,
)


def _candidate(
    number: int,
    *,
    parent: int | None = None,
    origins: tuple[Origin, ...] | None = None,
) -> Candidate:
    return Candidate(
        chunk_id=UUID(int=number),
        parent_id=UUID(int=parent if parent is not None else number),
        heading_path=("정책", f"절 {parent if parent is not None else number}"),
        excerpt=f"근거 {number}",
        line_start=number,
        line_end=number + 1,
        origins=origins or (Origin(f"design/wiki/{number}.md", f"{number:064x}"),),
    )


def test_rrf_uses_one_based_rank_equal_weights_and_nullable_single_ranks() -> None:
    first = _candidate(1)
    second = _candidate(2)
    third = _candidate(3)

    fused = reciprocal_rank_fusion(
        keyword=(first, second),
        vector=(second, third),
        k=60,
        keyword_weight=1.0,
        vector_weight=1.0,
    )

    assert [item.candidate.chunk_id for item in fused] == [
        second.chunk_id,
        first.chunk_id,
        third.chunk_id,
    ]
    assert fused[0].score == pytest.approx(1 / 62 + 1 / 61)
    assert (fused[1].keyword_rank, fused[1].vector_rank) == (1, None)
    assert (fused[2].keyword_rank, fused[2].vector_rank) == (None, 2)


def test_rrf_uses_chunk_uuid_as_the_stable_tie_breaker() -> None:
    lower = _candidate(1)
    higher = _candidate(2)

    fused = reciprocal_rank_fusion(
        keyword=(higher,),
        vector=(lower,),
        k=60,
        keyword_weight=1.0,
        vector_weight=1.0,
    )

    assert [item.candidate.chunk_id for item in fused] == [
        lower.chunk_id,
        higher.chunk_id,
    ]


def test_parent_grouping_keeps_best_child_rank_matches_and_all_origins() -> None:
    duplicate_origins = (
        Origin("design/wiki/b.md", "b" * 64),
        Origin("design/wiki/a.md", "a" * 64),
    )
    first = _candidate(1, parent=10, origins=duplicate_origins)
    second = _candidate(
        2,
        parent=10,
        origins=(Origin("design/wiki/a.md", "a" * 64),),
    )
    third = _candidate(3, parent=20)
    fused = reciprocal_rank_fusion(
        keyword=(first, second, third),
        vector=(second, first, third),
        k=60,
        keyword_weight=1.0,
        vector_weight=1.0,
    )

    evidence = group_evidence(fused, limit=20)

    assert len(evidence) == 2
    assert [item.rank for item in evidence] == [1, 2]
    assert [match.excerpt for match in evidence[0].matches] == ["근거 1", "근거 2"]
    assert evidence[0].score == evidence[0].matches[0].rrf_score
    assert [
        (origin.source_path, origin.content_hash) for origin in evidence[0].origins
    ] == [
        ("design/wiki/a.md", "a" * 64),
        ("design/wiki/b.md", "b" * 64),
    ]


@pytest.mark.parametrize("limit", [1, 20])
def test_parent_grouping_applies_evidence_limit(limit: int) -> None:
    candidates = tuple(_candidate(number) for number in range(1, 22))
    fused = reciprocal_rank_fusion(
        keyword=candidates,
        vector=(),
        k=60,
        keyword_weight=1.0,
        vector_weight=1.0,
    )

    assert len(group_evidence(fused, limit=limit)) == limit
