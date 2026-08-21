"""Atomic fixed-revision embedding-model cache preparation."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from omf_retrieval.application.indexing.hashing import canonical_json
from omf_retrieval.infrastructure.embedding.manifest import (
    canonical_model_manifest,
    create_model_manifest,
    create_pinned_model_manifest,
    model_manifest_path,
    pin_model_snapshot,
    resolve_embedding_cache_dir,
    verify_pinned_model_manifest,
)
from omf_retrieval.infrastructure.embedding.manifest_contract import (
    MAX_MODEL_MANIFEST_BYTES,
)
from omf_retrieval.settings import Settings

EMBEDDING_MODEL_NAME = "Qwen/Qwen3-Embedding-0.6B"
EMBEDDING_MODEL_REVISION = "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
MODEL_DOWNLOAD_ALLOW_PATTERNS = (
    "*.json",
    "*.model",
    "*.safetensors",
    "*.txt",
)
_PRIVATE_ROOT = ".omf-retrieval"
_SNAPSHOTS_DIRECTORY = f"{_PRIVATE_ROOT}/snapshots"
_PREPARE_LOCK = "prepare.lock"

ModelDownloader = Callable[..., Path]


class ModelPrepareError(RuntimeError):
    """Report a source-free model preparation failure."""


def prepare_embedding_model(
    settings: Settings, *, downloader: ModelDownloader | None = None
) -> bytes:
    """Download, verify, and atomically publish the fixed model manifest."""
    cache_root: Path | None = None
    stage: Path | None = None
    lock_descriptor: int | None = None
    lock_path: Path | None = None
    try:
        if (
            settings.embedding_model_name != EMBEDDING_MODEL_NAME
            or settings.embedding_model_revision != EMBEDDING_MODEL_REVISION
        ):
            raise ModelPrepareError("Embedding model preparation failed")
        cache_root = resolve_embedding_cache_dir(settings.embedding_cache_dir)
        private_root = cache_root / _PRIVATE_ROOT
        snapshots_root = cache_root.joinpath(*_SNAPSHOTS_DIRECTORY.split("/"))
        _ensure_private_directory(cache_root)
        _ensure_private_directory(private_root)
        _ensure_private_directory(snapshots_root)
        lock_path = private_root / _PREPARE_LOCK
        lock_descriptor = os.open(
            lock_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        stage = Path(tempfile.mkdtemp(prefix=".prepare-", dir=private_root))
        selected_downloader = downloader or download_model_snapshot
        downloaded = selected_downloader(
            model_name=EMBEDDING_MODEL_NAME,
            revision=EMBEDDING_MODEL_REVISION,
            cache_dir=cache_root,
            destination=stage,
            local_files_only=False,
            trust_remote_code=False,
        )
        if not isinstance(downloaded, Path) or downloaded != stage:
            raise ModelPrepareError("Embedding model preparation failed")

        preliminary = create_model_manifest(
            stage,
            cache_dir=cache_root,
            snapshot_coordinate=f"{_SNAPSHOTS_DIRECTORY}/{'0' * 64}",
            model_name=EMBEDDING_MODEL_NAME,
            revision=EMBEDDING_MODEL_REVISION,
        )
        snapshot_id = hashlib.sha256(
            canonical_json(
                {
                    "model": preliminary["model"],
                    "files": preliminary["files"],
                }
            )
        ).hexdigest()
        coordinate = f"{_SNAPSHOTS_DIRECTORY}/{snapshot_id}"
        manifest = create_model_manifest(
            stage,
            cache_dir=cache_root,
            snapshot_coordinate=coordinate,
            model_name=EMBEDDING_MODEL_NAME,
            revision=EMBEDDING_MODEL_REVISION,
        )
        manifest_bytes = canonical_model_manifest(manifest)
        final_snapshot = cache_root.joinpath(*coordinate.split("/"))
        if final_snapshot.exists():
            existing = create_model_manifest(
                final_snapshot,
                cache_dir=cache_root,
                snapshot_coordinate=coordinate,
                model_name=EMBEDDING_MODEL_NAME,
                revision=EMBEDDING_MODEL_REVISION,
            )
            if existing != manifest:
                raise ModelPrepareError("Embedding model preparation failed")
        else:
            staging_parent = stage.parent
            os.rename(stage, final_snapshot)
            stage = None
            _fsync_directory(staging_parent)
            _fsync_directory(final_snapshot.parent)
        with pin_model_snapshot(final_snapshot, cache_dir=cache_root) as pinned:
            published_snapshot = create_pinned_model_manifest(
                pinned,
                snapshot_coordinate=coordinate,
                model_name=EMBEDDING_MODEL_NAME,
                revision=EMBEDDING_MODEL_REVISION,
            )
            if published_snapshot != manifest or not pinned.matches_path():
                raise ModelPrepareError("Embedding model preparation failed")
            _atomic_publish(
                model_manifest_path(cache_root),
                manifest_bytes,
                validity_check=pinned.matches_path,
                final_validation=lambda: verify_pinned_model_manifest(
                    cache_root,
                    pinned,
                    model_name=EMBEDDING_MODEL_NAME,
                    revision=EMBEDDING_MODEL_REVISION,
                ),
            )
        return manifest_bytes
    except (KeyboardInterrupt, SystemExit):
        raise
    except ModelPrepareError:
        raise
    except Exception:
        raise ModelPrepareError("Embedding model preparation failed") from None
    finally:
        _cleanup_prepare(stage, lock_descriptor, lock_path)


def download_model_snapshot(
    *,
    model_name: str,
    revision: str,
    cache_dir: Path,
    destination: Path,
    local_files_only: bool,
    trust_remote_code: bool,
) -> Path:
    """Materialize approved model files through a lazy Hugging Face import."""
    if trust_remote_code is not False:
        raise ModelPrepareError("Embedding model preparation failed")
    from huggingface_hub import snapshot_download

    result = snapshot_download(
        repo_id=model_name,
        revision=revision,
        cache_dir=str(cache_dir),
        local_dir=str(destination),
        local_files_only=local_files_only,
        allow_patterns=list(MODEL_DOWNLOAD_ALLOW_PATTERNS),
    )
    if (
        type(result) is not str
        or Path(result).resolve(strict=False) != destination.resolve()
    ):
        raise ModelPrepareError("Embedding model preparation failed")
    metadata = destination / ".cache"
    if metadata.exists():
        if metadata.is_symlink() or not metadata.is_dir():
            raise ModelPrepareError("Embedding model preparation failed")
        shutil.rmtree(metadata)
    return destination


def _ensure_private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = path.lstat()
    if path.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
        raise ModelPrepareError("Embedding model preparation failed")


@dataclass(slots=True)
class _ManifestPublication:
    path: Path
    backup: Path | None
    previous_identity: tuple[int, int] | None
    previous_content: bytes | None
    published_identity: tuple[int, int]

    def rollback(self) -> None:
        """Restore the prior coordinate without deleting an unknown replacement."""
        current = _path_identity(self.path)
        if current != self.published_identity:
            return
        if self.backup is None:
            if self.previous_content is None:
                self.path.unlink()
            else:
                _restore_owned_manifest(
                    self.path,
                    self.previous_content,
                    owned_identity=self.published_identity,
                )
                return
        else:
            if _path_identity(self.backup) == self.previous_identity:
                os.replace(self.backup, self.path)
            elif self.previous_content is not None:
                _restore_owned_manifest(
                    self.path,
                    self.previous_content,
                    owned_identity=self.published_identity,
                )
                return
            else:
                return
        _fsync_directory(self.path.parent)

    def commit(self) -> None:
        """Remove only the transaction-owned backup after durable publication."""
        if self.backup is None:
            return
        if _path_identity(self.backup) == self.previous_identity:
            self.backup.unlink()
            _fsync_directory(self.path.parent)


def _atomic_publish(
    path: Path,
    content: bytes,
    *,
    validity_check: Callable[[], bool],
    final_validation: Callable[[], bool],
) -> None:
    publication: _ManifestPublication | None = None
    descriptor, temporary_name = tempfile.mkstemp(prefix=".manifest-", dir=path.parent)
    temporary: Path | None = Path(temporary_name)
    backup: Path | None = None
    previous_identity: tuple[int, int] | None = None
    previous_content: bytes | None = None
    try:
        offset = 0
        while offset < len(content):
            written = os.write(descriptor, content[offset:])
            if written <= 0:
                raise ModelPrepareError("Embedding model preparation failed")
            offset += written
        os.fsync(descriptor)
        temporary_metadata = os.fstat(descriptor)
        if not stat.S_ISREG(
            temporary_metadata.st_mode
        ) or temporary_metadata.st_size != len(content):
            raise ModelPrepareError("Embedding model preparation failed")
        temporary_identity = (
            temporary_metadata.st_dev,
            temporary_metadata.st_ino,
        )
        os.close(descriptor)
        descriptor = -1
        _fsync_directory(path.parent)
        previous_identity = _path_identity(path)
        if previous_identity is not None:
            if not stat.S_ISREG(path.lstat().st_mode):
                raise ModelPrepareError("Embedding model preparation failed")
            backup_descriptor, backup_name = tempfile.mkstemp(
                prefix=".manifest-backup-", dir=path.parent
            )
            os.close(backup_descriptor)
            backup = Path(backup_name)
            backup.unlink()
            os.link(path, backup, follow_symlinks=False)
            if _path_identity(backup) != previous_identity:
                raise ModelPrepareError("Embedding model preparation failed")
            previous_content = _read_regular_file(
                backup,
                expected_identity=previous_identity,
                maximum_bytes=MAX_MODEL_MANIFEST_BYTES,
            )
            _fsync_directory(path.parent)
        if not validity_check():
            raise ModelPrepareError("Embedding model preparation failed")
        os.replace(temporary, path)
        temporary = None
        published_metadata = _opened_regular_metadata(path)
        published_identity = (
            published_metadata.st_dev,
            published_metadata.st_ino,
        )
        if (
            published_identity != temporary_identity
            or published_metadata.st_mode != temporary_metadata.st_mode
            or published_metadata.st_size != temporary_metadata.st_size
        ):
            raise ModelPrepareError("Embedding model preparation failed")
        publication = _ManifestPublication(
            path=path,
            backup=backup,
            previous_identity=previous_identity,
            previous_content=previous_content,
            published_identity=published_identity,
        )
        _fsync_directory(path.parent)
        if not validity_check():
            raise ModelPrepareError("Embedding model preparation failed")
        publication.commit()
        backup = None
        if not final_validation():
            raise ModelPrepareError("Embedding model preparation failed")
        publication = None
    finally:
        if publication is not None:
            try:
                publication.rollback()
            except Exception:
                pass
        if descriptor >= 0:
            os.close(descriptor)
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass
        if backup is not None:
            try:
                if _path_identity(backup) == previous_identity:
                    backup.unlink()
            except OSError:
                pass


def _opened_regular_metadata(path: Path) -> os.stat_result:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ModelPrepareError("Embedding model preparation failed")
        return metadata
    finally:
        os.close(descriptor)


def _read_regular_file(
    path: Path, *, expected_identity: tuple[int, int], maximum_bytes: int
) -> bytes:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or (before.st_dev, before.st_ino) != expected_identity
            or not 0 < before.st_size <= maximum_bytes
        ):
            raise ModelPrepareError("Embedding model preparation failed")
        content = b""
        while len(content) <= before.st_size:
            chunk = os.read(descriptor, before.st_size - len(content) + 1)
            if not chunk:
                break
            content += chunk
        after = os.fstat(descriptor)
        if len(content) != before.st_size or any(
            getattr(before, field) != getattr(after, field)
            for field in (
                "st_dev",
                "st_ino",
                "st_mode",
                "st_size",
                "st_mtime_ns",
                "st_ctime_ns",
            )
        ):
            raise ModelPrepareError("Embedding model preparation failed")
        return content
    finally:
        os.close(descriptor)


def _restore_owned_manifest(
    path: Path, content: bytes, *, owned_identity: tuple[int, int]
) -> None:
    if _path_identity(path) != owned_identity:
        return
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".manifest-restore-", dir=path.parent
    )
    temporary: Path | None = Path(temporary_name)
    try:
        offset = 0
        while offset < len(content):
            written = os.write(descriptor, content[offset:])
            if written <= 0:
                raise ModelPrepareError("Embedding model preparation failed")
            offset += written
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != len(content):
            raise ModelPrepareError("Embedding model preparation failed")
        restored_identity = (metadata.st_dev, metadata.st_ino)
        os.close(descriptor)
        descriptor = -1
        _fsync_directory(path.parent)
        if _path_identity(path) != owned_identity:
            return
        os.replace(temporary, path)
        temporary = None
        if _path_identity(path) != restored_identity:
            raise ModelPrepareError("Embedding model preparation failed")
        _fsync_directory(path.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _path_identity(path: Path) -> tuple[int, int] | None:
    try:
        metadata = path.lstat()
        return metadata.st_dev, metadata.st_ino
    except FileNotFoundError:
        return None


def _remove_private_stage(stage: Path) -> None:
    try:
        shutil.rmtree(stage)
    except OSError:
        pass


def _cleanup_prepare(
    stage: Path | None, lock_descriptor: int | None, lock_path: Path | None
) -> None:
    if stage is not None:
        _remove_private_stage(stage)
    if lock_descriptor is None:
        return
    lock_metadata: os.stat_result | None = None
    try:
        lock_metadata = os.fstat(lock_descriptor)
    except OSError:
        pass
    try:
        os.close(lock_descriptor)
    except OSError:
        pass
    if lock_path is None or lock_metadata is None:
        return
    try:
        path_metadata = lock_path.lstat()
        if (path_metadata.st_dev, path_metadata.st_ino) == (
            lock_metadata.st_dev,
            lock_metadata.st_ino,
        ):
            lock_path.unlink()
    except OSError:
        pass
