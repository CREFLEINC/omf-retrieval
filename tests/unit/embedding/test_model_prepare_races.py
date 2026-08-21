"""Deterministic filesystem-race sentinels for model preparation."""

from __future__ import annotations

from pathlib import Path

import pytest

import omf_retrieval.infrastructure.embedding.prepare as prepare_module
from omf_retrieval.infrastructure.embedding.manifest import (
    model_manifest_path,
    verify_model_manifest,
)
from omf_retrieval.infrastructure.embedding.prepare import (
    EMBEDDING_MODEL_NAME,
    EMBEDDING_MODEL_REVISION,
    ModelPrepareError,
    prepare_embedding_model,
)
from omf_retrieval.settings import Settings


class _FinalValidationAbort(BaseException):
    pass


def _settings(cache: Path) -> Settings:
    return Settings(environment="test", embedding_cache_dir=cache)


def _downloader(content: bytes):
    def download(**kwargs: object) -> Path:
        destination = kwargs["destination"]
        assert isinstance(destination, Path)
        (destination / "model.safetensors").write_bytes(content)
        (destination / "config.json").write_bytes(b"{}")
        return destination

    return download


def test_replace_wrapper_cannot_substitute_a_different_manifest_inode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings(tmp_path)
    original = prepare_embedding_model(settings, downloader=_downloader(b"original"))
    manifest_path = model_manifest_path(tmp_path)
    external_prior = tmp_path / "external-prior.json"
    external_prior.write_bytes(original)
    external_prior.chmod(0o600)
    original_replace = prepare_module.os.replace
    substituted = False

    def substitute_after_replace(source: object, destination: object) -> None:
        nonlocal substituted
        original_replace(source, destination)
        source_path = Path(source)
        if (
            Path(destination) == manifest_path
            and source_path.name.startswith(".manifest-")
            and not source_path.name.startswith(".manifest-backup-")
            and not substituted
        ):
            substituted = True
            original_replace(external_prior, manifest_path)

    monkeypatch.setattr(prepare_module.os, "replace", substitute_after_replace)

    with pytest.raises(ModelPrepareError, match="preparation failed"):
        prepare_embedding_model(settings, downloader=_downloader(b"original"))

    assert substituted is True
    assert manifest_path.read_bytes() == original
    assert verify_model_manifest(
        tmp_path,
        model_name=EMBEDDING_MODEL_NAME,
        revision=EMBEDDING_MODEL_REVISION,
    )
    assert not list((tmp_path / ".omf-retrieval").glob(".manifest-*"))


def test_commit_fsync_snapshot_swap_is_caught_by_last_full_validation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings(tmp_path)
    original = prepare_embedding_model(settings, downloader=_downloader(b"model"))
    snapshots = tmp_path / ".omf-retrieval" / "snapshots"
    original_fsync_directory = prepare_module._fsync_directory
    directory_calls = 0
    swapped = False

    def swap_after_commit_fsync(path: Path) -> None:
        nonlocal directory_calls, swapped
        original_fsync_directory(path)
        directory_calls += 1
        if directory_calls == 4:
            assert not list((tmp_path / ".omf-retrieval").glob(".manifest-backup-*"))
            final = next(snapshots.iterdir())
            final.rename(snapshots / "renamed-original")
            final.mkdir()
            (final / "config.json").write_bytes(b'{"replacement":true}')
            (final / "model.safetensors").write_bytes(b"replacement")
            swapped = True

    monkeypatch.setattr(prepare_module, "_fsync_directory", swap_after_commit_fsync)

    with pytest.raises(ModelPrepareError, match="preparation failed"):
        prepare_embedding_model(settings, downloader=_downloader(b"model"))

    assert swapped is True
    assert model_manifest_path(tmp_path).read_bytes() == original
    assert not verify_model_manifest(
        tmp_path,
        model_name=EMBEDDING_MODEL_NAME,
        revision=EMBEDDING_MODEL_REVISION,
    )
    assert not list((tmp_path / ".omf-retrieval").glob(".manifest-*"))
    assert not (tmp_path / ".omf-retrieval" / "prepare.lock").exists()


def test_success_has_no_model_cache_mutation_after_final_full_validation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings(tmp_path)
    prepare_embedding_model(settings, downloader=_downloader(b"model"))
    original_validation = prepare_module.verify_pinned_model_manifest
    original_fsync_directory = prepare_module._fsync_directory
    original_replace = prepare_module.os.replace
    original_rename = prepare_module.os.rename
    original_unlink = prepare_module.Path.unlink
    validation_completed = False
    later_mutations: list[str] = []

    def record_validation(*args: object, **kwargs: object) -> bool:
        nonlocal validation_completed
        result = original_validation(*args, **kwargs)
        if result:
            validation_completed = True
        return result

    def record_fsync_directory(path: Path) -> None:
        if validation_completed:
            later_mutations.append("fsync-directory")
        original_fsync_directory(path)

    def record_replace(source: object, destination: object) -> None:
        if validation_completed:
            later_mutations.append("replace")
        original_replace(source, destination)

    def record_rename(source: object, destination: object) -> None:
        if validation_completed:
            later_mutations.append("rename")
        original_rename(source, destination)

    def record_unlink(path: Path, *args: object, **kwargs: object) -> None:
        if validation_completed and path.name != "prepare.lock":
            later_mutations.append("unlink")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(
        prepare_module, "verify_pinned_model_manifest", record_validation
    )
    monkeypatch.setattr(prepare_module, "_fsync_directory", record_fsync_directory)
    monkeypatch.setattr(prepare_module.os, "replace", record_replace)
    monkeypatch.setattr(prepare_module.os, "rename", record_rename)
    monkeypatch.setattr(prepare_module.Path, "unlink", record_unlink)

    prepare_embedding_model(settings, downloader=_downloader(b"model"))

    assert validation_completed is True
    assert later_mutations == []


@pytest.mark.parametrize(
    "failure", [KeyboardInterrupt(), SystemExit(11), _FinalValidationAbort()]
)
def test_final_validation_propagates_process_control_and_restores_prior_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: BaseException
) -> None:
    settings = _settings(tmp_path)
    original = prepare_embedding_model(settings, downloader=_downloader(b"model"))

    def abort(*args: object, **kwargs: object) -> bool:
        raise failure

    monkeypatch.setattr(prepare_module, "verify_pinned_model_manifest", abort)

    with pytest.raises(type(failure)) as captured:
        prepare_embedding_model(settings, downloader=_downloader(b"model"))

    assert captured.value is failure
    assert model_manifest_path(tmp_path).read_bytes() == original
    assert verify_model_manifest(
        tmp_path,
        model_name=EMBEDDING_MODEL_NAME,
        revision=EMBEDDING_MODEL_REVISION,
    )
    assert not list((tmp_path / ".omf-retrieval").glob(".manifest-*"))


def test_final_validation_ordinary_error_is_generic_and_restores_prior_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings(tmp_path)
    original = prepare_embedding_model(settings, downloader=_downloader(b"model"))

    def fail(*args: object, **kwargs: object) -> bool:
        raise OSError("secret-final-validation")

    monkeypatch.setattr(prepare_module, "verify_pinned_model_manifest", fail)

    with pytest.raises(ModelPrepareError) as captured:
        prepare_embedding_model(settings, downloader=_downloader(b"model"))

    assert "secret" not in str(captured.value)
    assert model_manifest_path(tmp_path).read_bytes() == original
    assert verify_model_manifest(
        tmp_path,
        model_name=EMBEDDING_MODEL_NAME,
        revision=EMBEDDING_MODEL_REVISION,
    )
    assert not list((tmp_path / ".omf-retrieval").glob(".manifest-*"))
