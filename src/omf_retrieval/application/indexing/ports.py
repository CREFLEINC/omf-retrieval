"""Application contracts for immutable source snapshots and parsed Markdown."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class SourceSnapshotValidationError(ValueError):
    """Raised when immutable source-snapshot values are invalid."""


class MarkdownStructureValidationError(ValueError):
    """Raised when immutable parsed-Markdown values are invalid."""


def split_physical_lines(source: str) -> tuple[str, ...]:
    """Split source at Markdown physical-line boundaries and retain endings.

    Args:
        source: Exact source string whose CR, LF, and CRLF boundaries are split.

    Returns:
        Immutable source slices whose concatenation exactly reproduces ``source``.
        Unicode separators other than CR and LF remain inside their source line.

    Raises:
        MarkdownStructureValidationError: If ``source`` is not an exact string.
    """
    if type(source) is not str:
        raise MarkdownStructureValidationError(
            "Physical-line source must be an exact string"
        )

    lines: list[str] = []
    line_start = 0
    index = 0
    while index < len(source):
        character = source[index]
        if character == "\r":
            index += 1
            if index < len(source) and source[index] == "\n":
                index += 1
            lines.append(source[line_start:index])
            line_start = index
        elif character == "\n":
            index += 1
            lines.append(source[line_start:index])
            line_start = index
        else:
            index += 1
    if line_start < len(source):
        lines.append(source[line_start:])
    return tuple(lines)


@dataclass(frozen=True, slots=True)
class ArchiveFile:
    """Represent source bytes at a canonical repository-relative path.

    Args:
        source_path: Canonical POSIX repository-relative source path.
        content: Original unmodified file bytes.

    Raises:
        SourceSnapshotValidationError: If the path or content is invalid.
    """

    source_path: str
    content: bytes

    def __post_init__(self) -> None:
        """Validate archive coordinates without exposing temporary paths."""
        _require_canonical_source_path(self.source_path)
        if type(self.content) is not bytes:
            raise SourceSnapshotValidationError("Archive content must be bytes")


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    """Represent an immutable archive-file set at one Git commit.

    Args:
        commit_sha: Lowercase full 40-character Git SHA-1 identifier.
        archive_files: Unique lexical-order archive files at that commit.

    Raises:
        SourceSnapshotValidationError: If commit or archive-file invariants fail.
    """

    commit_sha: str
    archive_files: tuple[ArchiveFile, ...]

    def __post_init__(self) -> None:
        """Validate snapshot identity and deterministic archive ordering."""
        if (
            type(self.commit_sha) is not str
            or re.fullmatch(r"[0-9a-f]{40}", self.commit_sha) is None
        ):
            raise SourceSnapshotValidationError(
                "Snapshot commit_sha must be a lowercase full Git SHA-1"
            )
        if type(self.archive_files) is not tuple:
            raise SourceSnapshotValidationError(
                "Snapshot archive_files must be a tuple"
            )
        if not all(
            type(archive_file) is ArchiveFile for archive_file in self.archive_files
        ):
            raise SourceSnapshotValidationError(
                "Snapshot archive_files must contain ArchiveFile values"
            )

        source_paths = tuple(
            archive_file.source_path for archive_file in self.archive_files
        )
        if len(set(source_paths)) != len(source_paths):
            raise SourceSnapshotValidationError("Snapshot archive files must be unique")
        if source_paths != tuple(sorted(source_paths)):
            raise SourceSnapshotValidationError(
                "Snapshot archive files must be in lexical source-path order"
            )


class SourceSnapshotProvider(Protocol):
    """Provide one immutable source snapshot from a repository commit."""

    def snapshot(self, repo: Path, commit_sha: str) -> SourceSnapshot:
        """Return the source snapshot identified by the requested commit."""


@dataclass(frozen=True, slots=True)
class ParsedBlock:
    """Represent one source-mapped Markdown block and its mapped children.

    Args:
        kind: Non-empty block kind such as ``paragraph`` or ``table_row``.
        raw_text: Exact source slice covered by this block.
        line_start: One-based inclusive first source line.
        line_end: One-based inclusive last source line.
        children: Exact immutable child blocks contained by this block's range.

    Raises:
        MarkdownStructureValidationError: If a type or source range is invalid.
    """

    kind: str
    raw_text: str
    line_start: int
    line_end: int
    children: tuple["ParsedBlock", ...]

    def __post_init__(self) -> None:
        """Validate exact value types and inclusive source coordinates."""
        if type(self.kind) is not str or not self.kind:
            raise MarkdownStructureValidationError(
                "Parsed block kind must be a non-empty exact string"
            )
        if type(self.raw_text) is not str:
            raise MarkdownStructureValidationError(
                "Parsed block raw_text must be an exact string"
            )
        _require_inclusive_line_range(self.line_start, self.line_end, "block")
        if type(self.children) is not tuple or not all(
            type(child) is ParsedBlock for child in self.children
        ):
            raise MarkdownStructureValidationError(
                "Parsed block children must be an exact ParsedBlock tuple"
            )
        if any(
            child.line_start < self.line_start or child.line_end > self.line_end
            for child in self.children
        ):
            raise MarkdownStructureValidationError(
                "Parsed block child range must be inside its parent"
            )


@dataclass(frozen=True, slots=True)
class ParsedSection:
    """Represent one heading-delimited Markdown section.

    Args:
        ordinal: Zero-based deterministic section position.
        parent_ordinal: Earlier parent section position, or ``None`` at the top.
        level: Markdown heading level, or zero for a synthetic root.
        heading: Raw inline heading content, or ``None`` for a synthetic root.
        heading_path: Immutable ancestor-to-current heading text path.
        body: Exact source after the heading and before the next section.
        line_start: One-based inclusive first section line, including its heading.
        line_end: One-based inclusive last section line.
        blocks: Exact immutable top-level blocks within the section body.

    Raises:
        MarkdownStructureValidationError: If hierarchy, type, or range is invalid.
    """

    ordinal: int
    parent_ordinal: int | None
    level: int
    heading: str | None
    heading_path: tuple[str, ...]
    body: str
    line_start: int
    line_end: int
    blocks: tuple[ParsedBlock, ...]

    def __post_init__(self) -> None:
        """Validate hierarchy, immutable containers, and source coordinates."""
        if type(self.ordinal) is not int or self.ordinal < 0:
            raise MarkdownStructureValidationError(
                "Parsed section ordinal must be a non-negative exact integer"
            )
        if self.parent_ordinal is not None and (
            type(self.parent_ordinal) is not int
            or self.parent_ordinal < 0
            or self.parent_ordinal >= self.ordinal
        ):
            raise MarkdownStructureValidationError(
                "Parsed section parent must reference an earlier ordinal"
            )
        if type(self.level) is not int or not 0 <= self.level <= 6:
            raise MarkdownStructureValidationError(
                "Parsed section level must be an exact integer from zero to six"
            )
        if type(self.heading_path) is not tuple or not all(
            type(part) is str for part in self.heading_path
        ):
            raise MarkdownStructureValidationError(
                "Parsed section heading_path must be an exact string tuple"
            )
        if self.level == 0:
            if self.heading is not None or self.heading_path:
                raise MarkdownStructureValidationError(
                    "Synthetic root sections cannot have a heading"
                )
        elif (
            type(self.heading) is not str
            or not self.heading_path
            or self.heading_path[-1] != self.heading
        ):
            raise MarkdownStructureValidationError(
                "Heading sections require a matching exact heading path"
            )
        if type(self.body) is not str:
            raise MarkdownStructureValidationError(
                "Parsed section body must be an exact string"
            )
        _require_inclusive_line_range(self.line_start, self.line_end, "section")
        if type(self.blocks) is not tuple or not all(
            type(block) is ParsedBlock for block in self.blocks
        ):
            raise MarkdownStructureValidationError(
                "Parsed section blocks must be an exact ParsedBlock tuple"
            )
        if any(
            block.line_start < self.line_start or block.line_end > self.line_end
            for block in self.blocks
        ):
            raise MarkdownStructureValidationError(
                "Parsed section block range must be inside its section"
            )


@dataclass(frozen=True, slots=True)
class ParsedMarkdown:
    """Represent deterministic immutable output from a Markdown parser.

    Args:
        parser_version: Non-empty identifier for the parser behavior contract.
        sections: Exact immutable sections in sequential ordinal order.

    Raises:
        MarkdownStructureValidationError: If identity or section order is invalid.
    """

    parser_version: str
    sections: tuple[ParsedSection, ...]

    def __post_init__(self) -> None:
        """Validate parser identity and deterministic section ordering."""
        if type(self.parser_version) is not str or not self.parser_version:
            raise MarkdownStructureValidationError(
                "Parser version must be a non-empty exact string"
            )
        if type(self.sections) is not tuple or not all(
            type(section) is ParsedSection for section in self.sections
        ):
            raise MarkdownStructureValidationError(
                "Parsed Markdown sections must be an exact ParsedSection tuple"
            )
        if any(
            section.ordinal != expected_ordinal
            for expected_ordinal, section in enumerate(self.sections)
        ):
            raise MarkdownStructureValidationError(
                "Parsed Markdown section ordinals must be sequential"
            )


class MarkdownParser(Protocol):
    """Define deterministic Markdown parsing without storage-layer identifiers."""

    def parse(self, source: str) -> ParsedMarkdown:
        """Parse an exact source string into immutable source-mapped sections.

        Args:
            source: Original Markdown text whose line endings must be retained.

        Returns:
            Deterministic parsed sections and block source maps.

        Raises:
            MarkdownStructureValidationError: If ``source`` is not an exact string.
        """


def _require_inclusive_line_range(
    line_start: object, line_end: object, subject: str
) -> None:
    if (
        type(line_start) is not int
        or type(line_end) is not int
        or line_start < 1
        or line_end < line_start
    ):
        raise MarkdownStructureValidationError(
            f"Parsed {subject} lines must be a positive inclusive range"
        )


def _require_canonical_source_path(source_path: object) -> None:
    if type(source_path) is not str or not source_path:
        raise SourceSnapshotValidationError("Archive source_path must be non-empty")
    if "\x00" in source_path or "\\" in source_path or source_path.startswith("/"):
        raise SourceSnapshotValidationError(
            "Archive source_path must be a relative POSIX path"
        )

    path_parts = source_path.split("/")
    if any(not part or part in {".", ".."} for part in path_parts):
        raise SourceSnapshotValidationError(
            "Archive source_path must already be canonical"
        )
