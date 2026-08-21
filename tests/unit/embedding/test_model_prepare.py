"""Contract tests for atomic embedding-model preparation."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import omf_retrieval.infrastructure.embedding.prepare as prepare_module
import omf_retrieval.infrastructure.embedding.snapshot_integrity as snapshot_module
from omf_retrieval.infrastructure.embedding.manifest import (
    model_manifest_path,
    verify_model_manifest,
)
from omf_retrieval.infrastructure.embedding.prepare import (
    EMBEDDING_MODEL_NAME,
    EMBEDDING_MODEL_REVISION,
    ModelPrepareError,
    download_model_snapshot,
    prepare_embedding_model,
)
from omf_retrieval.infrastructure.embedding.sentence_transformer import (
    SentenceTransformerEmbeddingProvider,
)
from omf_retrieval.settings import Settings


class _PrepareAbort(BaseException):
    pass


def _settings(cache: Path) -> Settings:
    return Settings(environment="test", embedding_cache_dir=cache)


def _downloader(calls: list[dict[str, object]], content: bytes = b"model"):
    def download(**kwargs: object) -> Path:
        calls.append(kwargs)
        destination = kwargs["destination"]
        assert isinstance(destination, Path)
        (destination / "model.safetensors").write_bytes(content)
        (destination / "config.json").write_bytes(b"{}")
        return destination

    return download


def test_prepare_uses_fixed_safe_download_contract_and_publishes_canonical_manifest(
    tmp_path: Path,
) -> None:
    calls: list[dict[str, object]] = []
    settings = _settings(tmp_path)

    output = prepare_embedding_model(settings, downloader=_downloader(calls))

    assert len(calls) == 1
    assert calls[0] == {
        "model_name": EMBEDDING_MODEL_NAME,
        "revision": EMBEDDING_MODEL_REVISION,
        "cache_dir": tmp_path.resolve(),
        "destination": calls[0]["destination"],
        "local_files_only": False,
        "trust_remote_code": False,
    }
    assert model_manifest_path(tmp_path).read_bytes() == output
    assert output.startswith(b'{"cache":')
    assert output.endswith(b"}")
    assert verify_model_manifest(
        tmp_path,
        model_name=EMBEDDING_MODEL_NAME,
        revision=EMBEDDING_MODEL_REVISION,
    )
    assert not list((tmp_path / ".omf-retrieval").glob(".prepare-*"))
    assert not (tmp_path / ".omf-retrieval" / "prepare.lock").exists()


def test_same_snapshot_bytes_produce_same_json_and_hash(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first = prepare_embedding_model(settings, downloader=_downloader([]))
    second = prepare_embedding_model(settings, downloader=_downloader([]))
    assert first == second


def test_directory_swap_during_final_rehash_never_publishes_invalid_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings(tmp_path)
    snapshots = tmp_path / ".omf-retrieval" / "snapshots"
    original_hash = snapshot_module._hash_open_regular_file
    swapped = False

    def swap_after_hash(
        descriptor: int, *, expected: os.stat_result | None = None
    ) -> tuple[int, str]:
        nonlocal swapped
        result = original_hash(descriptor, expected=expected)
        children = list(snapshots.iterdir()) if snapshots.exists() else []
        if children and not swapped:
            swapped = True
            final = children[0]
            final.rename(snapshots / "renamed-original")
            final.mkdir()
            (final / "config.json").write_bytes(b'{"replacement":true}')
            (final / "model.safetensors").write_bytes(b"replacement")
        return result

    monkeypatch.setattr(snapshot_module, "_hash_open_regular_file", swap_after_hash)
    with pytest.raises(ModelPrepareError, match="preparation failed"):
        prepare_embedding_model(settings, downloader=_downloader([]))
    assert swapped is True
    assert not model_manifest_path(tmp_path).exists()


def test_prepared_manifest_drives_readiness_and_tampering_makes_it_false(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    prepare_embedding_model(settings, downloader=_downloader([]))
    provider = SentenceTransformerEmbeddingProvider(settings)
    assert provider.is_ready() is True

    snapshot = next((tmp_path / ".omf-retrieval" / "snapshots").iterdir())
    (snapshot / "model.safetensors").write_bytes(b"tampered")
    assert provider.is_ready() is False


def test_failed_download_does_not_publish_partial_manifest(tmp_path: Path) -> None:
    def fail(**kwargs: object) -> Path:
        destination = kwargs["destination"]
        assert isinstance(destination, Path)
        (destination / "partial.bin").write_bytes(b"partial")
        raise RuntimeError("secret-download")

    with pytest.raises(ModelPrepareError) as captured:
        prepare_embedding_model(_settings(tmp_path), downloader=fail)

    assert "secret" not in str(captured.value)
    assert not model_manifest_path(tmp_path).exists()
    assert not list((tmp_path / ".omf-retrieval").glob(".prepare-*"))


def test_failure_preserves_an_existing_valid_manifest(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    original = prepare_embedding_model(
        settings, downloader=_downloader([], b"original")
    )

    def fail(**kwargs: object) -> Path:
        raise OSError("secret-path")

    with pytest.raises(ModelPrepareError):
        prepare_embedding_model(settings, downloader=fail)

    assert model_manifest_path(tmp_path).read_bytes() == original
    assert verify_model_manifest(
        tmp_path,
        model_name=EMBEDDING_MODEL_NAME,
        revision=EMBEDDING_MODEL_REVISION,
    )


def test_atomic_publish_failure_preserves_existing_manifest_and_cleans_temp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings(tmp_path)
    original = prepare_embedding_model(
        settings, downloader=_downloader([], b"original")
    )

    def fail_replace(source: object, destination: object) -> None:
        raise OSError("secret-replace")

    monkeypatch.setattr(prepare_module.os, "replace", fail_replace)
    with pytest.raises(ModelPrepareError) as captured:
        prepare_embedding_model(settings, downloader=_downloader([], b"changed"))

    assert "secret" not in str(captured.value)
    assert model_manifest_path(tmp_path).read_bytes() == original
    assert not list((tmp_path / ".omf-retrieval").glob(".manifest-*"))


def test_rename_failure_preserves_manifest_and_does_not_publish_partial_snapshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings(tmp_path)
    original = prepare_embedding_model(
        settings, downloader=_downloader([], b"original")
    )
    original_snapshots = {
        path.name for path in (tmp_path / ".omf-retrieval" / "snapshots").iterdir()
    }

    def fail_rename(source: object, destination: object) -> None:
        raise OSError("secret-rename")

    monkeypatch.setattr(prepare_module.os, "rename", fail_rename)
    with pytest.raises(ModelPrepareError) as captured:
        prepare_embedding_model(settings, downloader=_downloader([], b"changed"))

    assert "secret" not in str(captured.value)
    assert model_manifest_path(tmp_path).read_bytes() == original
    assert {
        path.name for path in (tmp_path / ".omf-retrieval" / "snapshots").iterdir()
    } == original_snapshots


def test_preexisting_content_coordinate_is_never_deleted_on_conflict(
    tmp_path: Path,
) -> None:
    probe_cache = tmp_path / "probe"
    probe = prepare_embedding_model(
        _settings(probe_cache), downloader=_downloader([], b"desired")
    )
    coordinate = json.loads(probe)["cache"]["snapshot"]

    cache = tmp_path / "cache"
    conflict = cache.joinpath(*coordinate.split("/"))
    conflict.mkdir(parents=True)
    (conflict / "config.json").write_bytes(b'{"preexisting":true}')
    (conflict / "model.safetensors").write_bytes(b"do-not-delete")

    with pytest.raises(ModelPrepareError, match="preparation failed"):
        prepare_embedding_model(
            _settings(cache), downloader=_downloader([], b"desired")
        )

    assert (conflict / "model.safetensors").read_bytes() == b"do-not-delete"
    assert not model_manifest_path(cache).exists()


def test_prepare_rejects_concurrent_lock_without_damaging_manifest(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    original = prepare_embedding_model(settings, downloader=_downloader([]))
    lock = tmp_path / ".omf-retrieval" / "prepare.lock"
    lock.write_bytes(b"busy")

    with pytest.raises(ModelPrepareError, match="preparation failed"):
        prepare_embedding_model(settings, downloader=_downloader([]))

    assert model_manifest_path(tmp_path).read_bytes() == original
    assert lock.read_bytes() == b"busy"


@pytest.mark.parametrize("field", ["embedding_model_name", "embedding_model_revision"])
def test_prepare_rejects_nonfixed_model_identity(tmp_path: Path, field: str) -> None:
    settings = _settings(tmp_path)
    object.__setattr__(settings, field, "changed")
    with pytest.raises(ModelPrepareError, match="preparation failed"):
        prepare_embedding_model(settings, downloader=_downloader([]))


def test_prepare_rejects_wrong_downloader_coordinate(tmp_path: Path) -> None:
    def wrong(**kwargs: object) -> Path:
        destination = kwargs["destination"]
        assert isinstance(destination, Path)
        (destination / "model.bin").write_bytes(b"model")
        return tmp_path

    with pytest.raises(ModelPrepareError, match="preparation failed"):
        prepare_embedding_model(_settings(tmp_path), downloader=wrong)


@pytest.mark.parametrize(
    "failure", [KeyboardInterrupt(), SystemExit(7), _PrepareAbort()]
)
def test_prepare_propagates_process_control_exceptions(
    tmp_path: Path, failure: BaseException
) -> None:
    def fail(**kwargs: object) -> Path:
        raise failure

    with pytest.raises(type(failure)) as captured:
        prepare_embedding_model(_settings(tmp_path), downloader=fail)
    assert captured.value is failure
    assert not model_manifest_path(tmp_path).exists()


def test_prepare_with_injected_downloader_does_not_import_heavy_libraries(
    tmp_path: Path,
) -> None:
    before = {
        name
        for name in sys.modules
        if name in {"torch", "transformers", "sentence_transformers"}
    }
    prepare_embedding_model(_settings(tmp_path), downloader=_downloader([]))
    after = {
        name
        for name in sys.modules
        if name in {"torch", "transformers", "sentence_transformers"}
    }
    assert after == before


def test_default_downloader_uses_pinned_local_destination_without_remote_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[dict[str, object]] = []
    destination = tmp_path / "destination"
    destination.mkdir()

    def snapshot_download(**kwargs: object) -> str:
        calls.append(kwargs)
        return str(destination)

    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(snapshot_download=snapshot_download),
    )
    result = download_model_snapshot(
        model_name=EMBEDDING_MODEL_NAME,
        revision=EMBEDDING_MODEL_REVISION,
        cache_dir=tmp_path,
        destination=destination,
        local_files_only=False,
        trust_remote_code=False,
    )
    assert result == destination
    assert calls == [
        {
            "repo_id": EMBEDDING_MODEL_NAME,
            "revision": EMBEDDING_MODEL_REVISION,
            "cache_dir": str(tmp_path),
            "local_dir": str(destination),
            "local_files_only": False,
            "allow_patterns": ["*.json", "*.model", "*.safetensors", "*.txt"],
        }
    ]
