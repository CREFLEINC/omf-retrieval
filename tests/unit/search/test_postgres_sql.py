"""Static PostgreSQL search policy contracts without a database."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy.dialects import postgresql

from omf_retrieval.application.search import ActiveIndex, SearchUnavailableError
from omf_retrieval.domain.models import EmbeddingDescriptor
from omf_retrieval.infrastructure.database.search import (
    _candidate_rows,
    keyword_candidate_statement,
    vector_candidate_statement,
)


def _compiled(statement: object) -> tuple[str, dict[str, object]]:
    compiled = statement.compile(dialect=postgresql.dialect())  # type: ignore[attr-defined]
    return " ".join(str(compiled).lower().split()), compiled.params


def test_keyword_sql_prefilters_active_source_grant_and_uses_trigram_top_50() -> None:
    client_id = uuid4()
    statement = keyword_candidate_statement(
        client_id, "omf", "긴급 승인", limit=50, minimum_score=0.25
    )
    sql, params = _compiled(statement)

    assert "authorized_scope" in sql
    assert "client_source_grants" in sql and "api_clients" in sql
    assert "active_index_run_id" in sql and "index_runs" in sql
    assert "similarity(" in sql and "limit" in sql
    assert "raw_score >= %(keyword_similarity_floor)s" in sql
    assert "chunk_id" in sql
    assert client_id in params.values()
    assert "omf" in params.values()
    assert "긴급 승인" in params.values()
    assert 50 in params.values()
    assert 0.25 in params.values()


def test_vector_sql_prefilters_identically_and_uses_exact_cosine_top_50() -> None:
    client_id = uuid4()
    statement = vector_candidate_statement(
        client_id,
        "omf",
        (0.0, 1.0),
        EmbeddingDescriptor("model", "revision", 2),
        "a" * 64,
        limit=50,
        minimum_score=0.5,
    )
    sql, params = _compiled(statement)

    assert "authorized_scope" in sql
    assert "client_source_grants" in sql and "api_clients" in sql
    assert "active_index_run_id" in sql and "index_runs" in sql
    assert "<=> cast(" in sql and "limit" in sql
    assert "1.0 -" in sql and "embedding.embedding <=> cast(" in sql
    assert "raw_score >= %(vector_similarity_floor)s" in sql
    assert "hnsw" not in sql and "ivfflat" not in sql
    assert "model" in params.values() and "revision" in params.values()
    assert 2 in params.values() and 50 in params.values()
    assert 0.5 in params.values()


def test_candidate_mapping_preserves_the_exact_raw_similarity() -> None:
    active = ActiveIndex(UUID(int=1), "a" * 40)
    rows = [
        {
            "run_id": active.run_id,
            "commit_sha": active.commit_sha,
            "chunk_id": UUID(int=2),
            "parent_id": UUID(int=3),
            "heading_path": ["정책"],
            "excerpt": "근거",
            "line_start": 10,
            "line_end": 11,
            "origins": [
                {"source_path": "design/wiki/policy.md", "content_hash": "b" * 64}
            ],
            "raw_score": 0.625,
        }
    ]

    candidates = _candidate_rows(rows, active)

    assert candidates[0].raw_score == 0.625
    assert candidates[0].candidate.chunk_id == UUID(int=2)


@pytest.mark.parametrize("score", [float("nan"), float("inf"), "0.5", 1])
def test_candidate_mapping_rejects_invalid_raw_similarity(score: object) -> None:
    active = ActiveIndex(UUID(int=1), "a" * 40)
    row = {
        "run_id": active.run_id,
        "commit_sha": active.commit_sha,
        "chunk_id": UUID(int=2),
        "parent_id": UUID(int=3),
        "heading_path": ["정책"],
        "excerpt": "근거",
        "line_start": 10,
        "line_end": 11,
        "origins": [{"source_path": "design/wiki/policy.md", "content_hash": "b" * 64}],
        "raw_score": score,
    }

    with pytest.raises(SearchUnavailableError):
        _candidate_rows([row], active)
