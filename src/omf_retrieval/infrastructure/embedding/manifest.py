"""Deterministic integrity manifests for prepared embedding-model snapshots."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import unicodedata
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from omf_retrieval.application.indexing.hashing import canonical_json

MODEL_MANIFEST_SCHEMA = "omf-retrieval.embedding-model-manifest"
MODEL_MANIFEST_VERSION = 1
MAX_MODEL_FILE_COUNT = 256
MAX_MODEL_DIRECTORY_COUNT = MAX_MODEL_FILE_COUNT
MAX_MODEL_FILE_BYTES = 4 * 1024**3
MAX_MODEL_TOTAL_BYTES = 8 * 1024**3
MAX_MODEL_MANIFEST_BYTES = MAX_MODEL_FILE_COUNT * (4096 + 512)
APPROVED_MODEL_FILE_SUFFIXES = (".json", ".model", ".safetensors", ".txt")
MODEL_MANIFEST_RELATIVE_PATH = PurePosixPath(
    ".omf-retrieval/embedding-model-manifest.json"
)
_READ_CHUNK_BYTES = 1024 * 1024
_HEX_DIGITS = frozenset("0123456789abcdef")


class ModelManifestError(ValueError):
    """Report a source-free model-manifest validation failure."""


def resolve_embedding_cache_dir(cache_dir: Path | None) -> Path:
    """Resolve an explicit cache or Hugging Face's environment-based default."""
    if cache_dir is not None:
        return cache_dir.expanduser().resolve(strict=False)
    hub_cache = os.environ.get("HF_HUB_CACHE")
    if hub_cache:
        return Path(hub_cache).expanduser().resolve(strict=False)
    hf_home = os.environ.get("HF_HOME")
    if hf_home:
        return (Path(hf_home).expanduser() / "hub").resolve(strict=False)
    xdg_cache = os.environ.get("XDG_CACHE_HOME")
    cache_home = Path(xdg_cache).expanduser() if xdg_cache else Path.home() / ".cache"
    return (cache_home / "huggingface" / "hub").resolve(strict=False)


def model_manifest_path(cache_dir: Path | None) -> Path:
    """Return the fixed manifest coordinate for one configured cache."""
    return resolve_embedding_cache_dir(cache_dir).joinpath(
        *MODEL_MANIFEST_RELATIVE_PATH.parts
    )


def create_model_manifest(
    snapshot_dir: Path,
    *,
    cache_dir: Path,
    snapshot_coordinate: str,
    model_name: str,
    revision: str,
) -> dict[str, Any]:
    """Hash a private regular-file snapshot into a canonical manifest value."""
    try:
        _require_exact_identity(model_name, revision)
        cache_root = cache_dir.expanduser().resolve(strict=False)
        try:
            snapshot_dir.resolve(strict=True).relative_to(cache_root)
        except (OSError, ValueError):
            raise ModelManifestError("Embedding model manifest is invalid") from None
        coordinate = _validated_snapshot_coordinate(snapshot_coordinate)
        files = _snapshot_files(snapshot_dir)
        payload: dict[str, Any] = {
            "schema": MODEL_MANIFEST_SCHEMA,
            "version": MODEL_MANIFEST_VERSION,
            "model": {"name": model_name, "revision": revision},
            "cache": {"root": ".", "snapshot": coordinate},
            "files": files,
        }
        return {**payload, "manifest_sha256": _sha256(canonical_json(payload))}
    except (KeyboardInterrupt, SystemExit):
        raise
    except ModelManifestError:
        raise
    except Exception:
        raise ModelManifestError("Embedding model manifest is invalid") from None


def canonical_model_manifest(manifest: Mapping[str, Any]) -> bytes:
    """Return canonical UTF-8 bytes after validating the manifest shape."""
    try:
        validated = _validated_manifest_value(manifest)
        return canonical_json(validated)
    except (KeyboardInterrupt, SystemExit):
        raise
    except ModelManifestError:
        raise
    except Exception:
        raise ModelManifestError("Embedding model manifest is invalid") from None


def verify_model_manifest(
    cache_dir: Path | None, *, model_name: str, revision: str
) -> bool:
    """Verify canonical metadata and every snapshot byte without model loading."""
    return (
        verified_model_snapshot(cache_dir, model_name=model_name, revision=revision)
        is not None
    )


def verified_model_snapshot(
    cache_dir: Path | None, *, model_name: str, revision: str
) -> Path | None:
    """Return the verified snapshot coordinate, or ``None`` for ordinary faults."""
    try:
        root = resolve_embedding_cache_dir(cache_dir)
        raw = _read_manifest_file(model_manifest_path(root))
        parsed = json.loads(raw)
        validated = _validated_manifest_value(parsed)
        if canonical_json(validated) != raw:
            return None
        if validated["model"] != {"name": model_name, "revision": revision}:
            return None
        coordinate = validated["cache"]["snapshot"]
        snapshot = root.joinpath(*PurePosixPath(coordinate).parts)
        rebuilt = create_model_manifest(
            snapshot,
            cache_dir=root,
            snapshot_coordinate=coordinate,
            model_name=model_name,
            revision=revision,
        )
        return snapshot if rebuilt == validated else None
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        return None


def _snapshot_files(snapshot_dir: Path) -> list[dict[str, Any]]:
    root_stat = snapshot_dir.lstat()
    if not stat.S_ISDIR(root_stat.st_mode) or snapshot_dir.is_symlink():
        raise ModelManifestError("Embedding model manifest is invalid")
    files: list[dict[str, Any]] = []
    canonical_keys: set[str] = set()
    total_bytes = 0
    directory_count = 0
    for directory, directory_names, file_names, directory_fd in os.fwalk(
        snapshot_dir, topdown=True, follow_symlinks=False
    ):
        relative_directory = Path(directory).relative_to(snapshot_dir)
        directory_names.sort()
        file_names.sort()
        for name in directory_names:
            directory_count += 1
            if directory_count > MAX_MODEL_DIRECTORY_COUNT:
                raise ModelManifestError("Embedding model manifest is invalid")
            metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            relative = (relative_directory / name).as_posix()
            canonical = _validated_relative_path(relative)
            duplicate_key = unicodedata.normalize("NFC", canonical).casefold()
            if duplicate_key in canonical_keys:
                raise ModelManifestError("Embedding model manifest is invalid")
            canonical_keys.add(duplicate_key)
            if not stat.S_ISDIR(metadata.st_mode):
                raise ModelManifestError("Embedding model manifest is invalid")
        for name in file_names:
            relative = (relative_directory / name).as_posix()
            canonical = _validated_relative_path(relative)
            duplicate_key = unicodedata.normalize("NFC", canonical).casefold()
            if duplicate_key in canonical_keys or not _is_approved_model_path(
                canonical
            ):
                raise ModelManifestError("Embedding model manifest is invalid")
            canonical_keys.add(duplicate_key)
            size, digest = _hash_regular_file_at(directory_fd, name)
            total_bytes += size
            if total_bytes > MAX_MODEL_TOTAL_BYTES:
                raise ModelManifestError("Embedding model manifest is invalid")
            files.append({"path": canonical, "size": size, "sha256": digest})
            if len(files) > MAX_MODEL_FILE_COUNT:
                raise ModelManifestError("Embedding model manifest is invalid")
    if not files:
        raise ModelManifestError("Embedding model manifest is invalid")
    return sorted(files, key=lambda item: item["path"])


def _hash_regular_file_at(directory_fd: int, name: str) -> tuple[int, str]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(name, flags, dir_fd=directory_fd)
    try:
        return _hash_open_regular_file(descriptor)
    finally:
        os.close(descriptor)


def _hash_open_regular_file(descriptor: int) -> tuple[int, str]:
    before = os.fstat(descriptor)
    allocated_bytes = getattr(before, "st_blocks", 0) * 512
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_size > MAX_MODEL_FILE_BYTES
        or (before.st_size > 0 and allocated_bytes < before.st_size)
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


def _read_manifest_file(path: Path) -> bytes:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or not 0 < before.st_size <= MAX_MODEL_MANIFEST_BYTES
        ):
            raise ModelManifestError("Embedding model manifest is invalid")
        size = before.st_size
        raw = b""
        while len(raw) <= size:
            chunk = os.read(descriptor, min(_READ_CHUNK_BYTES, size - len(raw) + 1))
            if not chunk:
                break
            raw += chunk
        after = os.fstat(descriptor)
        if len(raw) != size or any(
            getattr(before, field) != getattr(after, field)
            for field in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        ):
            raise ModelManifestError("Embedding model manifest is invalid")
        return raw
    finally:
        os.close(descriptor)


def _validated_manifest_value(value: object) -> dict[str, Any]:
    if type(value) is not dict or set(value) != {
        "schema",
        "version",
        "model",
        "cache",
        "files",
        "manifest_sha256",
    }:
        raise ModelManifestError("Embedding model manifest is invalid")
    schema = value["schema"]
    version = value["version"]
    model = value["model"]
    cache = value["cache"]
    files = value["files"]
    manifest_hash = value["manifest_sha256"]
    if schema != MODEL_MANIFEST_SCHEMA or type(schema) is not str:
        raise ModelManifestError("Embedding model manifest is invalid")
    if type(version) is not int or version != MODEL_MANIFEST_VERSION:
        raise ModelManifestError("Embedding model manifest is invalid")
    if type(model) is not dict or set(model) != {"name", "revision"}:
        raise ModelManifestError("Embedding model manifest is invalid")
    _require_exact_identity(model["name"], model["revision"])
    if (
        type(cache) is not dict
        or cache.get("root") != "."
        or set(cache) != {"root", "snapshot"}
    ):
        raise ModelManifestError("Embedding model manifest is invalid")
    snapshot = _validated_snapshot_coordinate(cache["snapshot"])
    if type(files) is not list or not 1 <= len(files) <= MAX_MODEL_FILE_COUNT:
        raise ModelManifestError("Embedding model manifest is invalid")
    validated_files: list[dict[str, Any]] = []
    seen: set[str] = set()
    total = 0
    for item in files:
        if type(item) is not dict or set(item) != {"path", "size", "sha256"}:
            raise ModelManifestError("Embedding model manifest is invalid")
        path = _validated_relative_path(item["path"])
        key = unicodedata.normalize("NFC", path).casefold()
        size = item["size"]
        digest = item["sha256"]
        if (
            key in seen
            or not _is_approved_model_path(path)
            or type(size) is not int
            or not 0 <= size <= MAX_MODEL_FILE_BYTES
        ):
            raise ModelManifestError("Embedding model manifest is invalid")
        if not _is_sha256(digest):
            raise ModelManifestError("Embedding model manifest is invalid")
        seen.add(key)
        total += size
        if total > MAX_MODEL_TOTAL_BYTES:
            raise ModelManifestError("Embedding model manifest is invalid")
        validated_files.append({"path": path, "size": size, "sha256": digest})
    if validated_files != sorted(validated_files, key=lambda item: item["path"]):
        raise ModelManifestError("Embedding model manifest is invalid")
    if not _is_sha256(manifest_hash):
        raise ModelManifestError("Embedding model manifest is invalid")
    payload = {
        "schema": schema,
        "version": version,
        "model": {"name": model["name"], "revision": model["revision"]},
        "cache": {"root": ".", "snapshot": snapshot},
        "files": validated_files,
    }
    if _sha256(canonical_json(payload)) != manifest_hash:
        raise ModelManifestError("Embedding model manifest is invalid")
    return {**payload, "manifest_sha256": manifest_hash}


def _validated_relative_path(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or unicodedata.normalize("NFC", value) != value
    ):
        raise ModelManifestError("Embedding model manifest is invalid")
    if "\\" in value or any(ord(character) < 32 for character in value):
        raise ModelManifestError("Embedding model manifest is invalid")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ModelManifestError("Embedding model manifest is invalid")
    return value


def _require_exact_identity(model_name: object, revision: object) -> None:
    if (
        type(model_name) is not str
        or not model_name.strip()
        or type(revision) is not str
        or not revision.strip()
    ):
        raise ModelManifestError("Embedding model manifest is invalid")


def _validated_snapshot_coordinate(value: object) -> str:
    coordinate = _validated_relative_path(value)
    parts = PurePosixPath(coordinate).parts
    if (
        len(parts) != 3
        or parts[:2] != (".omf-retrieval", "snapshots")
        or not _is_sha256(parts[2])
    ):
        raise ModelManifestError("Embedding model manifest is invalid")
    return coordinate


def _is_sha256(value: object) -> bool:
    return type(value) is str and len(value) == 64 and set(value) <= _HEX_DIGITS


def _is_approved_model_path(path: str) -> bool:
    return path.endswith(APPROVED_MODEL_FILE_SUFFIXES)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
