"""Emit secret-safe raw-score evidence for the internal MVP smoke calibration."""

from __future__ import annotations

import json
from math import inf, isfinite, nextafter
from pathlib import Path
from typing import Any

from omf_retrieval.application.admin.service import ClientAccessService
from omf_retrieval.application.indexing.config_identity import (
    document_embedding_config_hash,
)
from omf_retrieval.application.search.calibration import (
    KnownEvidenceScores,
    assess_calibration,
)
from omf_retrieval.application.search.policy import retrieval_config_snapshot
from omf_retrieval.application.search.ports import CandidateBatch, ScoredCandidate
from omf_retrieval.infrastructure.database.repository_auth import (
    PostgresClientRepository,
)
from omf_retrieval.infrastructure.database.search import (
    PostgresHybridSearchRepository,
)
from omf_retrieval.infrastructure.database.session import (
    create_database_engine,
    create_session_factory,
)
from omf_retrieval.infrastructure.embedding.sentence_transformer import (
    SentenceTransformerEmbeddingProvider,
)
from omf_retrieval.interfaces.api.runtime import database_url_from_environment
from omf_retrieval.settings import Settings

FIXTURE = Path(__file__).resolve().parents[1] / "config/smoke/omf_mvp_v2.json"
TOP_EVIDENCE_PER_LANE = 5


def _load_cases() -> list[dict[str, Any]]:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    if type(payload) is not dict or type(payload.get("cases")) is not list:
        raise ValueError("Invalid smoke fixture")
    return payload["cases"]


def _matches_target(item: ScoredCandidate, target: object) -> bool:
    if type(target) is not dict:
        return False
    expected_path = target.get("source_path")
    expected_heading = target.get("heading_contains")
    return (
        type(expected_path) is str
        and any(
            origin.source_path == expected_path for origin in item.candidate.origins
        )
        and (
            expected_heading is None
            or (
                type(expected_heading) is str
                and any(
                    expected_heading in heading
                    for heading in item.candidate.heading_path
                )
            )
        )
    )


def _best_acceptable(
    lane: tuple[ScoredCandidate, ...], case: dict[str, Any]
) -> ScoredCandidate | None:
    targets = case.get("acceptable_evidence")
    if type(targets) is not list or not targets:
        return None
    matches = [
        item
        for item in lane[:TOP_EVIDENCE_PER_LANE]
        if any(_matches_target(item, target) for target in targets)
    ]
    # Python's max preserves lane order for equal scores, making ties reproducible.
    return max(matches, key=lambda item: item.raw_score, default=None)


def _quality_notes(
    keyword: tuple[ScoredCandidate, ...],
    vector: tuple[ScoredCandidate, ...],
    case: dict[str, Any],
) -> tuple[str, ...]:
    target = case.get("diagnostic_target")
    if target is None:
        return ()
    visible = (*keyword[:TOP_EVIDENCE_PER_LANE], *vector[:TOP_EVIDENCE_PER_LANE])
    if any(_matches_target(item, target) for item in visible):
        return ()
    return ("diagnostic_target_missing",)


def _strict_floor_above(score: float) -> float:
    """Return the smallest binary float that excludes one observed maximum."""
    if type(score) is not float or not isfinite(score) or not 0.0 <= score < 1.0:
        raise ValueError("Invalid calibration maximum")
    return nextafter(score, inf)


def _candidate_output(item: ScoredCandidate) -> dict[str, object]:
    return {
        "raw_score": item.raw_score,
        "line_start": item.candidate.line_start,
        "line_end": item.candidate.line_end,
        "origins": [
            {
                "source_path": origin.source_path,
                "content_hash": origin.content_hash,
            }
            for origin in item.candidate.origins
        ],
    }


def _lane_output(lane: tuple[ScoredCandidate, ...]) -> list[dict[str, object]]:
    return [_candidate_output(item) for item in lane[:TOP_EVIDENCE_PER_LANE]]


def _collect() -> dict[str, object]:
    settings = Settings()
    if settings.api_token is None:
        raise ValueError("Calibration credential is unavailable")
    engine = create_database_engine(database_url_from_environment())
    try:
        transactions = create_session_factory(engine)
        embeddings = SentenceTransformerEmbeddingProvider(settings)
        repository = PostgresHybridSearchRepository(
            transactions,
            embedding_config_hash=(
                document_embedding_config_hash(
                    embeddings.embedding_config_snapshot.as_config()
                )
            ),
            retrieval_config=retrieval_config_snapshot(settings),
        )
        access = ClientAccessService(PostgresClientRepository(transactions))
        cases = _load_cases()

        def run(authorized: object) -> dict[str, object]:
            observations: list[dict[str, object]] = []
            known: list[KnownEvidenceScores] = []
            unknown_batch: CandidateBatch | None = None
            for case in cases:
                query = case["query"]
                query_vector = embeddings.embed_query(query)
                batch = repository.retrieve_for_calibration(
                    authorized,  # type: ignore[arg-type]
                    query,
                    query_vector,
                    embeddings.descriptor,
                    keyword_limit=settings.keyword_candidate_limit,
                    vector_limit=settings.vector_candidate_limit,
                )
                acceptable_keyword = _best_acceptable(batch.keyword, case)
                acceptable_vector = _best_acceptable(batch.vector, case)
                observations.append(
                    {
                        "case_id": case["id"],
                        "category": case["category"],
                        "query": query,
                        "keyword": _lane_output(batch.keyword),
                        "vector": _lane_output(batch.vector),
                        "acceptable": {
                            "keyword": (
                                _candidate_output(acceptable_keyword)
                                if acceptable_keyword is not None
                                else None
                            ),
                            "vector": (
                                _candidate_output(acceptable_vector)
                                if acceptable_vector is not None
                                else None
                            ),
                        },
                        "quality_notes": _quality_notes(
                            batch.keyword, batch.vector, case
                        ),
                    }
                )
                if case["expected_status"] == "no_evidence":
                    unknown_batch = batch
                else:
                    known.append(
                        KnownEvidenceScores(
                            case["id"],
                            (
                                acceptable_keyword.raw_score
                                if acceptable_keyword is not None
                                else None
                            ),
                            (
                                acceptable_vector.raw_score
                                if acceptable_vector is not None
                                else None
                            ),
                        )
                    )
            if unknown_batch is None:
                raise ValueError("Unknown smoke case is unavailable")
            unknown_keyword_max = max(
                (item.raw_score for item in unknown_batch.keyword), default=0.0
            )
            unknown_vector_max = max(
                (item.raw_score for item in unknown_batch.vector), default=0.0
            )
            assessment = assess_calibration(
                tuple(known),
                unknown_keyword_max=unknown_keyword_max,
                unknown_vector_max=unknown_vector_max,
            )
            return {
                "status": "calibratable" if assessment.calibratable else "blocked",
                "assessment": {
                    "positive_margin": assessment.calibratable,
                    "margin": assessment.margin,
                    "keyword_floor_lower_bound_exclusive": unknown_keyword_max,
                    "vector_floor_lower_bound_exclusive": unknown_vector_max,
                    "keyword_floor": (
                        _strict_floor_above(unknown_keyword_max)
                        if assessment.calibratable
                        else None
                    ),
                    "vector_floor": (
                        _strict_floor_above(unknown_vector_max)
                        if assessment.calibratable
                        else None
                    ),
                },
                "observations": observations,
            }

        return access.execute_authorized(
            settings.api_token.get_secret_value(), "omf", run
        )
    finally:
        engine.dispose()


def main() -> int:
    """Print one finite JSON report and return nonzero when separation fails."""
    try:
        report = _collect()
        output = json.dumps(
            report,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        print('{"code":"calibration_unavailable","status":"unavailable"}')
        return 2
    print(output)
    return 0 if report["status"] == "calibratable" else 2


if __name__ == "__main__":
    raise SystemExit(main())
