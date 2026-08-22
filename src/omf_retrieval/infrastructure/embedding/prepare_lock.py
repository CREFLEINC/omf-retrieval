"""Stable POSIX advisory lock for embedding-model preparation."""

from __future__ import annotations

import fcntl
import os
import stat
from pathlib import Path


class PrepareLockError(RuntimeError):
    """Report a source-free prepare-lock validation failure."""


def acquire_prepare_lock(path: Path) -> int:
    """Acquire the stable lock without following or replacing its path."""
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
            0o600,
        )
        metadata = os.fstat(descriptor)
        identity = (metadata.st_dev, metadata.st_ino)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise PrepareLockError("Embedding model preparation lock failed")
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        locked_metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(locked_metadata.st_mode)
            or locked_metadata.st_nlink != 1
            or stat.S_IMODE(locked_metadata.st_mode) != 0o600
            or (locked_metadata.st_dev, locked_metadata.st_ino) != identity
            or _path_identity(path) != identity
        ):
            raise PrepareLockError("Embedding model preparation lock failed")
        return descriptor
    except BaseException:
        if descriptor >= 0:
            release_prepare_lock(descriptor)
        raise


def release_prepare_lock(descriptor: int) -> None:
    """Close the descriptor so the kernel releases its advisory lock."""
    try:
        os.close(descriptor)
    except OSError:
        pass


def _path_identity(path: Path) -> tuple[int, int] | None:
    try:
        metadata = path.lstat()
        return metadata.st_dev, metadata.st_ino
    except FileNotFoundError:
        return None
