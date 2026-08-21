"""Semantic durability-event contract tests for model preparation."""

from __future__ import annotations

import stat
from collections.abc import Callable
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

DURABILITY_EVENTS = (
    "snapshot-source-parent",
    "snapshot-destination-parent",
    "manifest-temp-file",
    "manifest-temp-parent",
    "manifest-backup-parent",
    "manifest-published-parent",
    "manifest-cleanup-parent",
)

RECOVERY_DURABILITY_EVENTS = (
    "restore-temp-file",
    "restore-parent-before-replace",
    "restore-manifest-replace",
    "restore-parent-after-replace",
)


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


class _DurabilityProbe:
    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        cache: Path,
        *,
        failure_event: str | None,
    ) -> None:
        self.events: list[str] = []
        self.failure_event = failure_event
        self.failure_seen = False
        self.failure_exception = OSError("secret-durability-event")
        self.phase: str | None = None
        self.restore_descriptors: set[int] = set()
        self.snapshots = cache / ".omf-retrieval" / "snapshots"
        self.original_rename = prepare_module.os.rename
        self.original_fsync = prepare_module.os.fsync
        self.original_fsync_directory = prepare_module._fsync_directory
        self.original_link = prepare_module.os.link
        self.original_replace = prepare_module.os.replace
        self.original_unlink = prepare_module.Path.unlink
        self.original_mkstemp = prepare_module.tempfile.mkstemp
        monkeypatch.setattr(prepare_module.os, "rename", self.rename)
        monkeypatch.setattr(prepare_module.os, "fsync", self.fsync)
        monkeypatch.setattr(prepare_module, "_fsync_directory", self.fsync_directory)
        monkeypatch.setattr(prepare_module.os, "link", self.link)
        monkeypatch.setattr(prepare_module.os, "replace", self.replace)
        monkeypatch.setattr(
            prepare_module.Path,
            "unlink",
            lambda path, *args, **kwargs: self.unlink(path, *args, **kwargs),
        )
        monkeypatch.setattr(prepare_module.tempfile, "mkstemp", self.mkstemp)

    def invoke(self, event: str, operation: Callable[[], None]) -> None:
        self.events.append(event)
        if event == self.failure_event and not self.failure_seen:
            self.failure_seen = True
            raise self.failure_exception
        operation()

    def mkstemp(self, *args: object, **kwargs: object) -> tuple[int, str]:
        descriptor, name = self.original_mkstemp(*args, **kwargs)
        if Path(name).name.startswith(".manifest-restore-"):
            self.restore_descriptors.add(descriptor)
        return descriptor, name

    def rename(self, source: object, destination: object) -> None:
        self.original_rename(source, destination)
        if Path(destination).parent == self.snapshots:
            self.phase = "snapshot-source-parent"

    def fsync(self, descriptor: int) -> None:
        if stat.S_ISDIR(prepare_module.os.fstat(descriptor).st_mode):
            self.original_fsync(descriptor)
            return
        if descriptor in self.restore_descriptors:
            self.invoke("restore-temp-file", lambda: self.original_fsync(descriptor))
            self.phase = "restore-parent-before-replace"
            return
        self.invoke("manifest-temp-file", lambda: self.original_fsync(descriptor))

    def fsync_directory(self, path: Path) -> None:
        if self.phase is None:
            raise AssertionError("durability phase was not established")
        event = self.phase
        self.invoke(event, lambda: self.original_fsync_directory(path))
        if event == "snapshot-source-parent":
            self.phase = "snapshot-destination-parent"
        elif event == "snapshot-destination-parent":
            self.phase = "manifest-temp-parent"

    def link(self, source: object, destination: object, **kwargs: object) -> None:
        self.original_link(source, destination, **kwargs)
        self.phase = "manifest-backup-parent"

    def replace(self, source: object, destination: object) -> None:
        source_path = Path(source)
        if source_path.name.startswith(".manifest-restore-"):
            self.invoke(
                "restore-manifest-replace",
                lambda: self.original_replace(source, destination),
            )
            self.phase = "restore-parent-after-replace"
            return
        self.original_replace(source, destination)
        if (
            source_path.name.startswith(".manifest-")
            and not source_path.name.startswith(".manifest-backup-")
            and not source_path.name.startswith(".manifest-restore-")
        ):
            self.phase = "manifest-published-parent"

    def unlink(self, path: Path, *args: object, **kwargs: object) -> None:
        populated_backup = (
            path.name.startswith(".manifest-backup-")
            and path.exists()
            and path.stat().st_size > 0
        )
        self.original_unlink(path, *args, **kwargs)
        if populated_backup:
            self.phase = "manifest-cleanup-parent"


def test_success_fsyncs_every_durability_event_in_semantic_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings(tmp_path)
    prepare_embedding_model(settings, downloader=_downloader(b"original"))
    probe = _DurabilityProbe(monkeypatch, tmp_path, failure_event=None)

    prepare_embedding_model(settings, downloader=_downloader(b"changed"))

    assert probe.events == list(DURABILITY_EVENTS)


@pytest.mark.parametrize("failure_event", DURABILITY_EVENTS)
def test_every_fsync_failure_restores_prior_valid_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure_event: str
) -> None:
    settings = _settings(tmp_path)
    original = prepare_embedding_model(settings, downloader=_downloader(b"original"))
    probe = _DurabilityProbe(monkeypatch, tmp_path, failure_event=failure_event)

    with pytest.raises(ModelPrepareError) as captured:
        prepare_embedding_model(settings, downloader=_downloader(b"changed"))

    assert probe.failure_seen is True
    assert failure_event in probe.events
    assert "secret" not in str(captured.value)
    assert model_manifest_path(tmp_path).read_bytes() == original
    assert verify_model_manifest(
        tmp_path,
        model_name=EMBEDDING_MODEL_NAME,
        revision=EMBEDDING_MODEL_REVISION,
    )
    assert not list((tmp_path / ".omf-retrieval").glob(".manifest-*"))
    assert not (tmp_path / ".omf-retrieval" / "prepare.lock").exists()


def test_cleanup_failure_runs_every_restore_durability_event(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings(tmp_path)
    original = prepare_embedding_model(settings, downloader=_downloader(b"original"))
    probe = _DurabilityProbe(
        monkeypatch, tmp_path, failure_event="manifest-cleanup-parent"
    )

    with pytest.raises(ModelPrepareError):
        prepare_embedding_model(settings, downloader=_downloader(b"changed"))

    assert probe.events == [*DURABILITY_EVENTS, *RECOVERY_DURABILITY_EVENTS]
    assert model_manifest_path(tmp_path).read_bytes() == original
    assert verify_model_manifest(
        tmp_path,
        model_name=EMBEDDING_MODEL_NAME,
        revision=EMBEDDING_MODEL_REVISION,
    )
    assert not list((tmp_path / ".omf-retrieval").glob(".manifest-*"))


def test_owned_manifest_restore_has_exact_semantic_durability_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "manifest.json"
    path.write_bytes(b"published")
    metadata = path.stat()
    probe = _DurabilityProbe(monkeypatch, tmp_path, failure_event=None)

    prepare_module._restore_owned_manifest(
        path,
        b"previous",
        owned_identity=(metadata.st_dev, metadata.st_ino),
    )

    assert probe.events == list(RECOVERY_DURABILITY_EVENTS)
    assert path.read_bytes() == b"previous"
    assert not list(tmp_path.glob(".manifest-restore-*"))


@pytest.mark.parametrize("failure_event", RECOVERY_DURABILITY_EVENTS)
def test_owned_manifest_restore_propagates_every_durability_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure_event: str
) -> None:
    path = tmp_path / "manifest.json"
    path.write_bytes(b"published")
    metadata = path.stat()
    probe = _DurabilityProbe(monkeypatch, tmp_path, failure_event=failure_event)

    with pytest.raises(OSError) as captured:
        prepare_module._restore_owned_manifest(
            path,
            b"previous",
            owned_identity=(metadata.st_dev, metadata.st_ino),
        )

    assert captured.value is probe.failure_exception
    assert probe.failure_seen is True
    assert failure_event in probe.events
    assert not list(tmp_path.glob(".manifest-restore-*"))
