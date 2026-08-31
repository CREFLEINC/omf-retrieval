"""Static contracts for the procedural OMF shared-deployment harness."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".agents/skills/development-workflow/SKILL.md"
REFERENCE = (
    ROOT / ".agents/skills/development-workflow/references/deployment-harness.md"
)
CLAUDE = ROOT / "CLAUDE.md"


def _read(path: Path) -> str:
    assert path.is_file(), f"required deployment harness file is missing: {path}"
    return path.read_text(encoding="utf-8")


def _frontmatter(text: str) -> dict[str, str]:
    assert text.startswith("---\n"), "deployment reference needs frontmatter"
    _, raw, _ = text.split("---", 2)
    values: dict[str, str] = {}
    for line in raw.strip().splitlines():
        key, separator, value = line.partition(":")
        assert separator == ":", f"invalid frontmatter line: {line}"
        values[key.strip()] = value.strip().strip('"')
    return values


def _assert_in_order(text: str, terms: tuple[str, ...]) -> None:
    positions = [text.find(term) for term in terms]
    assert -1 not in positions, f"missing ordered deployment term: {terms}"
    assert positions == sorted(positions), f"deployment terms out of order: {terms}"


def _section(text: str, heading: str) -> str:
    match = re.search(
        rf"^## {re.escape(heading)}\n(?P<body>.*?)(?=^## |\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    assert match is not None, f"missing deployment section: {heading}"
    return match.group("body")


def test_actionable_deployment_triggers_mandatorily_load_the_reference() -> None:
    workflow = _read(WORKFLOW)
    reference = _read(REFERENCE)
    relative_reference = "references/deployment-harness.md"

    assert relative_reference in workflow
    assert re.search(
        r"배포.{0,30}재배포.{0,30}복구.{0,30}보정.{0,100}반드시.{0,100}"
        r"deployment-harness\.md",
        workflow,
        re.DOTALL,
    )
    for trigger in ("배포", "재배포", "복구", "보정"):
        assert trigger in reference
    assert "실행형 요청" in reference
    assert "상태 확인" in reference
    assert "개념 설명" in reference
    assert "선택지만" in reference


def test_reference_metadata_and_fixed_coordinates_are_explicit() -> None:
    reference = _read(REFERENCE)
    metadata = _frontmatter(reference)

    assert metadata["author"] == "Codex — 사용자 승인 반영"
    assert metadata["version"] == "v1.3"
    assert metadata["audience"] == "프로젝트 관련자"
    assert re.fullmatch(
        r"2026-08-31 [0-2][0-9]:[0-5][0-9] KST", metadata["modified_at"]
    )
    for coordinate in (
        "phoebe-onpremise-test",
        "/opt/omf-retrieval",
        "192.168.1.185:9090",
        "Ubuntu 22.04",
        "RTX 4090",
        "cuda:0",
        "/home/storage_disk3/omf-retrieval-disk",
        "a8f46f23cd3fb9c5f7042e987dff8103d23f0fa2",
        "Qwen/Qwen3-Embedding-0.6B",
        "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3",
        "427f2c4a-ab06-486a-9801-4bde3ef17d63",
        "158",
        "4,202",
        "5,584",
    ):
        assert coordinate in reference
    assert "용어" in reference


def test_normal_flow_is_ordered_and_separates_execution_from_verification() -> None:
    reference = _read(REFERENCE)
    flow = _section(reference, "정상 실행 순서")

    _assert_in_order(
        flow,
        (
            "승인 컨텍스트",
            "읽기 전용 preflight",
            "상태 snapshot",
            "서버 image build",
            "additive 0003 migration",
            "active run/count invariants",
            "CUDA raw calibration",
            "search policy apply",
            "internal validation",
            "6개 smoke",
            "security",
            "restart",
            "publish",
        ),
    )
    assert "실행 Agent" in reference
    assert "검증 Agent" in reference
    assert "별도 인스턴스" in reference
    assert "단계별 사용자 확인" in reference


def test_preflight_preserves_existing_assets_and_fails_before_mutation() -> None:
    reference = _read(REFERENCE)

    for preserved in (
        "model cache",
        "local-agent",
        "deployment token",
        "active run",
        "embedding",
    ):
        assert preserved in reference
    for drift in ("SHA drift", "GPU drift", "secret mode drift", "count drift"):
        assert drift in reference
    assert re.search(
        r"preflight.{0,200}실패.{0,200}(build|migration|publish).{0,80}실행하지 않",
        reference,
        re.DOTALL,
    )
    assert "값을 읽거나 출력하지 않는다" in reference
    assert "승인된 절대경로" in reference
    assert "0600" in reference


def test_forbidden_commands_and_destructive_data_actions_are_explicit() -> None:
    reference = _read(REFERENCE)

    for forbidden in (
        "omf-retrieval index",
        "omf-retrieval client create",
        "alembic downgrade",
        "docker compose down -v",
    ):
        assert forbidden in reference
    assert "volume 삭제" in reference
    assert "data 삭제" in reference
    assert "재색인" in reference
    assert "재발급" in reference


def test_calibration_and_smoke_failures_cannot_publish() -> None:
    reference = _read(REFERENCE)

    assert "calibration separation 실패" in reference
    assert "migration 실패" in reference
    assert "smoke 실패" in reference
    assert re.search(
        r"(calibration|smoke).{0,180}실패.{0,180}publish 금지",
        reference,
        re.DOTALL,
    )
    assert "외부 listener" in reference
    assert "열지" in reference


def test_rollback_is_api_only_and_preserves_index_data() -> None:
    reference = _read(REFERENCE)

    _assert_in_order(
        reference,
        ("이전 image", "이전 policy 환경설정", "API-only", "API만 재시작"),
    )
    assert "active run과 embedding을 보존" in reference
    assert "DB downgrade" in reference


def test_reference_records_independently_verified_shared_deployment() -> None:
    reference = _read(REFERENCE)

    for completed_gate in (
        "calibration code/image packaging",
        "Compose policy injection",
        "server preflight",
        "server build",
        "server migration",
        "server CUDA calibration",
        "server smoke",
        "security/restart",
        "publish",
        "PASS",
    ):
        assert completed_gate in reference
    for deployed_coordinate in (
        "6a211448d156bf5381277cf6f183ac19ccc94b0f",
        "sha256:8a8e684aabb28e43ca3b599fa8c6780a6f7c2350ce2bc91ad31976b2267041e6",
        "0003_search_policy_manifest",
        "10e86bbd-55b9-457c-9f73-0ca29d09625b",
        "b1758182ed1bef3f87017cd5db45aa0e9829e785004545ddf48a9bc2be4b21bb",
        "3·4·1·1·2",
        "no_evidence",
        "25",
        "42",
        "running/healthy",
    ):
        assert deployed_coordinate in reference
    current_gate = _section(reference, "현재 corrective gate 상태")
    current_gate_table = current_gate.split("\n\n", maxsplit=1)[0]
    assert "NOT RUN" not in current_gate_table
    assert "API 중단" not in current_gate_table
    assert "publish 금지" not in current_gate_table
    assert "독립 검증" in current_gate
    assert "공개 유지 가능" in current_gate
    assert "constructor mismatch 상태" not in reference


def test_secret_values_are_never_canonicalized_or_logged() -> None:
    reference = _read(REFERENCE)

    for known_path in (
        "/opt/omf-retrieval/.env",
        "/opt/omf-retrieval/secrets/postgres_password",
        "/opt/omf-retrieval/secrets/audit_hmac_key",
    ):
        assert known_path in reference
    for forbidden_value_shape in (
        r"postgresql(?:\+psycopg)?://",
        r"omfr_[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+",
        r"Bearer\s+[A-Za-z0-9._-]+",
        r"(?:password|token|secret)\s*[=:]\s*[^<\s`]+",
    ):
        assert re.search(forbidden_value_shape, reference, re.IGNORECASE) is None
    assert "DB URL" in reference
    assert "값" in reference
    assert "출력하지 않는다" in reference


def test_command_scoped_credentials_cannot_remain_in_the_parent_environment() -> None:
    reference = _read(REFERENCE)
    boundary = _section(reference, "비밀정보 경계")

    for contract_term in (
        "command-scoped subshell",
        "set +x",
        "set -u",
        "set -e",
        "명시적",
        "|| exit 64",
        "fail-closed",
        "nonzero",
        "parent shell",
        "정상 종료",
        "명령 실패",
        "interrupt",
        "command environment",
    ):
        assert contract_term in boundary
    assert re.search(
        r"token.*command-scoped subshell.*parent shell",
        boundary,
        re.DOTALL,
    )
    assert "실제 경로" not in boundary


def test_claude_pointer_stays_thin_and_no_commands_are_created() -> None:
    claude = _read(CLAUDE)

    assert claude.startswith("@AGENTS.md\n")
    assert "deployment-harness.md" in claude
    assert "2026-08-30" in claude
    assert len(claude.splitlines()) < 30
    assert not (ROOT / ".claude/commands").exists()
