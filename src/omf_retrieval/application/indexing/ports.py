"""Application contracts for immutable source snapshots."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class SourceSnapshotValidationError(ValueError):
    """Raised when immutable source-snapshot values are invalid."""


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
        if not isinstance(self.content, bytes):
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
            not isinstance(self.commit_sha, str)
            or re.fullmatch(r"[0-9a-f]{40}", self.commit_sha) is None
        ):
            raise SourceSnapshotValidationError(
                "Snapshot commit_sha must be a lowercase full Git SHA-1"
            )
        if not isinstance(self.archive_files, tuple):
            raise SourceSnapshotValidationError(
                "Snapshot archive_files must be a tuple"
            )
        if not all(
            isinstance(archive_file, ArchiveFile) for archive_file in self.archive_files
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


def _require_canonical_source_path(source_path: object) -> None:
    if not isinstance(source_path, str) or not source_path:
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
