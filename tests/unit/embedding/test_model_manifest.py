"""Contract tests for prepared embedding-model manifests."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

import omf_retrieval.infrastructure.embedding.manifest as manifest_module
from omf_retrieval.application.indexing.hashing import canonical_json
from omf_retrieval.infrastructure.embedding.manifest import (
    MAX_MODEL_FILE_BYTES,
    MAX_MODEL_FILE_COUNT,
    MODEL_MANIFEST_SCHEMA,
    MODEL_MANIFEST_VERSION,
    ModelManifestError,
    canonical_model_manifest,
    create_model_manifest,
    model_manifest_path,
    resolve_embedding_cache_dir,
    verify_model_manifest,
)

MODEL = "Qwen/Qwen3-Embedding-0.6B"
REVISION = "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
COORDINATE = ".omf-retrieval/snapshots/" + "0" * 64


def _manifest(files: list[dict[str, object]]) -> dict[str, object]:
    payload = {
        "schema": MODEL_MANIFEST_SCHEMA,
        "version": MODEL_MANIFEST_VERSION,
        "model": {"name": MODEL, "revision": REVISION},
        "cache": {"root": ".", "snapshot": COORDINATE},
        "files": files,
    }
    return {
        **payload,
        "manifest_sha256": hashlib.sha256(canonical_json(payload)).hexdigest(),
    }


def _entry(path: str, *, size: int = 0, byte: bytes = b"") -> dict[str, object]:
    return {
        "path": path,
        "size": size,
        "sha256": hashlib.sha256(byte).hexdigest(),
    }


def _write_published_manifest(cache: Path, manifest: object) -> None:
    path = model_manifest_path(cache)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json(manifest))


def test_manifest_is_deterministic_sorted_and_self_hashed(tmp_path: Path) -> None:
    snapshot = tmp_path / "stage"
    (snapshot / "1_Pooling").mkdir(parents=True)
    (snapshot / "tokenizer.json").write_bytes("한글".encode())
    (snapshot / "1_Pooling" / "config.json").write_bytes(b"{}")

    first = create_model_manifest(
        snapshot,
        cache_dir=tmp_path,
        snapshot_coordinate=COORDINATE,
        model_name=MODEL,
        revision=REVISION,
    )
    second = create_model_manifest(
        snapshot,
        cache_dir=tmp_path,
        snapshot_coordinate=COORDINATE,
        model_name=MODEL,
        revision=REVISION,
    )

    assert first == second
    assert [entry["path"] for entry in first["files"]] == [
        "1_Pooling/config.json",
        "tokenizer.json",
    ]
    payload = {key: value for key, value in first.items() if key != "manifest_sha256"}
    assert (
        first["manifest_sha256"] == hashlib.sha256(canonical_json(payload)).hexdigest()
    )
    assert canonical_model_manifest(first) == canonical_json(first)


@pytest.mark.parametrize(
    "files",
    [
        [_entry("large.safetensors", size=MAX_MODEL_FILE_BYTES)],
        [
            _entry("a.safetensors", size=MAX_MODEL_FILE_BYTES),
            _entry("b.safetensors", size=MAX_MODEL_FILE_BYTES),
        ],
        [_entry(f"{index:03}.json") for index in range(MAX_MODEL_FILE_COUNT)],
    ],
    ids=["file-exact-max", "total-exact-max", "count-exact-max"],
)
def test_manifest_bounds_include_each_exact_maximum(
    files: list[dict[str, object]],
) -> None:
    assert canonical_model_manifest(_manifest(files))


@pytest.mark.parametrize(
    "files",
    [
        [_entry("large.safetensors", size=MAX_MODEL_FILE_BYTES + 1)],
        [
            _entry("a.safetensors", size=MAX_MODEL_FILE_BYTES),
            _entry("b.safetensors", size=MAX_MODEL_FILE_BYTES),
            _entry("c.safetensors", size=1),
        ],
        [_entry(f"{index:03}.json") for index in range(MAX_MODEL_FILE_COUNT + 1)],
    ],
    ids=["file-max-plus-one", "total-max-plus-one", "count-max-plus-one"],
)
def test_manifest_bounds_reject_maximum_plus_one(
    files: list[dict[str, object]],
) -> None:
    with pytest.raises(ModelManifestError, match="manifest is invalid"):
        canonical_model_manifest(_manifest(files))


@pytest.mark.parametrize(
    "path",
    ["/absolute", "../escape", "a/../escape", "a\\b", "e\u0301.json", "a\u0000b"],
)
def test_manifest_rejects_unsafe_or_ambiguous_paths(path: str) -> None:
    with pytest.raises(ModelManifestError, match="manifest is invalid"):
        canonical_model_manifest(_manifest([_entry(path)]))


def test_manifest_rejects_casefold_duplicate_paths() -> None:
    with pytest.raises(ModelManifestError, match="manifest is invalid"):
        canonical_model_manifest(_manifest([_entry("A.json"), _entry("a.json")]))


def test_snapshot_rejects_symlink_and_fifo_without_source_leak(tmp_path: Path) -> None:
    snapshot = tmp_path / "secret-snapshot"
    snapshot.mkdir()
    target = tmp_path / "secret-target"
    target.write_text("secret-value")
    (snapshot / "model.bin").symlink_to(target)

    with pytest.raises(ModelManifestError) as captured:
        create_model_manifest(
            snapshot,
            cache_dir=tmp_path,
            snapshot_coordinate=COORDINATE,
            model_name=MODEL,
            revision=REVISION,
        )
    assert "secret" not in str(captured.value)

    (snapshot / "model.bin").unlink()
    fifo = snapshot / "pipe"
    os.mkfifo(fifo)
    with pytest.raises(ModelManifestError, match="manifest is invalid"):
        create_model_manifest(
            snapshot,
            cache_dir=tmp_path,
            snapshot_coordinate=COORDINATE,
            model_name=MODEL,
            revision=REVISION,
        )


def test_snapshot_rejects_unapproved_file_and_sparse_oversize(tmp_path: Path) -> None:
    snapshot = tmp_path / "stage"
    snapshot.mkdir()
    (snapshot / "remote_code.py").write_bytes(b"danger")
    with pytest.raises(ModelManifestError, match="manifest is invalid"):
        create_model_manifest(
            snapshot,
            cache_dir=tmp_path,
            snapshot_coordinate=COORDINATE,
            model_name=MODEL,
            revision=REVISION,
        )

    (snapshot / "remote_code.py").unlink()
    sparse = snapshot / "model.safetensors"
    with sparse.open("wb") as stream:
        stream.truncate(MAX_MODEL_FILE_BYTES + 1)
    with pytest.raises(ModelManifestError, match="manifest is invalid"):
        create_model_manifest(
            snapshot,
            cache_dir=tmp_path,
            snapshot_coordinate=COORDINATE,
            model_name=MODEL,
            revision=REVISION,
        )


def test_snapshot_detects_file_mutation_during_bounded_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    snapshot = tmp_path / "stage"
    snapshot.mkdir()
    model = snapshot / "model.safetensors"
    model.write_bytes(b"original")
    original_read = manifest_module.os.read
    mutated = False

    def mutating_read(descriptor: int, count: int) -> bytes:
        nonlocal mutated
        chunk = original_read(descriptor, count)
        if chunk and not mutated:
            mutated = True
            model.write_bytes(b"modified")
        return chunk

    monkeypatch.setattr(manifest_module.os, "read", mutating_read)
    with pytest.raises(ModelManifestError, match="manifest is invalid"):
        create_model_manifest(
            snapshot,
            cache_dir=tmp_path,
            snapshot_coordinate=COORDINATE,
            model_name=MODEL,
            revision=REVISION,
        )


def test_verify_rejects_tampering_noncanonical_json_and_extra_file(
    tmp_path: Path,
) -> None:
    snapshot = tmp_path.joinpath(*COORDINATE.split("/"))
    snapshot.mkdir(parents=True)
    (snapshot / "model.safetensors").write_bytes(b"model")
    manifest = create_model_manifest(
        snapshot,
        cache_dir=tmp_path,
        snapshot_coordinate=COORDINATE,
        model_name=MODEL,
        revision=REVISION,
    )
    _write_published_manifest(tmp_path, manifest)

    assert verify_model_manifest(tmp_path, model_name=MODEL, revision=REVISION)

    (snapshot / "model.safetensors").write_bytes(b"changed")
    assert not verify_model_manifest(tmp_path, model_name=MODEL, revision=REVISION)
    (snapshot / "model.safetensors").write_bytes(b"model")

    path = model_manifest_path(tmp_path)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    assert not verify_model_manifest(tmp_path, model_name=MODEL, revision=REVISION)
    _write_published_manifest(tmp_path, manifest)

    (snapshot / "extra.safetensors").write_bytes(b"extra")
    assert not verify_model_manifest(tmp_path, model_name=MODEL, revision=REVISION)


def test_verify_returns_false_for_ordinary_io_and_json_failures(tmp_path: Path) -> None:
    assert not verify_model_manifest(tmp_path, model_name=MODEL, revision=REVISION)
    path = model_manifest_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"not-json")
    assert not verify_model_manifest(tmp_path, model_name=MODEL, revision=REVISION)


@pytest.mark.parametrize("failure", [KeyboardInterrupt(), SystemExit(9)])
def test_verify_propagates_process_control_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: BaseException
) -> None:
    def fail(path: Path) -> bytes:
        raise failure

    monkeypatch.setattr(manifest_module, "_read_manifest_file", fail)
    with pytest.raises(type(failure)) as captured:
        verify_model_manifest(tmp_path, model_name=MODEL, revision=REVISION)
    assert captured.value is failure


def test_create_rejects_snapshot_outside_cache(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "model.safetensors").write_bytes(b"model")
    with pytest.raises(ModelManifestError, match="manifest is invalid"):
        create_model_manifest(
            outside,
            cache_dir=cache,
            snapshot_coordinate=COORDINATE,
            model_name=MODEL,
            revision=REVISION,
        )


def test_default_cache_matches_hugging_face_environment_precedence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("HF_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hub"))
    assert resolve_embedding_cache_dir(None) == (tmp_path / "hub").resolve()
    monkeypatch.delenv("HF_HUB_CACHE")
    assert resolve_embedding_cache_dir(None) == (tmp_path / "home" / "hub").resolve()
    monkeypatch.delenv("HF_HOME")
    assert (
        resolve_embedding_cache_dir(None)
        == (tmp_path / "xdg" / "huggingface" / "hub").resolve()
    )


def test_manifest_errors_do_not_call_hostile_string_conversion() -> None:
    class HostileString:
        def __str__(self) -> str:
            raise AssertionError("must not stringify")

        def __repr__(self) -> str:
            raise AssertionError("must not repr")

    with pytest.raises(ModelManifestError, match="manifest is invalid"):
        canonical_model_manifest(
            _manifest([_entry("ok.json")]) | {"schema": HostileString()}
        )
