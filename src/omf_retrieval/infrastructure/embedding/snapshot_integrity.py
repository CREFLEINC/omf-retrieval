"""Descriptor-pinned, bounded hashing of embedding-model snapshots."""

from __future__ import annotations

import hashlib
import os
import stat
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from omf_retrieval.infrastructure.embedding.manifest_contract import (
    MAX_MODEL_DIRECTORY_COUNT,
    MAX_MODEL_FILE_BYTES,
    MAX_MODEL_FILE_COUNT,
    MAX_MODEL_TOTAL_BYTES,
    ModelManifestError,
    is_approved_model_path,
    validated_relative_path,
)

_READ_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class PinnedModelSnapshot:
    """Hold an open snapshot root whose filesystem identity cannot be substituted."""

    path: Path
    descriptor: int
    device: int
    inode: int

    def matches_path(self) -> bool:
        """Return whether the published coordinate still names this open directory."""
        try:
            metadata = os.stat(self.path, follow_symlinks=False)
            return stat.S_ISDIR(metadata.st_mode) and (
                metadata.st_dev,
                metadata.st_ino,
            ) == (self.device, self.inode)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            return False


@contextmanager
def pin_model_snapshot(
    snapshot_dir: Path, *, cache_dir: Path
) -> Iterator[PinnedModelSnapshot]:
    """Open every path component without following symlinks and pin the root."""
    descriptor = -1
    try:
        cache_root = cache_dir.expanduser().resolve(strict=False)
        absolute_snapshot = snapshot_dir.absolute()
        try:
            relative = absolute_snapshot.relative_to(cache_root)
        except ValueError:
            raise ModelManifestError("Embedding model manifest is invalid") from None
        descriptor = _open_directory(cache_root)
        for part in relative.parts:
            child = os.open(
                part,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = child
        metadata = os.fstat(descriptor)
        path_metadata = os.stat(absolute_snapshot, follow_symlinks=False)
        if not stat.S_ISDIR(metadata.st_mode) or (
            metadata.st_dev,
            metadata.st_ino,
        ) != (path_metadata.st_dev, path_metadata.st_ino):
            raise ModelManifestError("Embedding model manifest is invalid")
        pinned = PinnedModelSnapshot(
            path=absolute_snapshot,
            descriptor=descriptor,
            device=metadata.st_dev,
            inode=metadata.st_ino,
        )
        if not pinned.matches_path():
            raise ModelManifestError("Embedding model manifest is invalid")
        yield pinned
    except (KeyboardInterrupt, SystemExit):
        raise
    except ModelManifestError:
        raise
    except Exception:
        raise ModelManifestError("Embedding model manifest is invalid") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


@dataclass(frozen=True, slots=True)
class _OpenSnapshotEntry:
    relative_path: str
    descriptor: int
    metadata: os.stat_result


def snapshot_files(snapshot: PinnedModelSnapshot) -> list[dict[str, Any]]:
    """Preflight and hash a pinned snapshot without materializing directory names."""
    open_files: list[_OpenSnapshotEntry] = []
    open_directories: list[_OpenSnapshotEntry] = []
    canonical_keys: set[str] = set()
    total_bytes = 0
    directory_count = 0
    try:
        root_descriptor = os.open(
            ".",
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=snapshot.descriptor,
        )
        root_metadata = os.fstat(root_descriptor)
        root_entry = _OpenSnapshotEntry("", root_descriptor, root_metadata)
        open_directories.append(root_entry)
        pending = [root_entry]
        while pending:
            directory = pending.pop()
            with os.scandir(directory.descriptor) as entries:
                for entry in entries:
                    name = entry.name
                    relative = (
                        f"{directory.relative_path}/{name}"
                        if directory.relative_path
                        else name
                    )
                    canonical = validated_relative_path(relative)
                    duplicate_key = unicodedata.normalize("NFC", canonical).casefold()
                    if duplicate_key in canonical_keys:
                        raise ModelManifestError("Embedding model manifest is invalid")
                    metadata = os.stat(
                        name,
                        dir_fd=directory.descriptor,
                        follow_symlinks=False,
                    )
                    if stat.S_ISDIR(metadata.st_mode):
                        directory_count += 1
                        if directory_count > MAX_MODEL_DIRECTORY_COUNT:
                            raise ModelManifestError(
                                "Embedding model manifest is invalid"
                            )
                        child_descriptor = os.open(
                            name,
                            os.O_RDONLY
                            | getattr(os, "O_CLOEXEC", 0)
                            | getattr(os, "O_DIRECTORY", 0)
                            | getattr(os, "O_NOFOLLOW", 0),
                            dir_fd=directory.descriptor,
                        )
                        child_metadata = os.fstat(child_descriptor)
                        if not _same_stat(metadata, child_metadata):
                            os.close(child_descriptor)
                            raise ModelManifestError(
                                "Embedding model manifest is invalid"
                            )
                        child = _OpenSnapshotEntry(
                            canonical, child_descriptor, child_metadata
                        )
                        open_directories.append(child)
                        pending.append(child)
                        canonical_keys.add(duplicate_key)
                        continue
                    if not stat.S_ISREG(metadata.st_mode):
                        raise ModelManifestError("Embedding model manifest is invalid")
                    if not is_approved_model_path(canonical):
                        raise ModelManifestError("Embedding model manifest is invalid")
                    if len(open_files) >= MAX_MODEL_FILE_COUNT:
                        raise ModelManifestError("Embedding model manifest is invalid")
                    descriptor = os.open(
                        name,
                        os.O_RDONLY
                        | getattr(os, "O_CLOEXEC", 0)
                        | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=directory.descriptor,
                    )
                    opened_metadata = os.fstat(descriptor)
                    if not _same_stat(metadata, opened_metadata):
                        os.close(descriptor)
                        raise ModelManifestError("Embedding model manifest is invalid")
                    allocated_bytes = getattr(opened_metadata, "st_blocks", 0) * 512
                    if opened_metadata.st_size > MAX_MODEL_FILE_BYTES or (
                        opened_metadata.st_size > 0
                        and allocated_bytes < opened_metadata.st_size
                    ):
                        os.close(descriptor)
                        raise ModelManifestError("Embedding model manifest is invalid")
                    total_bytes += opened_metadata.st_size
                    if total_bytes > MAX_MODEL_TOTAL_BYTES:
                        os.close(descriptor)
                        raise ModelManifestError("Embedding model manifest is invalid")
                    open_files.append(
                        _OpenSnapshotEntry(canonical, descriptor, opened_metadata)
                    )
                    canonical_keys.add(duplicate_key)
        if not open_files:
            raise ModelManifestError("Embedding model manifest is invalid")

        files: list[dict[str, Any]] = []
        for opened_file in sorted(open_files, key=lambda value: value.relative_path):
            size, digest = _hash_open_regular_file(
                opened_file.descriptor, expected=opened_file.metadata
            )
            files.append(
                {
                    "path": opened_file.relative_path,
                    "size": size,
                    "sha256": digest,
                }
            )
        for directory in open_directories:
            if not _same_stat(directory.metadata, os.fstat(directory.descriptor)):
                raise ModelManifestError("Embedding model manifest is invalid")
        return files
    finally:
        for opened_file in open_files:
            os.close(opened_file.descriptor)
        for directory in reversed(open_directories):
            os.close(directory.descriptor)


def _hash_open_regular_file(
    descriptor: int, *, expected: os.stat_result | None = None
) -> tuple[int, str]:
    before = os.fstat(descriptor)
    allocated_bytes = getattr(before, "st_blocks", 0) * 512
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_size > MAX_MODEL_FILE_BYTES
        or (before.st_size > 0 and allocated_bytes < before.st_size)
        or (expected is not None and not _same_stat(expected, before))
    ):
        raise ModelManifestError("Embedding model manifest is invalid")
    digest = hashlib.sha256()
    bytes_read = 0
    while bytes_read <= before.st_size:
        chunk = os.read(
            descriptor,
            min(_READ_CHUNK_BYTES, before.st_size - bytes_read + 1),
        )
        if not chunk:
            break
        bytes_read += len(chunk)
        if bytes_read > before.st_size or bytes_read > MAX_MODEL_FILE_BYTES:
            raise ModelManifestError("Embedding model manifest is invalid")
        digest.update(chunk)
    after = os.fstat(descriptor)
    stable_fields = (
        "st_dev",
        "st_ino",
        "st_mode",
        "st_size",
        "st_mtime_ns",
        "st_ctime_ns",
    )
    if bytes_read != before.st_size or any(
        getattr(before, field) != getattr(after, field) for field in stable_fields
    ):
        raise ModelManifestError("Embedding model manifest is invalid")
    return bytes_read, digest.hexdigest()


def _same_stat(left: os.stat_result, right: os.stat_result) -> bool:
    fields = (
        "st_dev",
        "st_ino",
        "st_mode",
        "st_size",
        "st_mtime_ns",
        "st_ctime_ns",
    )
    return all(getattr(left, field) == getattr(right, field) for field in fields)


def _open_directory(path: Path) -> int:
    return os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
