"""Application contracts for immutable source snapshots and parsed Markdown."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class SourceSnapshotValidationError(ValueError):
    """Raised when immutable source-snapshot values are invalid."""


class MarkdownStructureValidationError(ValueError):
    """Raised when immutable parsed-Markdown values are invalid."""


class TokenCounter(Protocol):
    """Describe token IDs and their exact source-backed character offsets.

    Implementations return one offset for every encoded token. Offsets satisfy
    ``0 <= start < end <= len(text)``. Consecutive tokenizer byte-fallback
    offsets may be identical, contained, or grow one connected source union;
    token starts never decrease and all other spans are non-overlapping.
    Special-token zero-length offsets are never returned.
    """

    def encode(self, text: str) -> Sequence[int]:
        """Encode source text without losing deterministic token order.

        Args:
            text: Exact source text to tokenize.

        Returns:
            Token identifiers aligned one-for-one with ``offsets(text)``.
        """

    def offsets(self, text: str) -> Sequence[tuple[int, int]]:
        """Return exact character spans for the encoded source tokens.

        Args:
            text: Exact source text passed to ``encode``.

        Returns:
            Nondecreasing half-open source spans grouped by connected unions.
        """


@dataclass(frozen=True, slots=True)
class TokenizerDescriptor:
    """Identify the exact tokenizer behavior used for chunk boundaries.

    Args:
        model_name: Non-blank tokenizer model identifier.
        revision: Non-blank immutable tokenizer revision.
        library_name: Non-blank tokenizer library identifier.
        library_version: Non-blank installed tokenizer library version.
        add_special_tokens: Whether tokenization includes special tokens.

    Raises:
        ValueError: If any value is blank or not its exact built-in type.
    """

    model_name: str
    revision: str
    library_name: str
    library_version: str
    add_special_tokens: bool = False

    def __post_init__(self) -> None:
        """Validate exact tokenizer identity values."""
        identity_fields = (
            ("model_name", self.model_name),
            ("revision", self.revision),
            ("library_name", self.library_name),
            ("library_version", self.library_version),
        )
        if any(
            type(value) is not str or not value.strip() for _, value in identity_fields
        ):
            raise ValueError(
                "Tokenizer identity fields must be non-blank exact strings"
            )
        if type(self.add_special_tokens) is not bool:
            raise ValueError("Tokenizer add_special_tokens must be an exact boolean")


@dataclass(frozen=True, slots=True)
class ChunkConfig:
    """Define deterministic child and parent token limits.

    Args:
        target_tokens: Preferred child size for ordinary text.
        soft_max_tokens: Largest ordinary child size before a required split.
        overlap_tokens: Prior-child token count repeated in the next child.
        atomic_max_tokens: Largest preserved table, list, or quote unit.
        parent_context_max_tokens: Largest parent context returned with a match.

    Raises:
        ValueError: If values are not exact integers or token limits conflict.
    """

    target_tokens: int = 400
    soft_max_tokens: int = 600
    overlap_tokens: int = 64
    atomic_max_tokens: int = 800
    parent_context_max_tokens: int = 1200

    def __post_init__(self) -> None:
        """Validate exact integer types and coherent token limits."""
        token_limits = (
            self.target_tokens,
            self.soft_max_tokens,
            self.overlap_tokens,
            self.atomic_max_tokens,
            self.parent_context_max_tokens,
        )
        if any(type(token_limit) is not int for token_limit in token_limits):
            raise ValueError("Chunk token limits must be exact integers")
        if (
            self.target_tokens <= 0
            or self.soft_max_tokens <= 0
            or self.atomic_max_tokens <= 0
            or self.parent_context_max_tokens <= 0
        ):
            raise ValueError("Chunk size limits must be positive")
        if not self.target_tokens <= self.soft_max_tokens <= self.atomic_max_tokens:
            raise ValueError("Chunk size limits must be monotonically ordered")
        if not 0 <= self.overlap_tokens < self.target_tokens:
            raise ValueError("Chunk overlap must be smaller than the target")


_OVERSIZED_ATOMIC_UNIT_WARNING_CODE = "oversized_atomic_unit_token_split"


@dataclass(frozen=True, slots=True)
class ChunkWarning:
    """Represent safe metadata for a forced oversized atomic-unit split.

    Args:
        block_kind: Non-empty parser block kind that required token splitting.
        line_start: One-based inclusive first affected source line.
        line_end: One-based inclusive last affected source line.
        code: Stable warning code for oversized atomic-unit token splitting.

    Raises:
        ValueError: If the code, block kind, or source range is invalid.
    """

    block_kind: str
    line_start: int
    line_end: int
    code: str = _OVERSIZED_ATOMIC_UNIT_WARNING_CODE

    def __post_init__(self) -> None:
        """Validate stable safe warning metadata."""
        if (
            type(self.code) is not str
            or self.code != _OVERSIZED_ATOMIC_UNIT_WARNING_CODE
        ):
            raise ValueError("Chunk warning code must be the stable approved value")
        if type(self.block_kind) is not str or not self.block_kind:
            raise ValueError(
                "Chunk warning block_kind must be a non-empty exact string"
            )
        _require_chunk_inclusive_line_range(self.line_start, self.line_end)


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    """Represent immutable storage-ready output from the child chunker.

    Args:
        ordinal: Zero-based deterministic child position within its section.
        raw_text: Non-empty exact source excerpt retained by the child.
        search_text: Non-empty heading-enriched text used for retrieval.
        token_count: Positive token count for ``search_text``.
        line_start: One-based inclusive first excerpt source line.
        line_end: One-based inclusive last excerpt source line.
        chunk_hash: Lowercase 64-character hexadecimal child identity digest.
        warnings: Exact immutable tuple of safe child warnings.

    Raises:
        ValueError: If a type, value, source range, hash, or warning is invalid.
    """

    ordinal: int
    raw_text: str
    search_text: str
    token_count: int
    line_start: int
    line_end: int
    chunk_hash: str
    warnings: tuple[ChunkWarning, ...] = ()

    def __post_init__(self) -> None:
        """Validate deterministic chunk output without generating its identity."""
        if type(self.ordinal) is not int or self.ordinal < 0:
            raise ValueError("Chunk ordinal must be a non-negative exact integer")
        if type(self.raw_text) is not str or not self.raw_text:
            raise ValueError("Chunk raw_text must be a non-empty exact string")
        if type(self.search_text) is not str or not self.search_text:
            raise ValueError("Chunk search_text must be a non-empty exact string")
        if type(self.token_count) is not int or self.token_count <= 0:
            raise ValueError("Chunk token_count must be a positive exact integer")
        _require_chunk_inclusive_line_range(self.line_start, self.line_end)
        if (
            type(self.chunk_hash) is not str
            or re.fullmatch(r"[0-9a-f]{64}", self.chunk_hash) is None
        ):
            raise ValueError("Chunk hash must be lowercase 64-character hexadecimal")
        if type(self.warnings) is not tuple or not all(
            type(warning) is ChunkWarning for warning in self.warnings
        ):
            raise ValueError("Chunk warnings must be an exact ChunkWarning tuple")


@dataclass(frozen=True, slots=True)
class ParentContext:
    """Represent an immutable source-backed context returned around a match.

    Args:
        raw_text: Non-empty contiguous source excerpt without a heading prefix.
        token_count: Positive token count for ``raw_text``.
        line_start: One-based inclusive first excerpt source line.
        line_end: One-based inclusive last excerpt source line.

    Raises:
        ValueError: If a type, value, or inclusive source range is invalid.
    """

    raw_text: str
    token_count: int
    line_start: int
    line_end: int

    def __post_init__(self) -> None:
        """Validate exact source text, token count, and inclusive lines."""
        if type(self.raw_text) is not str or not self.raw_text:
            raise ValueError("Parent context raw_text must be a non-empty exact string")
        if type(self.token_count) is not int or self.token_count <= 0:
            raise ValueError(
                "Parent context token_count must be a positive exact integer"
            )
        _require_chunk_inclusive_line_range(self.line_start, self.line_end)


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
        excluded_file_count: Non-directory files rejected by the source profile.

    Raises:
        SourceSnapshotValidationError: If commit or archive-file invariants fail.
    """

    commit_sha: str
    archive_files: tuple[ArchiveFile, ...]
    excluded_file_count: int

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
        if type(self.excluded_file_count) is not int or self.excluded_file_count < 0:
            raise SourceSnapshotValidationError(
                "Snapshot excluded_file_count must be a non-negative exact integer"
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


def _require_chunk_inclusive_line_range(line_start: object, line_end: object) -> None:
    if (
        type(line_start) is not int
        or type(line_end) is not int
        or line_start < 1
        or line_end < line_start
    ):
        raise ValueError("Chunk lines must be a positive inclusive range")


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
