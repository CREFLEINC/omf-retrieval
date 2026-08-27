"""Repository harness contract tests."""

from __future__ import annotations

import hashlib
import re
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
COMMON_SKILL = Path(".agents/skills/development-workflow/SKILL.md")
PLAN_TEMPLATE = Path(
    ".agents/skills/development-workflow/references/plan-report-template.md"
)
VERIFICATION_TEMPLATE = Path(
    ".agents/skills/development-workflow/references/verification-report-template.md"
)
TRIGGER_EVALUATION = Path(
    ".agents/skills/development-workflow/references/trigger-evaluation.md"
)
DRY_RUN_SCENARIOS = Path(
    ".agents/skills/development-workflow/references/dry-run-scenarios.md"
)

REQUIRED_FILES = (
    Path("AGENTS.md"),
    Path("CLAUDE.md"),
    COMMON_SKILL,
    PLAN_TEMPLATE,
    VERIFICATION_TEMPLATE,
    TRIGGER_EVALUATION,
    DRY_RUN_SCENARIOS,
    Path(".codex/agents/task-executor.toml"),
    Path(".codex/agents/task-verifier.toml"),
    Path(".claude/agents/task-executor.md"),
    Path(".claude/agents/task-verifier.md"),
    Path(".claude/skills/development-workflow/SKILL.md"),
)

PRESERVED_AGENTS_SECTION_HASHES = {
    "프로젝트 목표와 경계": (
        "62ba720e25d07aa9e08d3764f7c7ea0ca0e119904ae344fa491c5da942043410"
    ),
    "단계별 공동 진행": (
        "fc386a5312a7842a2c52e97ef655ba08c622e8e2fbf917284426065ed32f932b"
    ),
    "사용자 승인 필수 의사결정": (
        "84e3565fb83eb843777eea3410a5018f512512dc0cf0a04ec8cb9ab94887aae1"
    ),
    "문서 작성": ("80914913fe266066d43a6cac6ffa748aee67bd526ef1be309e2a4aa4d1376f35"),
    "Agent 조회 동작": (
        "1d6dd71cd362f9989e0a88b2d9ed1d1e9b729c72562c64df802f617f58636618"
    ),
    "확정된 MVP 설계 결정": (
        "3b9eaa8da5f09d8594de54f4a495b30fbe613ad328dae90ed8b5a7e11d46e2e7"
    ),
    "내부 모델 서버 현황": (
        "588b399157714c854352e4ec617fec4757452e52353e61f6d764aadb5882318a"
    ),
    "보안과 데이터 취급": (
        "e139a83e13b9302f8e5108a4055e0140cf3bc51a6a44b93f6536eeaeb16e02bb"
    ),
}

AGENT_TOML_PATTERN = re.compile(
    r'name = "(?P<name>[^"\n]+)"\n'
    r'description = "(?P<description>[^"\n]+)"\n'
    r'sandbox_mode = "(?P<sandbox_mode>workspace-write|read-only)"\n'
    r'developer_instructions = """\n'
    r'(?P<developer_instructions>(?:(?!""").)*)\n'
    r'"""\n?',
    re.DOTALL,
)


def parse_limited_agent_toml(text: str) -> dict[str, str]:
    """Consume the repository's complete, intentionally limited agent schema."""
    match = AGENT_TOML_PATTERN.fullmatch(text)
    if match is None:
        raise ValueError("agent TOML does not match the complete limited schema")
    return match.groupdict()


class HarnessContractTest(unittest.TestCase):
    """Verify the shared policy and thin platform adapters."""

    def _read(self, relative_path: Path) -> str:
        path = REPOSITORY_ROOT / relative_path
        self.assertTrue(path.is_file(), f"required harness file is missing: {path}")
        return path.read_text(encoding="utf-8")

    def _frontmatter(self, relative_path: Path) -> tuple[dict[str, str], str]:
        text = self._read(relative_path)
        self.assertTrue(
            text.startswith("---\n"), f"missing frontmatter: {relative_path}"
        )
        _, raw_frontmatter, body = text.split("---", 2)
        values: dict[str, str] = {}
        for line in raw_frontmatter.strip().splitlines():
            key, separator, value = line.partition(":")
            self.assertEqual(separator, ":", f"invalid frontmatter: {relative_path}")
            values[key.strip()] = value.strip().strip('"')
        return values, body

    def _section_bullets(self, text: str, heading: str) -> list[str]:
        pattern = rf"^## {re.escape(heading)}\n(?P<body>.*?)(?=^## |\Z)"
        match = re.search(pattern, text, flags=re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(match, f"missing section: {heading}")
        return [
            line
            for line in match.group("body").splitlines()
            if re.match(r"^- `.+`$", line)
        ]

    def _section(self, text: str, heading: str, level: int = 2) -> str:
        marker = "#" * level
        pattern = rf"^{marker} {re.escape(heading)}\n.*?(?=^{marker} |\Z)"
        match = re.search(pattern, text, re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(match, f"missing section: {heading}")
        return match.group(0)

    def _assert_terms_in_order(self, text: str, terms: tuple[str, ...]) -> None:
        positions = [text.find(term) for term in terms]
        self.assertNotIn(-1, positions, f"missing ordered term in: {terms}")
        self.assertEqual(positions, sorted(positions), f"terms out of order: {terms}")

    def _assert_test_design_branch(self, text: str) -> None:
        self.assertRegex(
            text,
            re.compile(r"테스트 설계가 있으면.{0,80}그대로 사용", re.DOTALL),
        )
        self.assertRegex(
            text,
            re.compile(
                r"테스트 관련 내용이 없을 때만.{0,100}"
                r"스스로.{0,40}테스트 코드",
                re.DOTALL,
            ),
        )

    def test_all_required_harness_files_exist(self) -> None:
        missing = [
            str(path)
            for path in REQUIRED_FILES
            if not (REPOSITORY_ROOT / path).is_file()
        ]
        self.assertEqual(missing, [], f"missing harness files: {missing}")

    def test_agents_md_is_current_canonical_policy(self) -> None:
        text = self._read(Path("AGENTS.md"))

        self.assertNotIn("## 아직 확정하지 않은 항목", text)
        self.assertNotIn("## 새 세션의 재개 지점", text)
        self.assertIn(
            "docs/design/2026-08-13-omf-retrieval-mvp-system-design.html", text
        )
        self.assertIn(
            "docs/superpowers/plans/2026-08-13-omf-retrieval-mvp-implementation.md",
            text,
        )

    def test_preserved_agents_sections_require_explicit_hash_update_for_changes(
        self,
    ) -> None:
        text = self._read(Path("AGENTS.md"))

        # These baseline hashes make intentional policy edits visible. Update a digest
        # only together with an explicitly approved change to the preserved section.
        for heading, expected_hash in PRESERVED_AGENTS_SECTION_HASHES.items():
            section = self._section(text, heading)
            actual_hash = hashlib.sha256(section.encode("utf-8")).hexdigest()
            self.assertEqual(
                actual_hash, expected_hash, f"preserved section: {heading}"
            )

    def test_shared_policy_defines_approval_slicing_and_independent_verification(
        self,
    ) -> None:
        text = self._read(Path("AGENTS.md"))

        plan_fields = ("주제", "목적", "내용", "기대 결과", "검증 방법")
        for plan_field in plan_fields:
            self.assertIn(plan_field, text)
        slicing = self._section(text, "작업 분할 우선순위", level=3)
        self._assert_terms_in_order(
            slicing,
            (
                "Single Intent",
                "Backward Compatibility & Risk Isolation",
                "Independent Testability",
                "Layered Slicing",
                "400줄",
                "500줄",
            ),
        )

        self.assertIn("최상위 영향 컴포넌트", text)
        self.assertIn("별도 인스턴스", text)
        self.assertIn("외부 의존성 없는 쉬운 검증", text)
        self.assertIn("계획된 모든 검증", text)
        self.assertIn("TDD", text)

    def test_plan_report_requires_unit_tests_evaluation_and_term_explanations(
        self,
    ) -> None:
        agents = self._read(Path("AGENTS.md"))
        plan = self._read(PLAN_TEMPLATE)

        for text in (agents, plan):
            self.assertRegex(
                text,
                re.compile(r"코드 개발.{0,80}Unit test 설계", re.DOTALL),
            )
            self.assertRegex(
                text,
                re.compile(
                    r"객관적 지표.{0,100}어려.{0,60}정성적 평가 기준",
                    re.DOTALL,
                ),
            )
            self.assertRegex(
                text,
                re.compile(
                    r"약어.{0,20}은어.{0,80}다른 문서"
                    r".{0,100}설명.{0,100}용어표",
                    re.DOTALL,
                ),
            )

    def test_test_design_is_inherited_and_only_created_when_plan_omits_it(
        self,
    ) -> None:
        codex_executor = parse_limited_agent_toml(
            self._read(Path(".codex/agents/task-executor.toml"))
        )["developer_instructions"]
        _, claude_executor = self._frontmatter(Path(".claude/agents/task-executor.md"))
        for text in (
            self._read(Path("AGENTS.md")),
            self._read(COMMON_SKILL),
            codex_executor,
            claude_executor,
        ):
            self._assert_test_design_branch(text)

    def test_coordinator_boundary_and_verification_failure_loop_are_complete(
        self,
    ) -> None:
        for path in (Path("AGENTS.md"), COMMON_SKILL):
            text = self._read(path)
            section = self._section(text, "조정 Agent와 검증 실패 루프", level=3)
            self.assertRegex(section, r"조정 Agent.{0,50}구현하지 않")
            self.assertRegex(section, r"자기 결과.{0,50}합격 판정.{0,30}내리지 않")
            self._assert_terms_in_order(
                section,
                (
                    "검증 실패",
                    "발견 전체",
                    "실행 Agent",
                    "수정",
                    "동일한 검증 Agent",
                    "계획된 모든 검증",
                    "처음부터",
                ),
            )

    def test_common_skill_has_active_initial_and_follow_up_triggers(self) -> None:
        frontmatter, body = self._frontmatter(COMMON_SKILL)

        self.assertEqual(frontmatter["name"], "development-workflow")
        description = frontmatter["description"]
        for trigger in (
            "반드시",
            "구현",
            "수정",
            "재실행",
            "업데이트",
            "보완",
            "이전 결과",
        ):
            self.assertIn(trigger, description)
        self.assertLess(len(self._read(COMMON_SKILL).splitlines()), 500)
        for reference in (
            "plan-report-template.md",
            "verification-report-template.md",
            "trigger-evaluation.md",
            "dry-run-scenarios.md",
        ):
            self.assertIn(reference, body)

    def test_report_templates_cover_approval_and_verification_evidence(self) -> None:
        plan = self._read(PLAN_TEMPLATE)
        verification = self._read(VERIFICATION_TEMPLATE)

        for field in ("주제", "목적", "내용", "기대 결과", "검증 방법"):
            self.assertIn(field, plan)
        self.assertIn("작업 분할 판단", plan)
        self.assertIn("사용자 승인", plan)

        for field in (
            "독립 검증 Agent",
            "계획된 검증",
            "실행 결과",
            "증거",
            "누락",
            "판정",
        ):
            self.assertIn(field, verification)

    def test_trigger_evaluation_has_realistic_positive_and_near_miss_cases(
        self,
    ) -> None:
        text = self._read(TRIGGER_EVALUATION)
        should_trigger = self._section_bullets(text, "Should trigger")
        should_not_trigger = self._section_bullets(text, "Should not trigger")

        self.assertGreaterEqual(len(should_trigger), 8)
        self.assertLessEqual(len(should_trigger), 10)
        self.assertGreaterEqual(len(should_not_trigger), 8)
        self.assertLessEqual(len(should_not_trigger), 10)
        for category in ("명시적 요청", "캐주얼 요청", "후속 수정"):
            category_text = self._section(text, category, level=3)
            category_cases = re.findall(r"^- `", category_text, re.MULTILINE)
            self.assertGreaterEqual(len(category_cases), 2)
        near_miss = self._section(text, "Near-miss 경계", level=3)
        self.assertGreaterEqual(len(re.findall(r"^- `", near_miss, re.MULTILINE)), 8)

    def test_dry_run_scenarios_cover_success_and_error_paths(self) -> None:
        text = self._read(DRY_RUN_SCENARIOS)

        self.assertIn("## 정상 흐름", text)
        self.assertIn("## 오류 흐름", text)
        self.assertIn("범위 변경", text)
        self.assertIn("재승인", text)
        self.assertIn("검증 Agent", text)
        failure = self._section(text, "일반 검증 실패", level=3)
        self._assert_terms_in_order(
            failure,
            (
                "검증 실패",
                "발견 전체",
                "실행 Agent",
                "수정",
                "동일한 검증 Agent",
                "전체 검증",
                "처음부터",
            ),
        )

    def test_codex_agents_use_official_schema_and_role_sandboxes(self) -> None:
        executor_path = Path(".codex/agents/task-executor.toml")
        verifier_path = Path(".codex/agents/task-verifier.toml")
        executor_text = self._read(executor_path)
        verifier_text = self._read(verifier_path)
        executor = parse_limited_agent_toml(executor_text)
        verifier = parse_limited_agent_toml(verifier_text)

        for config in (executor, verifier):
            self.assertTrue(config["name"])
            self.assertTrue(config["description"])
            self.assertTrue(config["developer_instructions"])
            self.assertIn("AGENTS.md", config["developer_instructions"])
            self.assertIn(str(COMMON_SKILL), config["developer_instructions"])

        self.assertEqual(executor["name"], "task-executor")
        self.assertEqual(executor["sandbox_mode"], "workspace-write")
        self.assertEqual(verifier["name"], "task-verifier")
        self.assertEqual(verifier["sandbox_mode"], "read-only")
        self.assertIn("최종 합격 판정", executor["developer_instructions"])
        self.assertIn("독립", verifier["developer_instructions"])
        for text in (executor_text, verifier_text):
            with self.assertRaises(ValueError):
                parse_limited_agent_toml(text + "unsupported = true\n")

    def test_claude_agents_have_role_appropriate_tool_surfaces(self) -> None:
        executor_frontmatter, executor_body = self._frontmatter(
            Path(".claude/agents/task-executor.md")
        )
        verifier_frontmatter, verifier_body = self._frontmatter(
            Path(".claude/agents/task-verifier.md")
        )

        self.assertEqual(set(executor_frontmatter), {"name", "description", "tools"})
        self.assertEqual(set(verifier_frontmatter), {"name", "description", "tools"})
        self.assertEqual(executor_frontmatter["name"], "task-executor")
        self.assertEqual(verifier_frontmatter["name"], "task-verifier")
        self.assertEqual(
            executor_frontmatter["description"],
            "승인된 단일 작업을 구현하고 쉬운 검증까지만 수행할 때 사용한다.",
        )
        self.assertEqual(
            verifier_frontmatter["description"],
            "구현과 분리된 인스턴스에서 승인 계획을 독립 검증할 때 사용한다.",
        )
        executor_tools = set(executor_frontmatter["tools"].split(", "))
        verifier_tools = set(verifier_frontmatter["tools"].split(", "))
        self.assertEqual(
            executor_tools, {"Read", "Grep", "Glob", "Bash", "Write", "Edit"}
        )
        self.assertEqual(verifier_tools, {"Read", "Grep", "Glob", "Bash"})

        for body in (executor_body, verifier_body):
            self.assertIn("AGENTS.md", body)
            self.assertIn(str(COMMON_SKILL), body)
        self.assertIn("최종 합격 판정", executor_body)
        self.assertIn("독립", verifier_body)

    def test_claude_pointer_and_skill_adapter_stay_thin(self) -> None:
        claude_md = self._read(Path("CLAUDE.md"))
        adapter_path = Path(".claude/skills/development-workflow/SKILL.md")
        _, adapter_body = self._frontmatter(adapter_path)

        self.assertTrue(claude_md.startswith("@AGENTS.md\n"))
        self.assertIn("변경 이력", claude_md)
        self.assertIn("development-workflow", claude_md)
        self.assertLess(len(claude_md.splitlines()), 30)
        self.assertIn(str(COMMON_SKILL), adapter_body)
        self.assertLess(len(self._read(adapter_path).splitlines()), 30)

        platform_files = (
            Path(".codex/agents/task-executor.toml"),
            Path(".codex/agents/task-verifier.toml"),
            Path(".claude/agents/task-executor.md"),
            Path(".claude/agents/task-verifier.md"),
            adapter_path,
        )
        for path in platform_files:
            text = self._read(path)
            self.assertNotIn("Backward Compatibility & Risk Isolation", text)
            self.assertNotIn("Layered Slicing", text)

    def test_no_deprecated_claude_commands_are_created(self) -> None:
        self.assertFalse((REPOSITORY_ROOT / ".claude/commands").exists())


if __name__ == "__main__":
    unittest.main()
