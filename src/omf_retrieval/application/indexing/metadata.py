"""Extract conservative metadata for one source-path occurrence."""

import re
from datetime import date

from omf_retrieval.domain.enums import DecisionState, OwnerDomain, VersionScope
from omf_retrieval.domain.models import DocumentMetadata

_TOP_LINE_LIMIT = 40
_ISO_DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")
_FILENAME_DATE_PATTERN = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})(?=$|[-_])")
_VERSION_PATTERN = re.compile(r"v(?P<version>\d+(?:\.\d+)*)")
_FILENAME_VERSION_PATTERN = re.compile(
    r"(?:^|[-_.()\[\]\s])v(?P<version>\d+(?:\.\d+)*)(?=$|[-_.()\[\]\s])"
)
_MARKDOWN_TITLE_PATTERN = re.compile(r"^ {0,3}#{1,6}\s+(?P<title>.*?)\s*#*\s*$")
_CONFIRMED_FILENAME_MARKERS = ("확정기록", "결정서")
_NEGATIVE_CONFIRMED_MARKERS = ("미확정", "불확정", "확정 전")
_DRAFT_MARKERS = ("초안", "제안안", "가설", "진행메모")
_DECISION_KEYS = {"상태", "결정 상태", "결정상태", "문서 상태", "문서상태"}
_CONFIRMED_VALUES = {"확정", "[확정]"}


class MetadataExtractionError(ValueError):
    """Raised when occurrence metadata input violates its public contract."""


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
    filename_stem = path_parts[-1].removesuffix(".md")
    document_date = _document_date(top_lines, filename_stem=filename_stem)
    version = _document_version(top_lines, filename_stem=filename_stem)
    decision_state = _decision_state(
        filename_stem=filename_stem,
        lines=top_lines,
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
