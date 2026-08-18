"""Unit tests for conservative occurrence metadata extraction."""

import json
from dataclasses import FrozenInstanceError
from datetime import date
from importlib.util import find_spec
from pathlib import Path

import pytest

from omf_retrieval.application.indexing.ports import ArchiveFile, SourceSnapshot
from omf_retrieval.domain.enums import RelationType
from omf_retrieval.domain.models import LineRange

PROJECT_ROOT = Path(__file__).resolve().parents[3]
OMF_RELATIONS_PATH = PROJECT_ROOT / "config/source_profiles/omf-relations.json"
VALID_COMMIT_SHA = "0123456789abcdef0123456789abcdef01234567"


class StringSubclass(str):
    """Represent an invalid string subclass at an exact-type boundary."""


class TupleSubclass(tuple[object, ...]):
    """Represent an invalid tuple subclass at an exact-type boundary."""


class IntSubclass(int):
    """Represent an invalid integer subclass at an exact-type boundary."""


class LineRangeSubclass(LineRange):
    """Represent an invalid line-range subclass at an exact-type boundary."""


class SourceSnapshotSubclass(SourceSnapshot):
    """Represent an invalid snapshot subclass at an exact-type boundary."""


def _source_snapshot(*files: tuple[str, bytes]) -> SourceSnapshot:
    return SourceSnapshot(
        commit_sha=VALID_COMMIT_SHA,
        archive_files=tuple(
            ArchiveFile(source_path=source_path, content=content)
            for source_path, content in sorted(files)
        ),
    )


def _valid_relation_entry(**overrides: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "from_source_path": "docs/planning/new.md",
        "to_source_path": "docs/planning/old.md",
        "relation_type": "supersedes",
        "evidence_source_path": "docs/planning/evidence.md",
        "evidence_line_start": 1,
        "evidence_line_end": 1,
    }
    entry.update(overrides)
    return entry


def _relation_payload(*entries: object) -> str:
    return json.dumps({"relations": list(entries)}, ensure_ascii=False)


def _relation_snapshot() -> SourceSnapshot:
    return _source_snapshot(
        ("docs/planning/evidence-2.md", b"Second evidence.\n"),
        ("docs/planning/evidence.md", b"Evidence.\n"),
        ("docs/planning/new.md", b"New.\n"),
        ("docs/planning/old.md", b"Old.\n"),
    )


def _relation_snapshot_with_evidence(content: bytes) -> SourceSnapshot:
    return _source_snapshot(
        ("docs/planning/evidence.md", content),
        ("docs/planning/new.md", b"New.\n"),
        ("docs/planning/old.md", b"Old.\n"),
    )


def test_extracts_approved_explicit_metadata_example() -> None:
    """Explicit top metadata and path markers produce occurrence metadata."""
    module_spec = find_spec("omf_retrieval.application.indexing.metadata")

    assert module_spec is not None, "metadata extractor module must exist"

    from omf_retrieval.application.indexing.metadata import extract_metadata
    from omf_retrieval.domain.enums import DecisionState, OwnerDomain, VersionScope

    metadata = extract_metadata(
        "docs/research/2026-07-14-긴급WO-운영방식-확정기록.md",
        (
            "# 긴급 WO 운영 방식",
            "작성일: 2026-07-14",
            "버전: v1.0",
            "상태: [확정]",
        ),
    )

    assert metadata.document_date == date(2026, 7, 14)
    assert metadata.version == "1.0"
    assert metadata.decision_state is DecisionState.CONFIRMED
    assert metadata.version_scope is VersionScope.CURRENT
    assert metadata.owner_domain is OwnerDomain.DOCS


def test_invalid_structured_version_uses_explicit_filename_fallback() -> None:
    """An invalid version field cannot override a valid filename marker."""
    from omf_retrieval.application.indexing.metadata import extract_metadata

    metadata = extract_metadata(
        "docs/research/2026-07-14-spec-v2.0.md",
        ("작성일: 2026-07-14", "버전: release-1"),
    )

    assert metadata.version == "2.0"


@pytest.mark.parametrize(
    ("source_path", "first_lines", "expected_date"),
    [
        (
            "docs/research/2026-01-02-priority.md",
            ("작성일: 2025-12-31",),
            date(2025, 12, 31),
        ),
        (
            "docs/research/leap-day.md",
            ("| 작성일 | 2024-02-29 |",),
            date(2024, 2, 29),
        ),
        (
            "docs/research/2026-07-14-fallback.md",
            ("작성일: 2026-02-29",),
            date(2026, 7, 14),
        ),
        (
            "docs/research/2026-02-29-invalid.md",
            ("작성일: 2026-2-9",),
            None,
        ),
        ("docs/research/2024-02-29-filename.md", (), date(2024, 2, 29)),
        ("docs/research/report-2026-07-14.md", (), None),
        ("docs/research/no-date.md", (), None),
    ],
)
def test_date_uses_only_valid_structured_or_leading_filename_signals(
    source_path: str,
    first_lines: tuple[str, ...],
    expected_date: date | None,
) -> None:
    """Date priority and validation prevent inference from arbitrary digits."""
    from omf_retrieval.application.indexing.metadata import extract_metadata

    assert extract_metadata(source_path, first_lines).document_date == expected_date


def test_date_reads_physical_line_40_but_not_line_41() -> None:
    """The top-metadata boundary includes physical line 40 only."""
    from omf_retrieval.application.indexing.metadata import extract_metadata

    line_40_date = (*("" for _ in range(39)), "작성일: 2024-02-29")
    line_41_date = (*("" for _ in range(40)), "작성일: 2024-02-29")

    assert extract_metadata(
        "docs/research/line-40.md", line_40_date
    ).document_date == date(2024, 2, 29)
    assert (
        extract_metadata("docs/research/line-41.md", line_41_date).document_date is None
    )


@pytest.mark.parametrize(
    ("source_path", "first_lines", "expected_version"),
    [
        (
            "docs/research/2026-07-14-priority-v4.0.md",
            ("버전: v2.1.3",),
            "2.1.3",
        ),
        ("docs/research/table.md", ("| 버전 | v3 |",), "3"),
        (
            "docs/research/fallback-v2.5.md",
            ("버전: version 1",),
            "2.5",
        ),
        ("docs/research/spec-v3.4.md", (), "3.4"),
        ("docs/research/2026-07-14-only-date.md", (), None),
        ("docs/research/rev1.2-not-explicit.md", (), None),
        ("docs/research/upper-V1.md", (), None),
        ("docs/research/no-version.md", (), None),
    ],
)
def test_version_uses_only_explicit_v_markers_and_removes_leading_v(
    source_path: str,
    first_lines: tuple[str, ...],
    expected_version: str | None,
) -> None:
    """Only explicit lowercase vN markers yield normalized numeric versions."""
    from omf_retrieval.application.indexing.metadata import extract_metadata

    assert extract_metadata(source_path, first_lines).version == expected_version


def test_version_reads_physical_line_40_but_not_line_41() -> None:
    """Version extraction applies the same inclusive 40-line boundary."""
    from omf_retrieval.application.indexing.metadata import extract_metadata

    line_40_version = (*("" for _ in range(39)), "버전: v1.40")
    line_41_version = (*("" for _ in range(40)), "버전: v1.41")

    assert extract_metadata("docs/research/line-40.md", line_40_version).version == (
        "1.40"
    )
    assert extract_metadata("docs/research/line-41.md", line_41_version).version is None


def test_exact_versions_path_segment_marks_historical_occurrence() -> None:
    """A path segment named exactly versions marks only that occurrence historical."""
    from omf_retrieval.application.indexing.metadata import extract_metadata
    from omf_retrieval.domain.enums import VersionScope

    metadata = extract_metadata("docs/planning/versions/spec.md", ())

    assert metadata.version_scope is VersionScope.HISTORICAL


@pytest.mark.parametrize(
    ("source_path", "historical"),
    [
        ("docs/planning/versions/spec.md", True),
        ("uiux/versions/spec.md", True),
        ("docs/planning/versions-old/spec.md", False),
        ("docs/planning/archive-versions/spec.md", False),
        ("docs/planning/current/spec.md", False),
    ],
)
def test_version_scope_uses_an_exact_case_sensitive_path_segment(
    source_path: str,
    historical: bool,
) -> None:
    """Similar segment names cannot be mistaken for the versions directory."""
    from omf_retrieval.application.indexing.metadata import extract_metadata
    from omf_retrieval.domain.enums import VersionScope

    expected_scope = VersionScope.HISTORICAL if historical else VersionScope.CURRENT

    assert extract_metadata(source_path, ()).version_scope is expected_scope


@pytest.mark.parametrize(
    ("source_path", "expected_owner"),
    [
        ("docs/research/spec.md", "docs"),
        ("uiux/spec.md", "uiux"),
    ],
)
def test_owner_domain_comes_only_from_the_first_path_segment(
    source_path: str,
    expected_owner: str,
) -> None:
    """Approved top-level source roots assign the occurrence owner."""
    from omf_retrieval.application.indexing.metadata import extract_metadata

    assert extract_metadata(source_path, ()).owner_domain.value == expected_owner


@pytest.mark.parametrize(
    "source_path",
    ["config/spec.md", "Docs/research/spec.md", "research/docs/spec.md"],
)
def test_out_of_scope_owner_roots_are_rejected(source_path: str) -> None:
    """Metadata extraction cannot assign ownership outside docs and uiux."""
    from omf_retrieval.application.indexing.metadata import extract_metadata

    with pytest.raises(ValueError, match="owner"):
        extract_metadata(source_path, ())


@pytest.mark.parametrize(
    "source_path",
    [
        "",
        ".",
        "/docs/research/spec.md",
        "./docs/research/spec.md",
        "docs//research/spec.md",
        "docs/research/./spec.md",
        "docs/research/../secret.md",
        "docs/research/spec.md/",
        "docs\\research\\spec.md",
        "docs/research/\x00secret.md",
    ],
)
def test_noncanonical_or_unsafe_source_paths_are_rejected(source_path: str) -> None:
    """Only already-canonical safe repository-relative POSIX paths are accepted."""
    from omf_retrieval.application.indexing.metadata import extract_metadata

    with pytest.raises(ValueError, match="canonical"):
        extract_metadata(source_path, ())


def test_identical_content_gets_metadata_for_each_occurrence_path() -> None:
    """Owner and version scope are calculated per path rather than per content."""
    from omf_retrieval.application.indexing.metadata import extract_metadata
    from omf_retrieval.domain.enums import OwnerDomain, VersionScope

    identical_first_lines = ("작성일: 2026-07-14", "버전: v1.0")

    docs_occurrence = extract_metadata("docs/research/shared.md", identical_first_lines)
    uiux_occurrence = extract_metadata("uiux/versions/shared.md", identical_first_lines)

    assert (
        docs_occurrence.document_date
        == uiux_occurrence.document_date
        == date(2026, 7, 14)
    )
    assert docs_occurrence.version == uiux_occurrence.version == "1.0"
    assert docs_occurrence.owner_domain is OwnerDomain.DOCS
    assert uiux_occurrence.owner_domain is OwnerDomain.UIUX
    assert docs_occurrence.version_scope is VersionScope.CURRENT
    assert uiux_occurrence.version_scope is VersionScope.HISTORICAL


def test_structured_confirmed_marker_sets_confirmed_state() -> None:
    """An exact explicit top status marker confirms the document occurrence."""
    from omf_retrieval.application.indexing.metadata import extract_metadata
    from omf_retrieval.domain.enums import DecisionState

    metadata = extract_metadata("docs/research/operation-policy.md", ("상태: [확정]",))

    assert metadata.decision_state is DecisionState.CONFIRMED


@pytest.mark.parametrize(
    "source_path",
    [
        "docs/research/operation-확정기록.md",
        "docs/planning/operation-결정서.md",
    ],
)
def test_confirmed_filename_markers_are_explicit_signals(source_path: str) -> None:
    """Each approved confirmed filename marker sets confirmed state."""
    from omf_retrieval.application.indexing.metadata import extract_metadata
    from omf_retrieval.domain.enums import DecisionState

    assert extract_metadata(source_path, ()).decision_state is (DecisionState.CONFIRMED)


@pytest.mark.parametrize(
    "first_lines",
    [
        ("상태: [확정]",),
        ("결정 상태: 확정",),
        ("| 상태 | [확정] |",),
        ("| 신뢰도 | 확정 |",),
    ],
)
def test_confirmed_structured_markers_are_explicit_signals(
    first_lines: tuple[str, ...],
) -> None:
    """Approved key-value and table markers set confirmed state."""
    from omf_retrieval.application.indexing.metadata import extract_metadata
    from omf_retrieval.domain.enums import DecisionState

    metadata = extract_metadata("docs/research/operation-policy.md", first_lines)

    assert metadata.decision_state is DecisionState.CONFIRMED


@pytest.mark.parametrize("marker", ["초안", "제안안", "가설", "진행메모"])
def test_draft_filename_markers_are_explicit_signals(marker: str) -> None:
    """Each approved draft filename marker sets draft state."""
    from omf_retrieval.application.indexing.metadata import extract_metadata
    from omf_retrieval.domain.enums import DecisionState

    metadata = extract_metadata(f"docs/research/operation-{marker}.md", ())

    assert metadata.decision_state is DecisionState.DRAFT


@pytest.mark.parametrize("marker", ["초안", "제안안", "가설", "진행메모"])
def test_draft_markers_in_the_first_top_title_are_explicit_signals(
    marker: str,
) -> None:
    """Each approved draft marker in the document title sets draft state."""
    from omf_retrieval.application.indexing.metadata import extract_metadata
    from omf_retrieval.domain.enums import DecisionState

    metadata = extract_metadata(
        "docs/research/operation-policy.md",
        ("", f"# 운영 정책 {marker}"),
    )

    assert metadata.decision_state is DecisionState.DRAFT


@pytest.mark.parametrize(
    "first_lines",
    [
        ("상태: 미확정",),
        ("상태: 불확정",),
        ("상태: 확정 전",),
        ("상태: [미확정]",),
        ("| 신뢰도 | 불확정 |",),
    ],
)
def test_negative_confirmed_phrases_do_not_confirm(
    first_lines: tuple[str, ...],
) -> None:
    """Negated status values cannot match the exact confirmed signal."""
    from omf_retrieval.application.indexing.metadata import extract_metadata
    from omf_retrieval.domain.enums import DecisionState

    metadata = extract_metadata("docs/research/operation-policy.md", first_lines)

    assert metadata.decision_state is DecisionState.UNKNOWN


def test_negative_phrase_suppresses_a_confirmed_marker_in_the_same_filename() -> None:
    """A negated filename cannot confirm merely because it also says decision record."""
    from omf_retrieval.application.indexing.metadata import extract_metadata
    from omf_retrieval.domain.enums import DecisionState

    metadata = extract_metadata("docs/research/미확정-결정서.md", ())

    assert metadata.decision_state is DecisionState.UNKNOWN


@pytest.mark.parametrize(
    "first_lines",
    [
        ("확정",),
        ("이 문서는 확정 상태입니다.",),
        ("본문에서 가설을 검토한다.",),
        ("# 운영 정책", "## 가설 검토"),
        (*("" for _ in range(40)), "상태: 확정"),
    ],
)
def test_body_or_out_of_boundary_markers_do_not_set_document_state(
    first_lines: tuple[str, ...],
) -> None:
    """Narrative body text and line 41 are not document-level signals."""
    from omf_retrieval.application.indexing.metadata import extract_metadata
    from omf_retrieval.domain.enums import DecisionState

    metadata = extract_metadata("docs/research/operation-policy.md", first_lines)

    assert metadata.decision_state is DecisionState.UNKNOWN


def test_only_the_filename_not_its_parent_directories_marks_state() -> None:
    """A marker in a directory name is not a filename signal."""
    from omf_retrieval.application.indexing.metadata import extract_metadata
    from omf_retrieval.domain.enums import DecisionState

    metadata = extract_metadata("docs/research/확정기록/operation.md", ())

    assert metadata.decision_state is DecisionState.UNKNOWN


@pytest.mark.parametrize(
    ("source_path", "first_lines"),
    [
        ("docs/research/확정기록-초안.md", ()),
        (
            "docs/research/결정서.md",
            ("# 운영 정책 제안안",),
        ),
        (
            "docs/research/operation.md",
            ("# 운영 정책 가설", "상태: [확정]"),
        ),
    ],
)
def test_conflicting_explicit_confirmed_and_draft_signals_return_unknown(
    source_path: str,
    first_lines: tuple[str, ...],
) -> None:
    """Neither explicit side wins when confirmed and draft signals conflict."""
    from omf_retrieval.application.indexing.metadata import extract_metadata
    from omf_retrieval.domain.enums import DecisionState

    assert extract_metadata(source_path, first_lines).decision_state is (
        DecisionState.UNKNOWN
    )


def test_top_line_40_state_signal_is_included() -> None:
    """A structured decision marker on physical line 40 is still considered."""
    from omf_retrieval.application.indexing.metadata import extract_metadata
    from omf_retrieval.domain.enums import DecisionState

    lines = (*("" for _ in range(39)), "상태: 확정")

    assert extract_metadata("docs/research/operation.md", lines).decision_state is (
        DecisionState.CONFIRMED
    )


def test_extraction_is_deterministic_and_returns_the_canonical_frozen_model() -> None:
    """Repeated exact inputs yield the existing immutable domain value object."""
    from omf_retrieval.application.indexing.metadata import extract_metadata
    from omf_retrieval.domain.models import DocumentMetadata

    first_lines = ("작성일: 2026-07-14\r\n", "버전: v1.0\n", "상태: 확정")

    first = extract_metadata("uiux/versions/operation.md", first_lines)
    second = extract_metadata("uiux/versions/operation.md", first_lines)

    assert first == second
    assert type(first) is DocumentMetadata
    with pytest.raises(FrozenInstanceError):
        first.version = "2.0"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("source_path", "first_lines"),
    [
        (StringSubclass("docs/research/spec.md"), ()),
        (b"docs/research/spec.md", ()),
        ("docs/research/spec.md", []),
        ("docs/research/spec.md", TupleSubclass(())),
        ("docs/research/spec.md", (StringSubclass("상태: 확정"),)),
        ("docs/research/spec.md", (42,)),
        ("docs/research/spec.md", ("line one\nline two",)),
        ("docs/research/spec.md", ("line one\rline two",)),
    ],
)
def test_public_input_boundary_rejects_nonexact_or_nonphysical_lines(
    source_path: object,
    first_lines: object,
) -> None:
    """Mutable, subclassed, and nonphysical inputs cannot enter extraction."""
    from omf_retrieval.application.indexing.metadata import (
        MetadataExtractionError,
        extract_metadata,
    )

    with pytest.raises(MetadataExtractionError, match="input"):
        extract_metadata(source_path, first_lines)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("source_path", "first_lines", "secret"),
    [
        ("classified-token/spec.md", (), "classified-token"),
        (
            "docs/research/spec.md",
            ("safe\nclassified-line",),
            "classified-line",
        ),
    ],
)
def test_validation_errors_do_not_echo_rejected_input(
    source_path: str,
    first_lines: tuple[str, ...],
    secret: str,
) -> None:
    """Validation diagnostics remain useful without exposing rejected source data."""
    from omf_retrieval.application.indexing.metadata import (
        MetadataExtractionError,
        extract_metadata,
    )

    with pytest.raises(MetadataExtractionError) as error_info:
        extract_metadata(source_path, first_lines)

    assert secret not in str(error_info.value)


def test_fenced_structured_state_is_not_document_metadata() -> None:
    """A metadata-shaped line inside a fenced block cannot confirm a document."""
    from omf_retrieval.application.indexing.metadata import extract_metadata
    from omf_retrieval.domain.enums import DecisionState

    metadata = extract_metadata(
        "docs/research/operation.md",
        ("```yaml", "상태: 확정", "```"),
    )

    assert metadata.decision_state is DecisionState.UNKNOWN


@pytest.mark.parametrize(
    "first_lines",
    [
        (
            "```yaml",
            "작성일: 2024-02-29",
            "버전: v9.1",
            "상태: 확정",
            "```",
        ),
        (
            "   ~~~metadata",
            "작성일: 2024-02-29",
            "버전: v9.1",
            "상태: 확정",
            "   ~~~",
        ),
        ("```yaml", "작성일: 2024-02-29", "버전: v9.1", "상태: 확정"),
        (
            "<!--",
            "작성일: 2024-02-29",
            "버전: v9.1",
            "상태: 확정",
            "-->",
        ),
        ("<!--", "작성일: 2024-02-29", "버전: v9.1", "상태: 확정"),
        (
            "    작성일: 2024-02-29",
            "\t버전: v9.1",
            "    상태: 확정",
        ),
        (
            "> 작성일: 2024-02-29",
            "> 버전: v9.1",
            "> 상태: 확정",
        ),
        (
            "`작성일: 2024-02-29`",
            "`버전: v9.1`",
            "`상태: 확정`",
        ),
    ],
)
def test_non_metadata_markdown_regions_hide_all_structured_signals(
    first_lines: tuple[str, ...],
) -> None:
    """Code, comments, blockquotes, and inline code cannot supply metadata."""
    from omf_retrieval.application.indexing.metadata import extract_metadata
    from omf_retrieval.domain.enums import DecisionState

    metadata = extract_metadata("docs/research/operation.md", first_lines)

    assert metadata.document_date is None
    assert metadata.version is None
    assert metadata.decision_state is DecisionState.UNKNOWN


@pytest.mark.parametrize(
    "first_lines",
    [
        ("```", "# 운영 정책 초안", "```"),
        ("~~~", "# 운영 정책 제안안", "~~~"),
        ("<!--", "# 운영 정책 가설", "-->"),
        ("<!-- # 운영 정책 초안 -->",),
        ("    # 운영 정책 진행메모",),
        ("> # 운영 정책 초안",),
        ("`# 운영 정책 초안`",),
    ],
)
def test_non_metadata_markdown_regions_hide_draft_headings(
    first_lines: tuple[str, ...],
) -> None:
    """A heading-shaped line outside document flow cannot set draft state."""
    from omf_retrieval.application.indexing.metadata import extract_metadata
    from omf_retrieval.domain.enums import DecisionState

    metadata = extract_metadata("docs/research/operation.md", first_lines)

    assert metadata.decision_state is DecisionState.UNKNOWN


def test_fence_requires_a_matching_close_of_at_least_the_opener_length() -> None:
    """Short, mismatched, or suffixed fence lines do not expose hidden metadata."""
    from omf_retrieval.application.indexing.metadata import extract_metadata
    from omf_retrieval.domain.enums import DecisionState

    metadata = extract_metadata(
        "docs/research/operation.md",
        (
            "````yaml",
            "작성일: 2024-02-29",
            "```",
            "~~~",
            "```` trailing text",
            "버전: v9.1",
            "상태: 확정",
            "````",
            "작성일: 2026-07-14",
            "버전: v2.0",
            "상태: 확정",
        ),
    )

    assert metadata.document_date == date(2026, 7, 14)
    assert metadata.version == "2.0"
    assert metadata.decision_state is DecisionState.CONFIRMED


@pytest.mark.parametrize(
    "excluded_lines",
    [
        ("```", "작성일: 2024-02-29", "버전: v9.1", "상태: 확정", "```"),
        ("<!-- 주석 시작", "작성일: 2024-02-29", "버전: v9.1", "상태: 확정", "-->"),
    ],
)
def test_valid_metadata_after_an_excluded_region_is_still_read(
    excluded_lines: tuple[str, ...],
) -> None:
    """Closing a code or comment region resumes metadata recognition."""
    from omf_retrieval.application.indexing.metadata import extract_metadata
    from omf_retrieval.domain.enums import DecisionState

    metadata = extract_metadata(
        "docs/research/operation.md",
        (
            *excluded_lines,
            "작성일: 2026-07-14",
            "버전: v1.2.3",
            "상태: [확정]",
        ),
    )

    assert metadata.document_date == date(2026, 7, 14)
    assert metadata.version == "1.2.3"
    assert metadata.decision_state is DecisionState.CONFIRMED


def test_block_filter_does_not_shift_the_physical_line_40_boundary() -> None:
    """Discarding block syntax cannot pull original line 41 into the top window."""
    from omf_retrieval.application.indexing.metadata import extract_metadata
    from omf_retrieval.domain.enums import DecisionState

    lines = ("```", "```", *("" for _ in range(38)), "상태: 확정")

    metadata = extract_metadata("docs/research/operation.md", lines)

    assert metadata.decision_state is DecisionState.UNKNOWN


def test_malformed_filename_version_with_repeated_dot_fails_closed() -> None:
    """A malformed v token cannot be partially accepted as an earlier version."""
    from omf_retrieval.application.indexing.metadata import extract_metadata

    metadata = extract_metadata("docs/research/spec-v1..2.md", ())

    assert metadata.version is None


@pytest.mark.parametrize(
    ("source_path", "expected_version"),
    [
        ("docs/research/v1.2.3.md", "1.2.3"),
        ("docs/research/spec-v1.2.3.md", "1.2.3"),
        ("docs/research/spec-v1.2.3-final.md", "1.2.3"),
        ("docs/research/spec_v1.2.3_final.md", "1.2.3"),
        ("docs/research/spec-v1.2.extra.md", None),
        ("docs/research/spec-v1.2beta.md", None),
        ("docs/research/spec-(v1.2).md", None),
        ("docs/research/spec v1.2.md", None),
    ],
)
def test_filename_version_requires_hyphen_underscore_or_end_boundaries(
    source_path: str,
    expected_version: str | None,
) -> None:
    """Explicit filename version tokens use only approved token boundaries."""
    from omf_retrieval.application.indexing.metadata import extract_metadata

    assert extract_metadata(source_path, ()).version == expected_version


def test_invalid_structured_version_still_uses_valid_filename_after_filtering() -> None:
    """Fail-closed structured parsing preserves the approved filename fallback."""
    from omf_retrieval.application.indexing.metadata import extract_metadata

    metadata = extract_metadata(
        "docs/research/spec-v2.4.md",
        ("버전: v1..9",),
    )

    assert metadata.version == "2.4"


def test_committed_empty_relation_sidecar_parses_to_an_immutable_empty_tuple() -> None:
    """The reserved committed container is the valid empty relation contract."""
    from omf_retrieval.application.indexing import metadata as metadata_module

    parser = getattr(metadata_module, "parse_relation_sidecar", None)

    assert callable(parser), "relation sidecar parser must exist"
    assert (
        parser(OMF_RELATIONS_PATH.read_text(encoding="utf-8"), _source_snapshot()) == ()
    )


def test_supersedes_relation_preserves_new_to_old_direction_and_evidence() -> None:
    """A valid explicit supersession maps new-to-old with source evidence."""
    from omf_retrieval.application.indexing import metadata as metadata_module

    parser = getattr(metadata_module, "parse_relation_sidecar", None)
    relation_type = getattr(metadata_module, "DocumentRelationSpec", None)

    assert callable(parser), "relation sidecar parser must exist"
    assert callable(relation_type), "relation DTO must exist"
    snapshot = _source_snapshot(
        ("docs/planning/new.md", b"# New\nReplaces the old policy.\n"),
        ("docs/planning/versions/old.md", b"# Old\n"),
    )
    payload = """{
      "relations": [
        {
          "from_source_path": "docs/planning/new.md",
          "to_source_path": "docs/planning/versions/old.md",
          "relation_type": "supersedes",
          "evidence_source_path": "docs/planning/new.md",
          "evidence_line_start": 1,
          "evidence_line_end": 2
        }
      ]
    }"""

    assert parser(payload, snapshot) == (
        relation_type(
            from_source_path="docs/planning/new.md",
            to_source_path="docs/planning/versions/old.md",
            relation_type=RelationType.SUPERSEDES,
            evidence_source_path="docs/planning/new.md",
            evidence_line_range=LineRange(line_start=1, line_end=2),
        ),
    )


def test_relation_parser_preserves_input_order_direction_and_third_party_evidence() -> (
    None
):
    """Both relation enums retain input order and an independent evidence path."""
    from omf_retrieval.application.indexing.metadata import (
        DocumentRelationSpec,
        parse_relation_sidecar,
    )

    snapshot = _source_snapshot(
        ("docs/planning/a.md", b"# A\n"),
        ("docs/planning/evidence.md", b"Context\nConflict stated.\nSupport.\n"),
        ("docs/planning/new.md", b"# New\n"),
        ("docs/planning/old.md", b"# Old\n"),
        ("uiux/z.md", b"# Z\n"),
    )
    payload = """{
      "relations": [
        {
          "from_source_path": "uiux/z.md",
          "to_source_path": "docs/planning/a.md",
          "relation_type": "potential_conflict",
          "evidence_source_path": "docs/planning/evidence.md",
          "evidence_line_start": 2,
          "evidence_line_end": 3
        },
        {
          "from_source_path": "docs/planning/new.md",
          "to_source_path": "docs/planning/old.md",
          "relation_type": "supersedes",
          "evidence_source_path": "docs/planning/evidence.md",
          "evidence_line_start": 1,
          "evidence_line_end": 1
        }
      ]
    }"""

    relations = parse_relation_sidecar(payload, snapshot)

    assert relations == (
        DocumentRelationSpec(
            from_source_path="uiux/z.md",
            to_source_path="docs/planning/a.md",
            relation_type=RelationType.POTENTIAL_CONFLICT,
            evidence_source_path="docs/planning/evidence.md",
            evidence_line_range=LineRange(line_start=2, line_end=3),
        ),
        DocumentRelationSpec(
            from_source_path="docs/planning/new.md",
            to_source_path="docs/planning/old.md",
            relation_type=RelationType.SUPERSEDES,
            evidence_source_path="docs/planning/evidence.md",
            evidence_line_range=LineRange(line_start=1, line_end=1),
        ),
    )
    assert type(relations) is tuple
    assert [relation.relation_type for relation in relations] == [
        RelationType.POTENTIAL_CONFLICT,
        RelationType.SUPERSEDES,
    ]
    assert not hasattr(relations[0], "__dict__")
    with pytest.raises(FrozenInstanceError):
        relations[0].from_source_path = "docs/planning/a.md"  # type: ignore[misc]


@pytest.mark.parametrize(
    "payload",
    [
        "{",
        "[]",
        "null",
        '{"relations": {}}',
        '{"relations": null}',
        '{"relations": [[]]}',
        '{"relations": [null]}',
    ],
)
def test_relation_sidecar_rejects_malformed_or_wrong_json_shapes(payload: str) -> None:
    """Malformed JSON and non-object/list/entry shapes fail closed."""
    from omf_retrieval.application.indexing.metadata import (
        RelationSidecarValidationError,
        parse_relation_sidecar,
    )

    with pytest.raises(RelationSidecarValidationError):
        parse_relation_sidecar(payload, _relation_snapshot())


@pytest.mark.parametrize(
    "payload",
    [
        "{}",
        '{"relations": [], "extra": true}',
        '{"relations": [], "relations": []}',
        _relation_payload(
            {
                key: value
                for key, value in _valid_relation_entry().items()
                if key != "evidence_line_end"
            }
        ),
        _relation_payload({**_valid_relation_entry(), "extra": True}),
        """{
          "relations": [{
            "from_source_path": "docs/planning/new.md",
            "from_source_path": "docs/planning/new.md",
            "to_source_path": "docs/planning/old.md",
            "relation_type": "supersedes",
            "evidence_source_path": "docs/planning/evidence.md",
            "evidence_line_start": 1,
            "evidence_line_end": 1
          }]
        }""",
    ],
    ids=[
        "missing-root-key",
        "unknown-root-key",
        "duplicate-root-key",
        "missing-entry-key",
        "unknown-entry-key",
        "duplicate-entry-key",
    ],
)
def test_relation_sidecar_requires_exact_unique_root_and_entry_keys(
    payload: str,
) -> None:
    """Missing, unknown, and duplicate JSON keys cannot alter the contract."""
    from omf_retrieval.application.indexing.metadata import (
        RelationSidecarValidationError,
        parse_relation_sidecar,
    )

    with pytest.raises(RelationSidecarValidationError):
        parse_relation_sidecar(payload, _relation_snapshot())


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("from_source_path", 1),
        ("to_source_path", ["docs/planning/old.md"]),
        ("relation_type", True),
        ("evidence_source_path", {"path": "docs/planning/evidence.md"}),
        ("evidence_line_start", "1"),
        ("evidence_line_end", 1.0),
        ("relation_type", "conflict"),
        ("relation_type", "SUPERSEDES"),
    ],
)
def test_relation_sidecar_rejects_wrong_value_types_and_unknown_relation_types(
    field_name: str,
    invalid_value: object,
) -> None:
    """Every relation value has one exact JSON type and two enum values."""
    from omf_retrieval.application.indexing.metadata import (
        RelationSidecarValidationError,
        parse_relation_sidecar,
    )

    with pytest.raises(RelationSidecarValidationError):
        parse_relation_sidecar(
            _relation_payload(_valid_relation_entry(**{field_name: invalid_value})),
            _relation_snapshot(),
        )


def test_relation_paths_are_canonicalized_before_snapshot_membership_checks() -> None:
    """Normalizable POSIX paths become the snapshot's canonical coordinates."""
    from omf_retrieval.application.indexing.metadata import parse_relation_sidecar

    payload = _relation_payload(
        _valid_relation_entry(
            from_source_path="./docs//planning/new.md",
            to_source_path="docs/./planning//old.md",
            evidence_source_path="./docs/planning//evidence.md",
        )
    )

    relation = parse_relation_sidecar(payload, _relation_snapshot())[0]

    assert relation.from_source_path == "docs/planning/new.md"
    assert relation.to_source_path == "docs/planning/old.md"
    assert relation.evidence_source_path == "docs/planning/evidence.md"


@pytest.mark.parametrize(
    ("field_name", "invalid_path"),
    [
        ("from_source_path", "/docs/planning/new.md"),
        ("to_source_path", "docs/planning/../old.md"),
        ("evidence_source_path", "docs\\planning\\evidence.md"),
        ("from_source_path", ""),
    ],
)
def test_relation_sidecar_rejects_unsafe_paths(
    field_name: str,
    invalid_path: str,
) -> None:
    """Unsafe relation coordinates never enter snapshot matching."""
    from omf_retrieval.application.indexing.metadata import (
        RelationSidecarValidationError,
        parse_relation_sidecar,
    )

    with pytest.raises(RelationSidecarValidationError):
        parse_relation_sidecar(
            _relation_payload(_valid_relation_entry(**{field_name: invalid_path})),
            _relation_snapshot(),
        )


@pytest.mark.parametrize(
    "field_name",
    ["from_source_path", "to_source_path", "evidence_source_path"],
)
def test_every_relation_path_must_exist_in_the_same_filtered_snapshot(
    field_name: str,
) -> None:
    """Endpoints and evidence cannot refer outside the supplied snapshot."""
    from omf_retrieval.application.indexing.metadata import (
        RelationSidecarValidationError,
        parse_relation_sidecar,
    )

    with pytest.raises(RelationSidecarValidationError):
        parse_relation_sidecar(
            _relation_payload(
                _valid_relation_entry(**{field_name: "docs/planning/missing.md"})
            ),
            _relation_snapshot(),
        )


def test_relation_sidecar_rejects_a_self_relation_after_canonicalization() -> None:
    """One document cannot relate to itself through an alternate path spelling."""
    from omf_retrieval.application.indexing.metadata import (
        RelationSidecarValidationError,
        parse_relation_sidecar,
    )

    with pytest.raises(RelationSidecarValidationError):
        parse_relation_sidecar(
            _relation_payload(
                _valid_relation_entry(to_source_path="./docs//planning/new.md")
            ),
            _relation_snapshot(),
        )


def test_relation_sidecar_rejects_an_exact_duplicate() -> None:
    """The same explicit relation entry can appear at most once."""
    from omf_retrieval.application.indexing.metadata import (
        RelationSidecarValidationError,
        parse_relation_sidecar,
    )

    duplicate = _valid_relation_entry()

    with pytest.raises(RelationSidecarValidationError):
        parse_relation_sidecar(
            _relation_payload(duplicate, duplicate),
            _relation_snapshot(),
        )


def test_potential_conflict_rejects_a_reverse_direction_duplicate() -> None:
    """A potential conflict is one undirected relation despite input direction."""
    from omf_retrieval.application.indexing.metadata import (
        RelationSidecarValidationError,
        parse_relation_sidecar,
    )

    forward = _valid_relation_entry(relation_type="potential_conflict")
    reverse = _valid_relation_entry(
        from_source_path="docs/planning/old.md",
        to_source_path="docs/planning/new.md",
        relation_type="potential_conflict",
        evidence_source_path="docs/planning/evidence-2.md",
    )

    with pytest.raises(RelationSidecarValidationError):
        parse_relation_sidecar(
            _relation_payload(forward, reverse),
            _relation_snapshot(),
        )


def test_reversed_supersedes_entries_remain_two_directional_relations() -> None:
    """Supersession direction is explicit and is neither inferred nor collapsed."""
    from omf_retrieval.application.indexing.metadata import parse_relation_sidecar

    forward = _valid_relation_entry()
    reverse = _valid_relation_entry(
        from_source_path="docs/planning/old.md",
        to_source_path="docs/planning/new.md",
        evidence_source_path="docs/planning/evidence-2.md",
    )

    relations = parse_relation_sidecar(
        _relation_payload(forward, reverse),
        _relation_snapshot(),
    )

    assert [
        (relation.from_source_path, relation.to_source_path) for relation in relations
    ] == [
        ("docs/planning/new.md", "docs/planning/old.md"),
        ("docs/planning/old.md", "docs/planning/new.md"),
    ]


@pytest.mark.parametrize(
    ("content", "line_start", "line_end"),
    [
        (b"one\r\n\r\nthree\r\n", 2, 3),
        (b"one\n", 1, 1),
        (b"one\ntwo", 2, 2),
        (b"\n\n", 2, 2),
    ],
    ids=["crlf-and-blank", "final-newline", "no-final-newline", "blank-lines"],
)
def test_evidence_range_uses_physical_markdown_lines(
    content: bytes,
    line_start: int,
    line_end: int,
) -> None:
    """CRLF, blank lines, and final-newline state retain physical coordinates."""
    from omf_retrieval.application.indexing.metadata import parse_relation_sidecar

    relation = parse_relation_sidecar(
        _relation_payload(
            _valid_relation_entry(
                evidence_line_start=line_start,
                evidence_line_end=line_end,
            )
        ),
        _relation_snapshot_with_evidence(content),
    )[0]

    assert relation.evidence_line_range == LineRange(
        line_start=line_start,
        line_end=line_end,
    )


@pytest.mark.parametrize(
    ("content", "line_start", "line_end"),
    [
        (b"one\n", 0, 1),
        (b"one\ntwo\n", 2, 1),
        (b"one\n", 1, 2),
        (b"one\n", 2, 2),
        (b"one\n", True, 1),
        (b"one\n", 1, False),
    ],
    ids=[
        "zero",
        "reversed",
        "end-out-of-range",
        "start-out-of-range",
        "boolean-start",
        "boolean-end",
    ],
)
def test_relation_sidecar_rejects_invalid_or_out_of_document_evidence_ranges(
    content: bytes,
    line_start: object,
    line_end: object,
) -> None:
    """Evidence is an exact one-based inclusive range inside its document."""
    from omf_retrieval.application.indexing.metadata import (
        RelationSidecarValidationError,
        parse_relation_sidecar,
    )

    with pytest.raises(RelationSidecarValidationError):
        parse_relation_sidecar(
            _relation_payload(
                _valid_relation_entry(
                    evidence_line_start=line_start,
                    evidence_line_end=line_end,
                )
            ),
            _relation_snapshot_with_evidence(content),
        )


@pytest.mark.parametrize(
    "content",
    [b"", b"\xffinvalid UTF-8"],
    ids=["empty-document", "invalid-utf8"],
)
def test_relation_sidecar_rejects_unmappable_evidence_documents(content: bytes) -> None:
    """Empty or non-UTF-8 evidence cannot supply a source line."""
    from omf_retrieval.application.indexing.metadata import (
        RelationSidecarValidationError,
        parse_relation_sidecar,
    )

    with pytest.raises(RelationSidecarValidationError):
        parse_relation_sidecar(
            _relation_payload(_valid_relation_entry()),
            _relation_snapshot_with_evidence(content),
        )


@pytest.mark.parametrize(
    "payload",
    [
        StringSubclass(_relation_payload(_valid_relation_entry())),
        b'{"relations": []}',
        1,
    ],
)
def test_relation_parser_rejects_nonexact_payload_types(payload: object) -> None:
    """Only an exact built-in string can cross the public JSON boundary."""
    from omf_retrieval.application.indexing.metadata import (
        RelationSidecarValidationError,
        parse_relation_sidecar,
    )

    with pytest.raises(RelationSidecarValidationError):
        parse_relation_sidecar(payload, _relation_snapshot())  # type: ignore[arg-type]


def test_relation_parser_rejects_a_snapshot_subclass() -> None:
    """Snapshot identity uses the exact immutable Task 4 public DTO."""
    from omf_retrieval.application.indexing.metadata import (
        RelationSidecarValidationError,
        parse_relation_sidecar,
    )

    snapshot = SourceSnapshotSubclass(
        commit_sha=VALID_COMMIT_SHA,
        archive_files=_relation_snapshot().archive_files,
    )

    with pytest.raises(RelationSidecarValidationError):
        parse_relation_sidecar(
            _relation_payload(_valid_relation_entry()),
            snapshot,
        )


@pytest.mark.parametrize(
    "constructor",
    [
        lambda: _document_relation_spec(
            from_source_path=StringSubclass("docs/planning/new.md")
        ),
        lambda: _document_relation_spec(from_source_path="./docs/planning/new.md"),
        lambda: _document_relation_spec(relation_type="supersedes"),
        lambda: _document_relation_spec(
            evidence_line_range=LineRangeSubclass(line_start=1, line_end=1)
        ),
        lambda: _document_relation_spec(
            evidence_line_range=LineRange(
                line_start=IntSubclass(1),
                line_end=1,
            )
        ),
        lambda: _document_relation_spec(
            evidence_line_range=LineRange(line_start=True, line_end=1)
        ),
        lambda: _document_relation_spec(to_source_path="docs/planning/new.md"),
    ],
)
def test_relation_dto_rejects_noncanonical_or_nonexact_values(
    constructor: object,
) -> None:
    """The frozen result cannot be directly constructed with weaker value types."""
    from omf_retrieval.application.indexing.metadata import (
        RelationSidecarValidationError,
    )

    with pytest.raises(RelationSidecarValidationError):
        constructor()  # type: ignore[operator]


def test_relation_errors_do_not_echo_payload_paths_or_evidence_bytes() -> None:
    """Validation diagnostics expose neither rejected JSON nor source content."""
    from omf_retrieval.application.indexing.metadata import (
        RelationSidecarValidationError,
        parse_relation_sidecar,
    )

    secret_path = "docs/planning/classified-token.md"
    secret_content = b"classified-evidence-bytes"
    payload = _relation_payload(
        _valid_relation_entry(
            evidence_source_path=secret_path,
            evidence_line_start=2,
            evidence_line_end=2,
        )
    )
    snapshot = _source_snapshot(
        ("docs/planning/new.md", b"New.\n"),
        ("docs/planning/old.md", b"Old.\n"),
        (secret_path, secret_content),
    )

    with pytest.raises(RelationSidecarValidationError) as error_info:
        parse_relation_sidecar(payload, snapshot)

    error_message = str(error_info.value)
    assert secret_path not in error_message
    assert secret_content.decode() not in error_message
    assert payload not in error_message


def test_relation_errors_discard_rejected_exception_context() -> None:
    """Malformed JSON cannot remain reachable through a validation error chain."""
    from omf_retrieval.application.indexing.metadata import (
        RelationSidecarValidationError,
        parse_relation_sidecar,
    )

    payload = '{"relations": ["classified-payload"'

    with pytest.raises(RelationSidecarValidationError) as error_info:
        parse_relation_sidecar(payload, _relation_snapshot())

    assert payload not in str(error_info.value)
    assert error_info.value.__cause__ is None
    assert error_info.value.__context__ is None


@pytest.mark.parametrize(
    ("payload", "forbidden_fragments"),
    [
        (
            '{"relations": [], "unknown": ' + "1234567890" * 500 + "}",
            ("1234567890", "Exceeds the limit", "5000 digits"),
        ),
        (
            "[" * 10_000 + '"classified-deep-json"' + "]" * 10_000,
            ("classified-deep-json", "maximum recursion depth"),
        ),
    ],
    ids=["oversized-integer", "excessive-nesting"],
)
def test_relation_json_decoder_failures_use_the_sanitized_public_error(
    payload: str,
    forbidden_fragments: tuple[str, ...],
) -> None:
    """Decoder resource failures cannot escape or retain rejected JSON."""
    from omf_retrieval.application.indexing.metadata import (
        RelationSidecarValidationError,
        parse_relation_sidecar,
    )

    validation_error: RelationSidecarValidationError | None = None
    try:
        parse_relation_sidecar(payload, _relation_snapshot())
    except RelationSidecarValidationError as error:
        validation_error = error
    except (ValueError, RecursionError):
        pass

    assert validation_error is not None, "decoder failures must use the public error"
    assert str(validation_error) == (
        "Relation sidecar must be valid duplicate-free JSON"
    )
    assert payload not in str(validation_error)
    assert all(
        fragment not in str(validation_error) for fragment in forbidden_fragments
    )
    assert validation_error.__cause__ is None
    assert validation_error.__context__ is None


def test_relation_parsing_is_deterministic_and_never_infers_relations() -> None:
    """Repeated explicit input is stable and document names create no relation."""
    from omf_retrieval.application.indexing.metadata import parse_relation_sidecar

    snapshot = _relation_snapshot()
    payload = _relation_payload(
        _valid_relation_entry(relation_type="potential_conflict")
    )

    first = parse_relation_sidecar(payload, snapshot)
    second = parse_relation_sidecar(payload, snapshot)

    assert first == second
    assert type(first) is tuple
    assert parse_relation_sidecar('{"relations": []}', snapshot) == ()


def _document_relation_spec(**overrides: object) -> object:
    from omf_retrieval.application.indexing.metadata import DocumentRelationSpec

    values: dict[str, object] = {
        "from_source_path": "docs/planning/new.md",
        "to_source_path": "docs/planning/old.md",
        "relation_type": RelationType.SUPERSEDES,
        "evidence_source_path": "docs/planning/evidence.md",
        "evidence_line_range": LineRange(line_start=1, line_end=1),
    }
    values.update(overrides)
    return DocumentRelationSpec(**values)  # type: ignore[arg-type]
