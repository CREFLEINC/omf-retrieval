"""Extract conservative occurrence metadata and explicit document relations."""

import json
import re
from dataclasses import dataclass
from datetime import date

from omf_retrieval.application.indexing.ports import SourceSnapshot
from omf_retrieval.domain.enums import (
    DecisionState,
    OwnerDomain,
    RelationType,
    VersionScope,
)
from omf_retrieval.domain.models import DocumentMetadata, LineRange
from omf_retrieval.infrastructure.source.profiles import (
    SourceProfileValidationError,
    canonical_source_path,
)

_TOP_LINE_LIMIT = 40
_ISO_DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")
_FILENAME_DATE_PATTERN = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})(?=$|[-_])")
_VERSION_PATTERN = re.compile(r"v(?P<version>\d+(?:\.\d+)*)")
_FILENAME_VERSION_PATTERN = re.compile(
    r"(?:^|[-_])v(?P<version>\d+(?:\.\d+)*)(?=$|[-_])"
)
_MARKDOWN_TITLE_PATTERN = re.compile(r"^ {0,3}#{1,6}\s+(?P<title>.*?)\s*#*\s*$")
_FENCE_OPEN_PATTERN = re.compile(r"^ {0,3}(?P<fence>`{3,}|~{3,})(?P<info>.*)$")
_FENCE_CLOSE_PATTERN = re.compile(r"^ {0,3}(?P<fence>`{3,}|~{3,})[ \t]*$")
_CONFIRMED_FILENAME_MARKERS = ("확정기록", "결정서")
_NEGATIVE_CONFIRMED_MARKERS = ("미확정", "불확정", "확정 전")
_DRAFT_MARKERS = ("초안", "제안안", "가설", "진행메모")
_DECISION_KEYS = {"상태", "결정 상태", "결정상태", "문서 상태", "문서상태"}
_CONFIRMED_VALUES = {"확정", "[확정]"}
_RELATION_ROOT_KEYS = {"relations"}
_RELATION_ENTRY_KEYS = {
    "from_source_path",
    "to_source_path",
    "relation_type",
    "evidence_source_path",
    "evidence_line_start",
    "evidence_line_end",
}


class MetadataExtractionError(ValueError):
    """Raised when occurrence metadata input violates its public contract."""


class RelationSidecarValidationError(ValueError):
    """Raised when an explicit document-relation sidecar is invalid."""


@dataclass(frozen=True, slots=True)
class DocumentRelationSpec:
    """Represent one explicit relation and its source evidence."""

    from_source_path: str
    to_source_path: str
    relation_type: RelationType
    evidence_source_path: str
    evidence_line_range: LineRange

    def __post_init__(self) -> None:
        """Validate immutable canonical relation values."""
        for source_path in (
            self.from_source_path,
            self.to_source_path,
            self.evidence_source_path,
        ):
            if (
                type(source_path) is not str
                or _canonical_relation_path(source_path) != source_path
            ):
                raise RelationSidecarValidationError(
                    "Relation paths must be canonical exact strings"
                )
        if self.from_source_path == self.to_source_path:
            raise RelationSidecarValidationError(
                "A document cannot have a relation to itself"
            )
        if type(self.relation_type) is not RelationType:
            raise RelationSidecarValidationError(
                "Relation type must use the approved enum"
            )
        if type(self.evidence_line_range) is not LineRange or (
            type(self.evidence_line_range.line_start) is not int
            or type(self.evidence_line_range.line_end) is not int
        ):
            raise RelationSidecarValidationError(
                "Relation evidence must use an exact line range"
            )


class _DuplicateJsonKey(ValueError):
    """Signal a duplicate key without retaining rejected JSON content."""


def parse_relation_sidecar(
    payload: str,
    snapshot: SourceSnapshot,
) -> tuple[DocumentRelationSpec, ...]:
    """Parse explicit relation JSON for one immutable source snapshot.

    Args:
        payload: Committed relation-sidecar JSON text.
        snapshot: Filtered source files from the same immutable snapshot.

    Returns:
        Relation specifications in input order.

    Raises:
        RelationSidecarValidationError: If JSON or snapshot invariants fail.
    """
    if type(payload) is not str or type(snapshot) is not SourceSnapshot:
        raise RelationSidecarValidationError(
            "Relation sidecar inputs must use exact public types"
        )
    json_is_valid, raw_sidecar = _load_relation_json(payload)
    if not json_is_valid:
        raise RelationSidecarValidationError(
            "Relation sidecar must be valid duplicate-free JSON"
        )
    if type(raw_sidecar) is not dict or set(raw_sidecar) != _RELATION_ROOT_KEYS:
        raise RelationSidecarValidationError(
            "Relation sidecar root keys must match the contract"
        )

    raw_relations = raw_sidecar["relations"]
    if type(raw_relations) is not list:
        raise RelationSidecarValidationError(
            "Relation sidecar relations must be a JSON array"
        )

    snapshot_files = {
        archive_file.source_path: archive_file
        for archive_file in snapshot.archive_files
    }
    relations: list[DocumentRelationSpec] = []
    seen_relations: set[DocumentRelationSpec] = set()
    seen_conflict_pairs: set[tuple[str, str]] = set()
    for raw_relation in raw_relations:
        relation = _parse_relation_entry(raw_relation)
        if any(
            source_path not in snapshot_files
            for source_path in (
                relation.from_source_path,
                relation.to_source_path,
                relation.evidence_source_path,
            )
        ):
            raise RelationSidecarValidationError(
                "Every relation path must exist in the supplied snapshot"
            )
        _validate_evidence_range(
            relation.evidence_line_range,
            snapshot_files[relation.evidence_source_path].content,
        )
        if relation in seen_relations:
            raise RelationSidecarValidationError(
                "An exact relation entry cannot be repeated"
            )
        if relation.relation_type is RelationType.POTENTIAL_CONFLICT:
            conflict_pair = tuple(
                sorted((relation.from_source_path, relation.to_source_path))
            )
            if conflict_pair in seen_conflict_pairs:
                raise RelationSidecarValidationError(
                    "A potential conflict can be declared only once"
                )
            seen_conflict_pairs.add(conflict_pair)
        relations.append(relation)
        seen_relations.add(relation)
    return tuple(relations)


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey
        result[key] = value
    return result


def _load_relation_json(payload: str) -> tuple[bool, object]:
    try:
        return True, json.loads(payload, object_pairs_hook=_unique_json_object)
    except (json.JSONDecodeError, _DuplicateJsonKey):
        return False, None


def _parse_relation_entry(raw_relation: object) -> DocumentRelationSpec:
    if type(raw_relation) is not dict or set(raw_relation) != _RELATION_ENTRY_KEYS:
        raise RelationSidecarValidationError(
            "Relation entry keys must match the contract"
        )
    if any(
        type(raw_relation[field_name]) is not str
        for field_name in (
            "from_source_path",
            "to_source_path",
            "relation_type",
            "evidence_source_path",
        )
    ) or any(
        type(raw_relation[field_name]) is not int
        for field_name in ("evidence_line_start", "evidence_line_end")
    ):
        raise RelationSidecarValidationError(
            "Relation entry values must use exact JSON types"
        )

    relation_type_value = raw_relation["relation_type"]
    try:
        relation_type = RelationType(relation_type_value)
    except ValueError:
        relation_type = None
    if relation_type is None:
        raise RelationSidecarValidationError(
            "Relation type must be one of the approved values"
        )

    evidence_line_start = raw_relation["evidence_line_start"]
    evidence_line_end = raw_relation["evidence_line_end"]
    if evidence_line_start < 1 or evidence_line_end < evidence_line_start:
        raise RelationSidecarValidationError(
            "Relation evidence must be a positive inclusive line range"
        )

    return DocumentRelationSpec(
        from_source_path=_canonical_relation_path(raw_relation["from_source_path"]),
        to_source_path=_canonical_relation_path(raw_relation["to_source_path"]),
        relation_type=relation_type,
        evidence_source_path=_canonical_relation_path(
            raw_relation["evidence_source_path"]
        ),
        evidence_line_range=LineRange(
            line_start=evidence_line_start,
            line_end=evidence_line_end,
        ),
    )


def _canonical_relation_path(source_path: str) -> str:
    try:
        canonical_path = canonical_source_path(source_path)
    except SourceProfileValidationError:
        canonical_path = None
    if canonical_path is None:
        raise RelationSidecarValidationError(
            "Relation path must be a safe repository-relative POSIX path"
        )
    return canonical_path


def _validate_evidence_range(line_range: LineRange, content: bytes) -> None:
    try:
        source = content.decode("utf-8")
    except UnicodeDecodeError:
        source = None
    if source is None:
        raise RelationSidecarValidationError(
            "Relation evidence document must be valid UTF-8"
        )

    physical_line_count = len(source.splitlines(keepends=True))
    if line_range.line_end > physical_line_count:
        raise RelationSidecarValidationError(
            "Relation evidence range must exist in its source document"
        )


def extract_metadata(
    source_path: str,
    first_lines: tuple[str, ...],
) -> DocumentMetadata:
    """Return explicit metadata attached to one source-path occurrence.

    Args:
        source_path: Canonical repository-relative POSIX source path.
        first_lines: Immutable physical source lines, of which at most 40 are read.

    Returns:
        Metadata derived only from the approved explicit signals.

    Raises:
        MetadataExtractionError: If an input is not exact, immutable, and safe.
    """
    _require_exact_inputs(source_path=source_path, first_lines=first_lines)
    path_parts = _canonical_path_parts(source_path)
    top_lines = first_lines[:_TOP_LINE_LIMIT]
    metadata_lines = _metadata_lines(top_lines)
    filename_stem = path_parts[-1].removesuffix(".md")
    document_date = _document_date(metadata_lines, filename_stem=filename_stem)
    version = _document_version(metadata_lines, filename_stem=filename_stem)
    decision_state = _decision_state(
        filename_stem=filename_stem,
        lines=metadata_lines,
    )
    return DocumentMetadata(
        document_date=document_date,
        version=version,
        version_scope=(
            VersionScope.HISTORICAL
            if "versions" in path_parts
            else VersionScope.CURRENT
        ),
        decision_state=decision_state,
        owner_domain=_owner_domain(path_parts),
    )


def _require_exact_inputs(
    *,
    source_path: object,
    first_lines: object,
) -> None:
    if type(source_path) is not str or type(first_lines) is not tuple:
        raise MetadataExtractionError(
            "Metadata input types must be exact and immutable"
        )
    if any(
        type(line) is not str or not _is_physical_line(line) for line in first_lines
    ):
        raise MetadataExtractionError(
            "Metadata input lines must be exact physical strings"
        )


def _is_physical_line(line: str) -> bool:
    line_without_ending = line
    if line_without_ending.endswith("\n"):
        line_without_ending = line_without_ending[:-1]
        if line_without_ending.endswith("\r"):
            line_without_ending = line_without_ending[:-1]
    return "\n" not in line_without_ending and "\r" not in line_without_ending


def _metadata_lines(lines: tuple[str, ...]) -> tuple[str, ...]:
    metadata_lines: list[str] = []
    fence_character: str | None = None
    fence_length = 0
    in_html_comment = False

    for line in lines:
        line_content = line.rstrip("\r\n")
        if fence_character is not None:
            close_match = _FENCE_CLOSE_PATTERN.fullmatch(line_content)
            if close_match is not None:
                closing_fence = close_match.group("fence")
                if (
                    closing_fence[0] == fence_character
                    and len(closing_fence) >= fence_length
                ):
                    fence_character = None
                    fence_length = 0
            continue

        if in_html_comment:
            in_html_comment = _html_comment_remains_open(
                line_content, already_open=True
            )
            continue

        if _is_indented_code_line(line_content):
            continue

        open_match = _FENCE_OPEN_PATTERN.fullmatch(line_content)
        if open_match is not None:
            opening_fence = open_match.group("fence")
            info = open_match.group("info")
            if opening_fence[0] == "~" or "`" not in info:
                fence_character = opening_fence[0]
                fence_length = len(opening_fence)
                continue

        if "<!--" in line_content:
            in_html_comment = _html_comment_remains_open(
                line_content, already_open=False
            )
            continue

        metadata_lines.append(line)

    return tuple(metadata_lines)


def _is_indented_code_line(line: str) -> bool:
    indentation_columns = 0
    for character in line:
        if character == " ":
            indentation_columns += 1
        elif character == "\t":
            indentation_columns += 4 - indentation_columns % 4
        else:
            break
        if indentation_columns >= 4:
            return True
    return False


def _html_comment_remains_open(line: str, *, already_open: bool) -> bool:
    cursor = 0
    if already_open:
        closing_index = line.find("-->", cursor)
        if closing_index < 0:
            return True
        cursor = closing_index + len("-->")

    while True:
        opening_index = line.find("<!--", cursor)
        if opening_index < 0:
            return False
        closing_index = line.find("-->", opening_index + len("<!--"))
        if closing_index < 0:
            return True
        cursor = closing_index + len("-->")


def _canonical_path_parts(source_path: str) -> tuple[str, ...]:
    if (
        not source_path
        or "\x00" in source_path
        or "\\" in source_path
        or source_path.startswith("/")
    ):
        raise MetadataExtractionError("Source path must be canonical and safe")

    path_parts = tuple(source_path.split("/"))
    if any(not part or part in {".", ".."} for part in path_parts):
        raise MetadataExtractionError("Source path must be canonical and safe")
    return path_parts


def _owner_domain(path_parts: tuple[str, ...]) -> OwnerDomain:
    if path_parts[0] == "docs":
        return OwnerDomain.DOCS
    if path_parts[0] == "uiux":
        return OwnerDomain.UIUX
    raise MetadataExtractionError("Source path owner must be docs or uiux")


def _decision_state(
    *,
    filename_stem: str,
    lines: tuple[str, ...],
) -> DecisionState:
    confirmed = _confirmed_filename(filename_stem) or _structured_confirmed(lines)
    draft = _contains_marker(filename_stem, _DRAFT_MARKERS) or _draft_title(lines)
    if confirmed == draft:
        return DecisionState.UNKNOWN
    return DecisionState.CONFIRMED if confirmed else DecisionState.DRAFT


def _confirmed_filename(filename_stem: str) -> bool:
    return not _contains_marker(
        filename_stem, _NEGATIVE_CONFIRMED_MARKERS
    ) and _contains_marker(filename_stem, _CONFIRMED_FILENAME_MARKERS)


def _structured_confirmed(lines: tuple[str, ...]) -> bool:
    for entry_kind, key, value in _structured_entries(lines):
        if key in _DECISION_KEYS and value in _CONFIRMED_VALUES:
            return True
        if entry_kind == "table" and key == "신뢰도" and value == "확정":
            return True
    return False


def _draft_title(lines: tuple[str, ...]) -> bool:
    for line in lines:
        title_match = _MARKDOWN_TITLE_PATTERN.fullmatch(line.strip("\r\n"))
        if title_match is not None:
            return _contains_marker(title_match.group("title"), _DRAFT_MARKERS)
    return False


def _contains_marker(value: str, markers: tuple[str, ...]) -> bool:
    return any(marker in value for marker in markers)


def _document_date(
    lines: tuple[str, ...],
    *,
    filename_stem: str,
) -> date | None:
    structured_date = _structured_value(lines, key="작성일")
    parsed_date = _parse_date(structured_date)
    if parsed_date is not None:
        return parsed_date

    filename_match = _FILENAME_DATE_PATTERN.match(filename_stem)
    return _parse_date(filename_match.group("date") if filename_match else None)


def _document_version(
    lines: tuple[str, ...],
    *,
    filename_stem: str,
) -> str | None:
    structured_version = _structured_value(lines, key="버전")
    structured_match = (
        _VERSION_PATTERN.fullmatch(structured_version)
        if structured_version is not None
        else None
    )
    if structured_match is not None:
        return structured_match.group("version")

    filename_match = _FILENAME_VERSION_PATTERN.search(filename_stem)
    return filename_match.group("version") if filename_match else None


def _parse_date(candidate: str | None) -> date | None:
    if candidate is None or _ISO_DATE_PATTERN.fullmatch(candidate) is None:
        return None
    try:
        return date.fromisoformat(candidate)
    except ValueError:
        return None


def _structured_value(lines: tuple[str, ...], *, key: str) -> str | None:
    for _, entry_key, value in _structured_entries(lines):
        if entry_key == key:
            return value
    return None


def _structured_entries(
    lines: tuple[str, ...],
) -> tuple[tuple[str, str, str], ...]:
    entries: list[tuple[str, str, str]] = []
    for line in lines:
        stripped_line = line.strip()
        if stripped_line.startswith("|") and stripped_line.endswith("|"):
            cells = tuple(cell.strip() for cell in stripped_line[1:-1].split("|"))
            if len(cells) >= 2:
                entries.append(("table", cells[0], cells[1]))
            continue

        key, separator, value = stripped_line.partition(":")
        if separator:
            entries.append(("key_value", key.strip(), value.strip()))
    return tuple(entries)
