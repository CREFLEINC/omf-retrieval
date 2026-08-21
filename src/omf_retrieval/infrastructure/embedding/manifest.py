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
from omf_retrieval.infrastructure.embedding.manifest_contract import (
    MAX_MODEL_FILE_BYTES,
    MAX_MODEL_FILE_COUNT,
    MAX_MODEL_MANIFEST_BYTES,
    MAX_MODEL_TOTAL_BYTES,
    MODEL_MANIFEST_RELATIVE_PATH,
    MODEL_MANIFEST_SCHEMA,
    MODEL_MANIFEST_VERSION,
    ModelManifestError,
    is_approved_model_path,
    is_sha256,
    require_exact_identity,
    validated_relative_path,
    validated_snapshot_coordinate,
)
from omf_retrieval.infrastructure.embedding.snapshot_integrity import (
    PinnedModelSnapshot,
    pin_model_snapshot,
    snapshot_files,
)

_READ_CHUNK_BYTES = 1024 * 1024


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
        with pin_model_snapshot(snapshot_dir, cache_dir=cache_dir) as pinned:
            return create_pinned_model_manifest(
                pinned,
                snapshot_coordinate=snapshot_coordinate,
                model_name=model_name,
                revision=revision,
            )
    except (KeyboardInterrupt, SystemExit):
        raise
    except ModelManifestError:
        raise
    except Exception:
        raise ModelManifestError("Embedding model manifest is invalid") from None


def create_pinned_model_manifest(
    snapshot: PinnedModelSnapshot,
    *,
    snapshot_coordinate: str,
    model_name: str,
    revision: str,
) -> dict[str, Any]:
    """Hash an already-pinned snapshot and reject identity changes around it."""
    try:
        require_exact_identity(model_name, revision)
        coordinate = validated_snapshot_coordinate(snapshot_coordinate)
        if not snapshot.matches_path():
            raise ModelManifestError("Embedding model manifest is invalid")
        files = snapshot_files(snapshot)
        if not snapshot.matches_path():
            raise ModelManifestError("Embedding model manifest is invalid")
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


def verify_pinned_model_manifest(
    cache_dir: Path,
    snapshot: PinnedModelSnapshot,
    *,
    model_name: str,
    revision: str,
) -> bool:
    """Fully verify the published manifest against one already-pinned snapshot."""
    try:
        root = resolve_embedding_cache_dir(cache_dir)
        if not snapshot.matches_path():
            return False
        raw = _read_manifest_file(model_manifest_path(root))
        parsed = json.loads(raw)
        validated = _validated_manifest_value(parsed)
        if canonical_json(validated) != raw or validated["model"] != {
            "name": model_name,
            "revision": revision,
        }:
            return False
        coordinate = validated["cache"]["snapshot"]
        expected_snapshot = root.joinpath(*PurePosixPath(coordinate).parts).absolute()
        if expected_snapshot != snapshot.path:
            return False
        rebuilt = create_pinned_model_manifest(
            snapshot,
            snapshot_coordinate=coordinate,
            model_name=model_name,
            revision=revision,
        )
        return rebuilt == validated and snapshot.matches_path()
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        return False


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
    require_exact_identity(model["name"], model["revision"])
    if (
        type(cache) is not dict
        or cache.get("root") != "."
        or set(cache) != {"root", "snapshot"}
    ):
        raise ModelManifestError("Embedding model manifest is invalid")
    snapshot = validated_snapshot_coordinate(cache["snapshot"])
    if type(files) is not list or not 1 <= len(files) <= MAX_MODEL_FILE_COUNT:
        raise ModelManifestError("Embedding model manifest is invalid")
    validated_files: list[dict[str, Any]] = []
    seen: set[str] = set()
    total = 0
    for item in files:
        if type(item) is not dict or set(item) != {"path", "size", "sha256"}:
            raise ModelManifestError("Embedding model manifest is invalid")
        path = validated_relative_path(item["path"])
        key = unicodedata.normalize("NFC", path).casefold()
        size = item["size"]
        digest = item["sha256"]
        if (
            key in seen
            or not is_approved_model_path(path)
            or type(size) is not int
            or not 0 <= size <= MAX_MODEL_FILE_BYTES
        ):
            raise ModelManifestError("Embedding model manifest is invalid")
        if not is_sha256(digest):
            raise ModelManifestError("Embedding model manifest is invalid")
        seen.add(key)
        total += size
        if total > MAX_MODEL_TOTAL_BYTES:
            raise ModelManifestError("Embedding model manifest is invalid")
        validated_files.append({"path": path, "size": size, "sha256": digest})
    if validated_files != sorted(validated_files, key=lambda item: item["path"]):
        raise ModelManifestError("Embedding model manifest is invalid")
    if not is_sha256(manifest_hash):
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


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
