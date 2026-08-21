"""Atomic fixed-revision embedding-model cache preparation."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tempfile
from collections.abc import Callable
from pathlib import Path

from omf_retrieval.application.indexing.hashing import canonical_json
from omf_retrieval.infrastructure.embedding.manifest import (
    canonical_model_manifest,
    create_model_manifest,
    model_manifest_path,
    resolve_embedding_cache_dir,
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
            os.rename(stage, final_snapshot)
            stage = None
        published_snapshot = create_model_manifest(
            final_snapshot,
            cache_dir=cache_root,
            snapshot_coordinate=coordinate,
            model_name=EMBEDDING_MODEL_NAME,
            revision=EMBEDDING_MODEL_REVISION,
        )
        if published_snapshot != manifest:
            raise ModelPrepareError("Embedding model preparation failed")
        _atomic_publish(model_manifest_path(cache_root), manifest_bytes)
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


def _atomic_publish(path: Path, content: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=".manifest-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        offset = 0
        while offset < len(content):
            written = os.write(descriptor, content[offset:])
            if written <= 0:
                raise ModelPrepareError("Embedding model preparation failed")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except OSError:
            pass


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
