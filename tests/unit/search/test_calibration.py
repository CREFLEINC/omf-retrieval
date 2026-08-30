"""Pure score-separation and fixed smoke-fixture contracts."""

import json
import runpy
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from omf_retrieval.application.indexing.config_identity import (
    document_embedding_config_hash,
)
from omf_retrieval.application.search.calibration import (
    KnownEvidenceScores,
    assess_calibration,
)
from omf_retrieval.application.search.ports import Candidate, Origin, ScoredCandidate
from omf_retrieval.settings import Settings

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
FIXTURE = REPOSITORY_ROOT / "config/smoke/omf_mvp_v2.json"
HARNESS = runpy.run_path(str(REPOSITORY_ROOT / "scripts/calibrate_search.py"))


def _collect_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    repository_error: Exception | None = None,
) -> tuple[dict[str, object], object]:
    collect = HARNESS["_collect"]
    assert callable(collect)
    globals_ = collect.__globals__
    captured: dict[str, object] = {}
    embedding_config = {
        "document": {
            "provider": "sentence-transformers",
            "model_name": "fixture/model",
            "revision": "a" * 40,
            "dimension": 1,
            "normalize_embeddings": True,
            "library_name": "sentence-transformers",
            "library_version": "5.1.0",
        },
        "query": {"instruction": "Query: {query}"},
    }
    settings = Settings(environment="test", api_token="fixture-token")

    class FakeEngine:
        def dispose(self) -> None:
            captured["disposed"] = True

    class FakeEmbeddings:
        def __init__(self, actual_settings: Settings) -> None:
            assert actual_settings is settings
            self.embedding_config_snapshot = SimpleNamespace(
                as_config=lambda: embedding_config
            )
            self.descriptor = SimpleNamespace()

        def embed_query(self, _query: str) -> tuple[float, ...]:
            return (1.0,)

    class FakeRepository:
        def __init__(self, transactions: object, **kwargs: object) -> None:
            if repository_error is not None:
                raise repository_error
            captured["transactions"] = transactions
            captured["repository_kwargs"] = kwargs

        def retrieve_for_calibration(self, *args: object, **kwargs: object) -> object:
            del args, kwargs
            return SimpleNamespace(keyword=(), vector=())

    class FakeAccess:
        def __init__(self, _repository: object) -> None:
            pass

        def execute_authorized(
            self, token: str, source_key: str, operation: object
        ) -> object:
            captured["credential"] = (token, source_key)
            return operation(SimpleNamespace())  # type: ignore[operator]

    transactions = object()
    monkeypatch.setitem(globals_, "Settings", lambda: settings)
    monkeypatch.setitem(globals_, "create_database_engine", lambda _url: FakeEngine())
    monkeypatch.setitem(
        globals_, "create_session_factory", lambda _engine: transactions
    )
    monkeypatch.setitem(globals_, "database_url_from_environment", lambda: "fixture")
    monkeypatch.setitem(
        globals_, "SentenceTransformerEmbeddingProvider", FakeEmbeddings
    )
    monkeypatch.setitem(globals_, "PostgresHybridSearchRepository", FakeRepository)
    monkeypatch.setitem(globals_, "PostgresClientRepository", lambda _value: object())
    monkeypatch.setitem(globals_, "ClientAccessService", FakeAccess)
    monkeypatch.setitem(
        globals_,
        "_load_cases",
        lambda: [
            {
                "id": "known",
                "category": "known",
                "query": "known query",
                "expected_status": "ok",
                "acceptable_evidence": [],
            },
            {
                "id": "unknown",
                "category": "unknown",
                "query": "unknown query",
                "expected_status": "no_evidence",
            },
        ],
    )
    captured["expected_embedding_hash"] = document_embedding_config_hash(
        embedding_config
    )
    return captured, collect


def test_positive_margin_is_calibratable() -> None:
    assessment = assess_calibration(
        (
            KnownEvidenceScores("keyword", 0.4, None),
            KnownEvidenceScores("vector", None, 0.8),
        ),
        unknown_keyword_max=0.2,
        unknown_vector_max=0.5,
    )

    assert assessment.calibratable is True
    assert assessment.margin == pytest.approx(0.2)


@pytest.mark.parametrize("known_score", [0.2, 0.1])
def test_zero_or_negative_margin_is_not_calibratable(known_score: float) -> None:
    assessment = assess_calibration(
        (KnownEvidenceScores("case", known_score, None),),
        unknown_keyword_max=0.2,
        unknown_vector_max=0.5,
    )

    assert assessment.calibratable is False
    assert assessment.margin is not None and assessment.margin <= 0.0


def test_missing_expected_candidate_is_not_calibratable() -> None:
    assessment = assess_calibration(
        (KnownEvidenceScores("missing", None, None),),
        unknown_keyword_max=0.2,
        unknown_vector_max=0.5,
    )

    assert assessment.calibratable is False
    assert assessment.margin is None


def test_fixed_commit_smoke_fixture_has_five_grounded_and_one_unknown_case() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    cases = payload["cases"]

    assert payload["source_key"] == "omf"
    assert payload["commit_sha"] == "a8f46f23cd3fb9c5f7042e987dff8103d23f0fa2"
    assert [case["category"] for case in cases] == [
        "기능 요구사항",
        "확정 정책·의사결정",
        "API 계약",
        "사용자 업무 흐름",
        "프로젝트 용어",
        "문서에 없는 질문",
    ]
    for case in cases[:5]:
        assert case["expected_status"] == "ok"
        assert case["acceptable_evidence"]
        for target in case["acceptable_evidence"]:
            assert target["source_path"].startswith("design/wiki/")
            assert target["source_path"].endswith(".md")
            assert target["heading_contains"]
    assert cases[5]["expected_status"] == "no_evidence"
    assert "acceptable_evidence" not in cases[5]


def _candidate(
    *, identity: int, path: str, heading: str, score: float
) -> ScoredCandidate:
    return ScoredCandidate(
        Candidate(
            UUID(int=identity),
            UUID(int=identity + 100),
            (heading,),
            "minimal evidence",
            10,
            11,
            (Origin(path, f"{identity:064x}"),),
        ),
        score,
    )


def test_direct_acceptable_evidence_in_top_five_is_selected_deterministically() -> None:
    best_acceptable = HARNESS.get("_best_acceptable")
    assert callable(best_acceptable)
    case = {
        "acceptable_evidence": [
            {"source_path": "design/wiki/direct.md", "heading_contains": "직접"},
            {"source_path": "design/wiki/also.md", "heading_contains": "대안"},
        ]
    }
    lane = (
        _candidate(
            identity=1, path="design/wiki/direct.md", heading="직접 근거", score=0.6
        ),
        _candidate(
            identity=2, path="design/wiki/also.md", heading="대안 근거", score=0.7
        ),
        _candidate(
            identity=3, path="design/wiki/direct.md", heading="직접 근거", score=0.7
        ),
    )

    assert best_acceptable(lane, case) == lane[1]


def test_unrelated_alternative_and_acceptable_below_top_five_do_not_pass_gate() -> None:
    best_acceptable = HARNESS.get("_best_acceptable")
    assert callable(best_acceptable)
    case = {
        "acceptable_evidence": [
            {"source_path": "design/wiki/direct.md", "heading_contains": "직접"}
        ]
    }
    unrelated = tuple(
        _candidate(
            identity=index,
            path="design/wiki/unrelated.md",
            heading="다른 근거",
            score=0.9 - index / 100,
        )
        for index in range(1, 6)
    )
    below_gate = _candidate(
        identity=6,
        path="design/wiki/direct.md",
        heading="직접 근거",
        score=0.5,
    )

    assert best_acceptable(unrelated, case) is None
    assert best_acceptable((*unrelated, below_gate), case) is None


def test_missing_diagnostic_target_is_a_quality_note_not_a_gate_failure() -> None:
    best_acceptable = HARNESS.get("_best_acceptable")
    quality_notes = HARNESS.get("_quality_notes")
    assert callable(best_acceptable)
    assert callable(quality_notes)
    case = {
        "acceptable_evidence": [
            {"source_path": "design/wiki/direct.md", "heading_contains": "직접"}
        ],
        "diagnostic_target": {
            "source_path": "design/wiki/ideal.md",
            "heading_contains": "이상적",
        },
    }
    direct = _candidate(
        identity=1,
        path="design/wiki/direct.md",
        heading="직접 근거",
        score=0.6,
    )

    assert best_acceptable((direct,), case) == direct
    assert quality_notes((direct,), (), case) == ("diagnostic_target_missing",)


def test_calibrated_floor_is_the_next_representable_float_above_unknown() -> None:
    strict_floor_above = HARNESS.get("_strict_floor_above")
    assert callable(strict_floor_above)

    assert strict_floor_above(0.036585364) == 0.03658536400000001
    assert strict_floor_above(0.4834405039715637) == 0.48344050397156374


def test_collect_uses_the_document_embedding_identity_for_current_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured, collect = _collect_fakes(monkeypatch)

    report = collect()

    assert report["status"] == "blocked"
    assert captured["repository_kwargs"] == {
        "embedding_config_hash": captured["expected_embedding_hash"],
        "embedding_provider": "sentence-transformers",
    }
    assert captured["credential"] == ("fixture-token", "omf")
    assert captured["disposed"] is True


def test_calibration_output_contains_minimal_raw_provenance_without_excerpt() -> None:
    candidate = ScoredCandidate(
        Candidate(
            UUID(int=1),
            UUID(int=2),
            ("정책",),
            "원문 전체를 출력하면 안 됨",
            10,
            11,
            (Origin("design/wiki/policy.md", "a" * 64),),
        ),
        0.625,
    )

    output = HARNESS["_lane_output"]((candidate,))

    assert output == [
        {
            "raw_score": 0.625,
            "line_start": 10,
            "line_end": 11,
            "origins": [
                {
                    "source_path": "design/wiki/policy.md",
                    "content_hash": "a" * 64,
                }
            ],
        }
    ]
    assert "excerpt" not in output[0]


def test_calibration_failure_output_does_not_echo_private_exception(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail() -> dict[str, object]:
        raise RuntimeError("secret-token private-host /private/source")

    main = HARNESS["main"]
    monkeypatch.setitem(main.__globals__, "_collect", fail)

    assert main() == 2
    assert capsys.readouterr().out == (
        '{"code":"calibration_unavailable","status":"unavailable"}\n'
    )


def test_repository_assembly_failure_remains_secret_safe(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _captured, _collect = _collect_fakes(
        monkeypatch,
        repository_error=RuntimeError("secret-token private-host /private/source"),
    )

    assert HARNESS["main"]() == 2
    assert capsys.readouterr().out == (
        '{"code":"calibration_unavailable","status":"unavailable"}\n'
    )
