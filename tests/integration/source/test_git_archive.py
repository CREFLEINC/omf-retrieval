"""Integration tests for immutable Git archive source snapshots."""

import hashlib
import importlib
import importlib.util
import io
import os
import stat
import subprocess
import tarfile
from pathlib import Path
from typing import Any, Callable

import pytest

from omf_retrieval.application.indexing.ports import ArchiveFile, SourceSnapshot
from omf_retrieval.infrastructure.source.profiles import SourceProfileConfig


class StringSubclass(str):
    """Represent an invalid string subclass at an exact-type boundary."""


class IntegerSubclass(int):
    """Represent an invalid integer subclass at an exact-type boundary."""


def _git(repo: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return completed.stdout.strip()


def _git_status(repo: Path) -> str:
    environment = os.environ.copy()
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    completed = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=repo,
        env=environment,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return completed.stdout.strip()


def _write(repo: Path, relative_path: str, content: bytes) -> None:
    destination = repo / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)


def _commit_all(repo: Path, message: str) -> str:
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _make_two_commit_repo(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "source"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Task 4B Test")
    _git(repo, "config", "user.email", "task4b@example.invalid")
    _write(repo, ".gitignore", b"ignored.tmp\n")
    _write(repo, "docs/research/direct.md", b"A direct\x00bytes\n")
    _write(repo, "docs/planning/nested/plan.md", "계획 A\n".encode())
    _write(repo, "uiux/spec.md", b"UI A\n")
    _write(repo, "docs/raw/secret.md", b"secret\n")
    _write(repo, "docs/research/image.png", b"not markdown\n")
    commit_a = _commit_all(repo, "commit A")

    _write(repo, "docs/research/direct.md", b"B direct\n")
    _write(repo, "docs/research/new.md", b"only B\n")
    commit_b = _commit_all(repo, "commit B")
    _write(repo, "ignored.tmp", b"ignored worktree content\n")
    return repo, commit_a, commit_b


def _index_state(repo: Path) -> tuple[int, int, str]:
    index_path = repo / ".git/index"
    metadata = index_path.stat()
    return (
        metadata.st_mtime_ns,
        metadata.st_size,
        hashlib.sha256(index_path.read_bytes()).hexdigest(),
    )


def _omf_test_profile() -> SourceProfileConfig:
    return SourceProfileConfig(
        source_key="test",
        include_patterns=(
            "docs/research/**/*.md",
            "docs/planning/**/*.md",
            "uiux/**/*.md",
        ),
        exclude_patterns=("docs/raw/**",),
    )


def _worktree_state(repo: Path) -> dict[str, bytes]:
    return {
        path.relative_to(repo).as_posix(): path.read_bytes()
        for path in repo.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(repo).parts
    }


def _caught_exception(operation: Callable[[], object]) -> Exception | None:
    try:
        operation()
    except Exception as error:  # noqa: BLE001 - assertion inspects boundary type
        return error
    return None


def _captured_result(
    operation: Callable[[], object],
) -> tuple[object | None, Exception | None]:
    try:
        return operation(), None
    except Exception as error:  # noqa: BLE001 - assertion inspects boundary type
        return None, error


def _tar_bytes(members: list[tuple[tarfile.TarInfo, bytes | None]]) -> bytes:
    archive_buffer = io.BytesIO()
    with tarfile.open(
        fileobj=archive_buffer, mode="w", format=tarfile.PAX_FORMAT
    ) as archive:
        for member, content in members:
            if content is not None:
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))
            else:
                archive.addfile(member)
    return archive_buffer.getvalue()


def _regular_member(
    name: str,
    content: bytes,
    *,
    pax_path: str | None = None,
) -> tuple[tarfile.TarInfo, bytes]:
    member = tarfile.TarInfo(name)
    if pax_path is not None:
        member.pax_headers = {"path": pax_path}
    return member, content


def _crafted_archive_snapshot(
    *,
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    repo: Path,
    commit_sha: str,
    archive_bytes: bytes,
    temp_parent: Path,
    observed_modes: list[tuple[int, tuple[tuple[str, int], ...]]] | None = None,
    provider_options: dict[str, object] | None = None,
) -> SourceSnapshot:
    def crafted_archive_writer(**kwargs: Any) -> None:
        archive_output = kwargs["archive_output"]
        temporary_root = Path(archive_output.name).parent
        if observed_modes is not None:
            observed_modes.append(
                (
                    stat.S_IMODE(temporary_root.stat().st_mode),
                    tuple(
                        sorted(
                            (
                                child.name,
                                stat.S_IMODE(child.stat().st_mode),
                            )
                            for child in temporary_root.iterdir()
                            if child.is_dir()
                        )
                    ),
                )
            )
        archive_output.write(archive_bytes)

    monkeypatch.setattr(module, "_write_git_archive", crafted_archive_writer)
    provider = module.GitArchiveSnapshotProvider(
        _omf_test_profile(),
        temp_parent=temp_parent,
        **(provider_options or {}),
    )
    return provider.snapshot(repo, commit_sha)


def test_git_archive_provider_module_exists() -> None:
    """The infrastructure package exposes the approved Git provider module."""
    module_spec = importlib.util.find_spec(
        "omf_retrieval.infrastructure.source.git_archive"
    )

    assert module_spec is not None


def test_snapshot_reads_requested_commit_bytes_in_lexical_profile_order(
    tmp_path: Path,
) -> None:
    """Using HEAD or worktree files instead of the requested commit is a bug."""
    module_spec = importlib.util.find_spec(
        "omf_retrieval.infrastructure.source.git_archive"
    )
    assert module_spec is not None
    module = importlib.import_module(module_spec.name)
    provider_type = getattr(module, "GitArchiveSnapshotProvider", None)
    assert callable(provider_type)
    repo, commit_a, commit_b = _make_two_commit_repo(tmp_path)
    before = {
        "head": _git(repo, "rev-parse", "HEAD"),
        "status": _git_status(repo),
        "index": _index_state(repo),
        "tracked": (repo / "docs/research/direct.md").read_bytes(),
        "ignored": (repo / "ignored.tmp").read_bytes(),
    }

    snapshot = provider_type(_omf_test_profile()).snapshot(repo, commit_a)

    assert snapshot == SourceSnapshot(
        commit_sha=commit_a,
        archive_files=(
            ArchiveFile("docs/planning/nested/plan.md", "계획 A\n".encode()),
            ArchiveFile("docs/research/direct.md", b"A direct\x00bytes\n"),
            ArchiveFile("uiux/spec.md", b"UI A\n"),
        ),
    )
    assert commit_a != commit_b
    assert _index_state(repo) == before["index"]
    assert _git(repo, "rev-parse", "HEAD") == before["head"] == commit_b
    assert _git_status(repo) == before["status"]
    assert (repo / "docs/research/direct.md").read_bytes() == before["tracked"]
    assert (repo / "ignored.tmp").read_bytes() == before["ignored"]


def test_snapshot_allows_an_empty_profile_result(tmp_path: Path) -> None:
    """A commit without profile-selected files is a valid empty snapshot."""
    module_spec = importlib.util.find_spec(
        "omf_retrieval.infrastructure.source.git_archive"
    )
    assert module_spec is not None
    module = importlib.import_module(module_spec.name)
    provider_type = getattr(module, "GitArchiveSnapshotProvider", None)
    assert callable(provider_type)
    repo, commit_a, _ = _make_two_commit_repo(tmp_path)
    empty_profile = SourceProfileConfig(
        source_key="empty",
        include_patterns=("not-present/**/*.md",),
        exclude_patterns=(),
    )

    snapshot = provider_type(empty_profile).snapshot(repo, commit_a)

    assert snapshot == SourceSnapshot(commit_sha=commit_a, archive_files=())


def test_git_failures_use_a_module_specific_exception() -> None:
    """Callers can distinguish sanitized provider failures from raw subprocess errors."""
    module = importlib.import_module("omf_retrieval.infrastructure.source.git_archive")

    error_type = getattr(module, "GitArchiveSnapshotError", None)

    assert isinstance(error_type, type)
    assert issubclass(error_type, RuntimeError)


def test_provider_cleans_an_injected_private_temp_parent(tmp_path: Path) -> None:
    """Snapshot resources are observable as cleaned without touching the source repo."""
    module = importlib.import_module("omf_retrieval.infrastructure.source.git_archive")
    provider_type = module.GitArchiveSnapshotProvider
    repo, commit_a, _ = _make_two_commit_repo(tmp_path)
    temp_parent = tmp_path / "provider-temporary"
    temp_parent.mkdir()

    try:
        provider = provider_type(_omf_test_profile(), temp_parent=temp_parent)
    except TypeError:
        provider = None

    assert provider is not None
    assert provider.snapshot(repo, commit_a).commit_sha == commit_a
    assert list(temp_parent.iterdir()) == []


@pytest.mark.parametrize("dirty_kind", ["staged", "unstaged", "untracked"])
def test_snapshot_rejects_every_dirty_worktree_state_without_writes(
    tmp_path: Path,
    dirty_kind: str,
) -> None:
    """Missing any staged, unstaged, or untracked guard would archive dirty input."""
    module = importlib.import_module("omf_retrieval.infrastructure.source.git_archive")
    repo, commit_a, _ = _make_two_commit_repo(tmp_path)
    if dirty_kind == "staged":
        _write(repo, "docs/research/staged.md", b"staged\n")
        _git(repo, "add", "docs/research/staged.md")
    elif dirty_kind == "unstaged":
        _write(repo, "docs/research/direct.md", b"unstaged\n")
    else:
        _write(repo, "docs/research/untracked.md", b"untracked\n")
    temp_parent = tmp_path / "provider-temporary"
    temp_parent.mkdir()
    provider = module.GitArchiveSnapshotProvider(
        _omf_test_profile(), temp_parent=temp_parent
    )
    before_files = _worktree_state(repo)
    before_status = _git_status(repo)
    before_index = _index_state(repo)

    caught_error: Exception | None = None
    try:
        provider.snapshot(repo, commit_a)
    except Exception as error:  # noqa: BLE001 - assertion inspects boundary type
        caught_error = error

    assert type(caught_error) is module.GitArchiveSnapshotError
    assert "clean" in str(caught_error).lower()
    assert _worktree_state(repo) == before_files
    assert _git_status(repo) == before_status
    assert _index_state(repo) == before_index
    assert list(temp_parent.iterdir()) == []


@pytest.mark.parametrize(
    "invalid_commit",
    [
        "0123456",
        "0123456789ABCDEF0123456789ABCDEF01234567",
        "HEAD",
        "--help",
        " 0123456789abcdef0123456789abcdef01234567",
        "0123456789abcdef0123456789abcdef01234567 ",
        123,
    ],
)
def test_snapshot_rejects_nonexact_commit_identifiers_before_rev_parse(
    tmp_path: Path,
    invalid_commit: object,
) -> None:
    """Refs, options, abbreviations, whitespace, and non-strings are not commit IDs."""
    module = importlib.import_module("omf_retrieval.infrastructure.source.git_archive")
    repo, _, _ = _make_two_commit_repo(tmp_path)
    provider = module.GitArchiveSnapshotProvider(_omf_test_profile())

    caught_error: Exception | None = None
    try:
        provider.snapshot(repo, invalid_commit)  # type: ignore[arg-type]
    except Exception as error:  # noqa: BLE001 - assertion inspects boundary type
        caught_error = error

    assert type(caught_error) is module.GitArchiveSnapshotError
    assert "commit" in str(caught_error).lower()


def test_snapshot_rejects_a_string_subclass_commit_identifier(tmp_path: Path) -> None:
    """A str subclass cannot bypass the exact builtin commit boundary."""
    module = importlib.import_module("omf_retrieval.infrastructure.source.git_archive")
    repo, commit_a, _ = _make_two_commit_repo(tmp_path)
    provider = module.GitArchiveSnapshotProvider(_omf_test_profile())

    caught_error: Exception | None = None
    try:
        provider.snapshot(repo, StringSubclass(commit_a))
    except Exception as error:  # noqa: BLE001 - assertion inspects boundary type
        caught_error = error

    assert type(caught_error) is module.GitArchiveSnapshotError
    assert "commit" in str(caught_error).lower()


@pytest.mark.parametrize("repository_kind", ["non-repository", "missing"])
def test_snapshot_sanitizes_repository_validation_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    repository_kind: str,
) -> None:
    """Raw paths and secret-like environment values never escape Git failures."""
    module = importlib.import_module("omf_retrieval.infrastructure.source.git_archive")
    repo = tmp_path / "raw-secret-repository-name"
    if repository_kind == "non-repository":
        repo.mkdir()
    monkeypatch.setenv("TASK4B_SECRET_TOKEN", "raw-secret-environment-value")
    provider = module.GitArchiveSnapshotProvider(_omf_test_profile())

    caught_error: Exception | None = None
    try:
        provider.snapshot(repo, "f" * 40)
    except Exception as error:  # noqa: BLE001 - assertion inspects boundary type
        caught_error = error

    assert type(caught_error) is module.GitArchiveSnapshotError
    message = str(caught_error)
    assert "raw-secret" not in message
    assert "TASK4B_SECRET_TOKEN" not in message
    assert "environment-value" not in message


def test_snapshot_sanitizes_a_nonexistent_exact_commit_failure(tmp_path: Path) -> None:
    """A missing object becomes a stable module error rather than CalledProcessError."""
    module = importlib.import_module("omf_retrieval.infrastructure.source.git_archive")
    repo, _, _ = _make_two_commit_repo(tmp_path)
    provider = module.GitArchiveSnapshotProvider(_omf_test_profile())

    caught_error: Exception | None = None
    try:
        provider.snapshot(repo, "f" * 40)
    except Exception as error:  # noqa: BLE001 - assertion inspects boundary type
        caught_error = error

    assert type(caught_error) is module.GitArchiveSnapshotError
    assert "commit" in str(caught_error).lower()


def test_git_commands_use_exact_shell_free_read_only_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Removing argv, cwd, no-lock env, or shell=False violates source isolation."""
    module = importlib.import_module("omf_retrieval.infrastructure.source.git_archive")
    repo, commit_a, _ = _make_two_commit_repo(tmp_path)
    real_run = subprocess.run
    real_popen = subprocess.Popen
    observed_calls: list[tuple[list[str], dict[str, Any]]] = []
    observed_process_calls: list[tuple[list[str], dict[str, Any]]] = []

    def recording_run(
        argv: list[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[Any]:
        observed_calls.append((argv, kwargs.copy()))
        return real_run(argv, **kwargs)

    def recording_popen(argv: list[str], **kwargs: Any) -> subprocess.Popen[bytes]:
        if argv[:2] == ["git", "archive"]:
            observed_process_calls.append((argv, kwargs.copy()))
        return real_popen(argv, **kwargs)

    monkeypatch.setattr(module.subprocess, "run", recording_run)
    monkeypatch.setattr(module.subprocess, "Popen", recording_popen)

    snapshot = module.GitArchiveSnapshotProvider(_omf_test_profile()).snapshot(
        repo, commit_a
    )

    assert snapshot.commit_sha == commit_a
    assert [call[0] for call in observed_calls] == [
        [
            "git",
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--ignore-submodules=none",
        ],
        [
            "git",
            "rev-parse",
            "--verify",
            "--end-of-options",
            f"{commit_a}^{{commit}}",
        ],
    ]
    assert [call[0] for call in observed_process_calls] == [
        ["git", "archive", "--format=tar", commit_a]
    ]
    all_calls = [*observed_calls, *observed_process_calls]
    assert all(type(argv) is list for argv, _ in all_calls)
    assert all(kwargs["cwd"] == repo for _, kwargs in all_calls)
    assert all(kwargs["shell"] is False for _, kwargs in all_calls)
    assert all(kwargs["env"]["GIT_OPTIONAL_LOCKS"] == "0" for _, kwargs in all_calls)


def test_snapshot_rejects_an_actual_git_symlink_member(tmp_path: Path) -> None:
    """A selected Git symlink can never be treated as regular source bytes."""
    module = importlib.import_module("omf_retrieval.infrastructure.source.git_archive")
    repo = tmp_path / "source"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Task 4B Test")
    _git(repo, "config", "user.email", "task4b@example.invalid")
    _write(repo, "docs/research/target.md", b"target bytes\n")
    symlink_path = repo / "docs/research/link.md"
    os.symlink("target.md", symlink_path)
    commit_sha = _commit_all(repo, "symlink")
    before_target = (repo / "docs/research/target.md").read_bytes()

    caught_error: Exception | None = None
    try:
        module.GitArchiveSnapshotProvider(_omf_test_profile()).snapshot(
            repo, commit_sha
        )
    except Exception as error:  # noqa: BLE001 - assertion inspects boundary type
        caught_error = error

    assert type(caught_error) is module.GitArchiveSnapshotError
    assert symlink_path.is_symlink()
    assert (repo / "docs/research/target.md").read_bytes() == before_target
    assert _git_status(repo) == ""


@pytest.mark.parametrize(
    ("member_name", "pax_path"),
    [
        ("/docs/research/absolute.md", None),
        ("docs/research/../../escape.md", None),
        ("docs\\research\\backslash.md", None),
        (".", None),
        ("safe-placeholder.md", "../../pax-escape.md"),
        ("safe-placeholder.md", "/docs/research/pax-absolute.md"),
    ],
)
def test_snapshot_rejects_unsafe_effective_archive_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    member_name: str,
    pax_path: str | None,
) -> None:
    """Unsafe header and PAX-resolved paths cannot reach profile selection."""
    module = importlib.import_module("omf_retrieval.infrastructure.source.git_archive")
    repo, commit_a, _ = _make_two_commit_repo(tmp_path)
    temp_parent = tmp_path / "provider-temporary"
    temp_parent.mkdir()
    archive_bytes = _tar_bytes(
        [_regular_member(member_name, b"raw-secret-content", pax_path=pax_path)]
    )

    caught_error: Exception | None = None
    try:
        _crafted_archive_snapshot(
            module=module,
            monkeypatch=monkeypatch,
            repo=repo,
            commit_sha=commit_a,
            archive_bytes=archive_bytes,
            temp_parent=temp_parent,
        )
    except Exception as error:  # noqa: BLE001 - assertion inspects boundary type
        caught_error = error

    assert type(caught_error) is module.GitArchiveSnapshotError
    assert "raw-secret-content" not in str(caught_error)
    assert list(temp_parent.iterdir()) == []


def test_snapshot_rejects_canonical_duplicate_archive_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two archive headers cannot resolve to the same extraction destination."""
    module = importlib.import_module("omf_retrieval.infrastructure.source.git_archive")
    repo, commit_a, _ = _make_two_commit_repo(tmp_path)
    temp_parent = tmp_path / "provider-temporary"
    temp_parent.mkdir()
    archive_bytes = _tar_bytes(
        [
            _regular_member("docs/research/duplicate.md", b"first"),
            _regular_member("docs/research/./duplicate.md", b"second"),
        ]
    )

    caught_error: Exception | None = None
    try:
        _crafted_archive_snapshot(
            module=module,
            monkeypatch=monkeypatch,
            repo=repo,
            commit_sha=commit_a,
            archive_bytes=archive_bytes,
            temp_parent=temp_parent,
        )
    except Exception as error:  # noqa: BLE001 - assertion inspects boundary type
        caught_error = error

    assert type(caught_error) is module.GitArchiveSnapshotError
    assert list(temp_parent.iterdir()) == []


@pytest.mark.parametrize(
    "member_type",
    [
        tarfile.SYMTYPE,
        tarfile.LNKTYPE,
        tarfile.FIFOTYPE,
        tarfile.CHRTYPE,
        tarfile.BLKTYPE,
        tarfile.CONTTYPE,
        tarfile.GNUTYPE_SPARSE,
        b"Z",
    ],
)
def test_snapshot_rejects_links_special_and_unknown_members_even_when_excluded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    member_type: bytes,
) -> None:
    """Profile exclusion never bypasses global archive member-type validation."""
    module = importlib.import_module("omf_retrieval.infrastructure.source.git_archive")
    repo, commit_a, _ = _make_two_commit_repo(tmp_path)
    temp_parent = tmp_path / "provider-temporary"
    temp_parent.mkdir()
    member = tarfile.TarInfo("outside-profile/danger")
    member.type = member_type
    if member_type in {tarfile.SYMTYPE, tarfile.LNKTYPE}:
        member.linkname = "docs/research/target.md"
    if member_type in {tarfile.CHRTYPE, tarfile.BLKTYPE}:
        member.devmajor = 1
        member.devminor = 3
    archive_bytes = _tar_bytes([(member, None)])

    caught_error: Exception | None = None
    try:
        _crafted_archive_snapshot(
            module=module,
            monkeypatch=monkeypatch,
            repo=repo,
            commit_sha=commit_a,
            archive_bytes=archive_bytes,
            temp_parent=temp_parent,
        )
    except Exception as error:  # noqa: BLE001 - assertion inspects boundary type
        caught_error = error

    assert type(caught_error) is module.GitArchiveSnapshotError
    assert list(temp_parent.iterdir()) == []


def test_snapshot_rejects_a_negative_declared_size_even_when_excluded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A malformed negative file size cannot hide outside the selected profile."""
    module = importlib.import_module("omf_retrieval.infrastructure.source.git_archive")
    repo, commit_a, _ = _make_two_commit_repo(tmp_path)
    temp_parent = tmp_path / "provider-temporary"
    temp_parent.mkdir()
    member = tarfile.TarInfo("outside/negative-size.bin")
    member.size = -1
    archive_bytes = _tar_bytes([(member, None)])

    caught_error = _caught_exception(
        lambda: _crafted_archive_snapshot(
            module=module,
            monkeypatch=monkeypatch,
            repo=repo,
            commit_sha=commit_a,
            archive_bytes=archive_bytes,
            temp_parent=temp_parent,
        )
    )

    assert type(caught_error) is module.GitArchiveSnapshotError
    assert list(temp_parent.iterdir()) == []


def test_snapshot_rejects_a_temp_parent_inside_the_source_repo(tmp_path: Path) -> None:
    """Even ignored temporary artifacts must never be created in the source worktree."""
    module = importlib.import_module("omf_retrieval.infrastructure.source.git_archive")
    repo, commit_a, _ = _make_two_commit_repo(tmp_path)
    exclude_path = repo / ".git/info/exclude"
    exclude_path.write_text("provider-temporary/\n", encoding="utf-8")
    temp_parent = repo / "provider-temporary"
    temp_parent.mkdir()
    before_exclude = exclude_path.read_bytes()

    caught_error = _caught_exception(
        lambda: module.GitArchiveSnapshotProvider(
            _omf_test_profile(), temp_parent=temp_parent
        ).snapshot(repo, commit_a)
    )

    assert type(caught_error) is module.GitArchiveSnapshotError
    assert "temporary" in str(caught_error).lower()
    assert list(temp_parent.iterdir()) == []
    assert exclude_path.read_bytes() == before_exclude
    assert _git_status(repo) == ""


def test_snapshot_accepts_safe_directories_and_long_unicode_pax_file_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Safe PAX paths retain exact bytes through real validation and extraction."""
    module = importlib.import_module("omf_retrieval.infrastructure.source.git_archive")
    repo, commit_a, _ = _make_two_commit_repo(tmp_path)
    temp_parent = tmp_path / "provider-temporary"
    temp_parent.mkdir()
    long_path = "docs/research/" + ("긴경로" * 35) + "/문서.md"
    directory = tarfile.TarInfo("docs/research/safe/")
    directory.type = tarfile.DIRTYPE
    content = "원본 bytes\x00보존\n".encode()
    archive_bytes = _tar_bytes(
        [
            (directory, None),
            _regular_member("pax-placeholder", content, pax_path=long_path),
        ]
    )
    observed_modes: list[tuple[int, tuple[tuple[str, int], ...]]] = []

    snapshot = _crafted_archive_snapshot(
        module=module,
        monkeypatch=monkeypatch,
        repo=repo,
        commit_sha=commit_a,
        archive_bytes=archive_bytes,
        temp_parent=temp_parent,
        observed_modes=observed_modes,
    )

    assert snapshot == SourceSnapshot(
        commit_sha=commit_a,
        archive_files=(ArchiveFile(source_path=long_path, content=content),),
    )
    assert observed_modes == [(0o700, (("extracted", 0o700),))]
    assert list(temp_parent.iterdir()) == []


@pytest.mark.parametrize("archive_kind", ["corrupt", "truncated"])
def test_snapshot_fails_closed_on_corrupt_or_truncated_tar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    archive_kind: str,
) -> None:
    """Malformed headers and declared/read truncation become sanitized failures."""
    module = importlib.import_module("omf_retrieval.infrastructure.source.git_archive")
    repo, commit_a, _ = _make_two_commit_repo(tmp_path)
    temp_parent = tmp_path / "provider-temporary"
    temp_parent.mkdir()
    if archive_kind == "corrupt":
        archive_bytes = b"not a tar archive\nraw-secret-content"
    else:
        complete_tar = _tar_bytes(
            [_regular_member("docs/research/truncated.md", b"x" * 1_024)]
        )
        archive_bytes = complete_tar[: 512 + 100]

    caught_error: Exception | None = None
    try:
        _crafted_archive_snapshot(
            module=module,
            monkeypatch=monkeypatch,
            repo=repo,
            commit_sha=commit_a,
            archive_bytes=archive_bytes,
            temp_parent=temp_parent,
        )
    except Exception as error:  # noqa: BLE001 - assertion inspects boundary type
        caught_error = error

    assert type(caught_error) is module.GitArchiveSnapshotError
    assert "raw-secret-content" not in str(caught_error)
    assert list(temp_parent.iterdir()) == []


@pytest.mark.parametrize(
    ("limit_name", "invalid_value"),
    [
        (limit_name, invalid_value)
        for limit_name in (
            "max_members",
            "max_file_bytes",
            "max_total_bytes",
            "max_archive_bytes",
        )
        for invalid_value in (0, -1, True, IntegerSubclass(1), 1.0)
    ],
)
def test_provider_rejects_nonpositive_or_nonexact_integer_limits(
    limit_name: str,
    invalid_value: object,
) -> None:
    """Disabled, boolean, subclass, and non-integer resource limits fail closed."""
    module = importlib.import_module("omf_retrieval.infrastructure.source.git_archive")

    caught_error = _caught_exception(
        lambda: module.GitArchiveSnapshotProvider(
            _omf_test_profile(), **{limit_name: invalid_value}
        )
    )

    assert type(caught_error) is module.GitArchiveSnapshotError
    assert "limit" in str(caught_error).lower()


def test_archive_member_count_allows_exact_limit_and_rejects_one_over(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every included or excluded tar member consumes the global member budget."""
    module = importlib.import_module("omf_retrieval.infrastructure.source.git_archive")
    repo, commit_a, _ = _make_two_commit_repo(tmp_path)
    temp_parent = tmp_path / "provider-temporary"
    temp_parent.mkdir()
    exact_archive = _tar_bytes(
        [
            _regular_member("outside/one.txt", b"1"),
            _regular_member("outside/two.txt", b"2"),
        ]
    )
    one_over_archive = _tar_bytes(
        [
            _regular_member("outside/one.txt", b"1"),
            _regular_member("outside/two.txt", b"2"),
            _regular_member("outside/three.txt", b"3"),
        ]
    )

    exact_snapshot, exact_error = _captured_result(
        lambda: _crafted_archive_snapshot(
            module=module,
            monkeypatch=monkeypatch,
            repo=repo,
            commit_sha=commit_a,
            archive_bytes=exact_archive,
            temp_parent=temp_parent,
            provider_options={"max_members": 2},
        )
    )
    caught_error = _caught_exception(
        lambda: _crafted_archive_snapshot(
            module=module,
            monkeypatch=monkeypatch,
            repo=repo,
            commit_sha=commit_a,
            archive_bytes=one_over_archive,
            temp_parent=temp_parent,
            provider_options={"max_members": 2},
        )
    )

    assert exact_error is None
    assert isinstance(exact_snapshot, SourceSnapshot)
    assert exact_snapshot.archive_files == ()
    assert type(caught_error) is module.GitArchiveSnapshotError
    assert "limit" in str(caught_error).lower()
    assert list(temp_parent.iterdir()) == []


def test_included_file_allows_exact_limit_and_rejects_one_over(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An included regular file cannot exceed its individual byte budget."""
    module = importlib.import_module("omf_retrieval.infrastructure.source.git_archive")
    repo, commit_a, _ = _make_two_commit_repo(tmp_path)
    temp_parent = tmp_path / "provider-temporary"
    temp_parent.mkdir()
    exact_content = b"1234"
    exact_archive = _tar_bytes(
        [_regular_member("docs/research/exact.md", exact_content)]
    )
    one_over_archive = _tar_bytes([_regular_member("docs/research/over.md", b"12345")])

    exact_snapshot, exact_error = _captured_result(
        lambda: _crafted_archive_snapshot(
            module=module,
            monkeypatch=monkeypatch,
            repo=repo,
            commit_sha=commit_a,
            archive_bytes=exact_archive,
            temp_parent=temp_parent,
            provider_options={"max_file_bytes": 4},
        )
    )
    caught_error = _caught_exception(
        lambda: _crafted_archive_snapshot(
            module=module,
            monkeypatch=monkeypatch,
            repo=repo,
            commit_sha=commit_a,
            archive_bytes=one_over_archive,
            temp_parent=temp_parent,
            provider_options={"max_file_bytes": 4},
        )
    )

    assert exact_error is None
    assert isinstance(exact_snapshot, SourceSnapshot)
    assert exact_snapshot.archive_files == (
        ArchiveFile("docs/research/exact.md", exact_content),
    )
    assert type(caught_error) is module.GitArchiveSnapshotError
    assert "limit" in str(caught_error).lower()
    assert list(temp_parent.iterdir()) == []


def test_included_total_allows_exact_limit_and_rejects_one_over(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sum of included file bytes cannot exceed the snapshot byte budget."""
    module = importlib.import_module("omf_retrieval.infrastructure.source.git_archive")
    repo, commit_a, _ = _make_two_commit_repo(tmp_path)
    temp_parent = tmp_path / "provider-temporary"
    temp_parent.mkdir()
    exact_archive = _tar_bytes(
        [
            _regular_member("docs/research/a.md", b"12"),
            _regular_member("docs/research/b.md", b"34"),
        ]
    )
    one_over_archive = _tar_bytes(
        [
            _regular_member("docs/research/a.md", b"12"),
            _regular_member("docs/research/b.md", b"345"),
        ]
    )

    exact_snapshot, exact_error = _captured_result(
        lambda: _crafted_archive_snapshot(
            module=module,
            monkeypatch=monkeypatch,
            repo=repo,
            commit_sha=commit_a,
            archive_bytes=exact_archive,
            temp_parent=temp_parent,
            provider_options={"max_total_bytes": 4},
        )
    )
    caught_error = _caught_exception(
        lambda: _crafted_archive_snapshot(
            module=module,
            monkeypatch=monkeypatch,
            repo=repo,
            commit_sha=commit_a,
            archive_bytes=one_over_archive,
            temp_parent=temp_parent,
            provider_options={"max_total_bytes": 4},
        )
    )

    assert exact_error is None
    assert isinstance(exact_snapshot, SourceSnapshot)
    assert [file.content for file in exact_snapshot.archive_files] == [b"12", b"34"]
    assert type(caught_error) is module.GitArchiveSnapshotError
    assert "limit" in str(caught_error).lower()
    assert list(temp_parent.iterdir()) == []


def test_excluded_large_file_does_not_consume_included_byte_limits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only selected regular files consume per-file and included-total budgets."""
    module = importlib.import_module("omf_retrieval.infrastructure.source.git_archive")
    repo, commit_a, _ = _make_two_commit_repo(tmp_path)
    temp_parent = tmp_path / "provider-temporary"
    temp_parent.mkdir()
    archive_bytes = _tar_bytes([_regular_member("outside/large.bin", b"x" * 32)])

    snapshot, caught_error = _captured_result(
        lambda: _crafted_archive_snapshot(
            module=module,
            monkeypatch=monkeypatch,
            repo=repo,
            commit_sha=commit_a,
            archive_bytes=archive_bytes,
            temp_parent=temp_parent,
            provider_options={"max_file_bytes": 1, "max_total_bytes": 1},
        )
    )

    assert caught_error is None
    assert isinstance(snapshot, SourceSnapshot)
    assert snapshot.archive_files == ()
    assert list(temp_parent.iterdir()) == []


def test_raw_git_archive_stream_allows_exact_limit_and_rejects_one_over(
    tmp_path: Path,
) -> None:
    """The real Git stdout stream is bounded while it is copied to private storage."""
    module = importlib.import_module("omf_retrieval.infrastructure.source.git_archive")
    repo, commit_a, _ = _make_two_commit_repo(tmp_path)
    archive_size = len(
        subprocess.run(
            ["git", "archive", "--format=tar", commit_a],
            cwd=repo,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout
    )
    temp_parent = tmp_path / "provider-temporary"
    temp_parent.mkdir()

    exact_snapshot, exact_error = _captured_result(
        lambda: module.GitArchiveSnapshotProvider(
            _omf_test_profile(),
            temp_parent=temp_parent,
            max_archive_bytes=archive_size,
        ).snapshot(repo, commit_a)
    )
    caught_error = _caught_exception(
        lambda: module.GitArchiveSnapshotProvider(
            _omf_test_profile(),
            temp_parent=temp_parent,
            max_archive_bytes=archive_size - 1,
        ).snapshot(repo, commit_a)
    )

    assert exact_error is None
    assert isinstance(exact_snapshot, SourceSnapshot)
    assert exact_snapshot.commit_sha == commit_a
    assert type(caught_error) is module.GitArchiveSnapshotError
    assert "limit" in str(caught_error).lower()
    assert list(temp_parent.iterdir()) == []


def test_git_archive_process_failure_is_sanitized_and_cleans_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure after temp creation leaks neither subprocess details nor resources."""
    module = importlib.import_module("omf_retrieval.infrastructure.source.git_archive")
    repo, commit_a, _ = _make_two_commit_repo(tmp_path)
    temp_parent = tmp_path / "provider-temporary"
    temp_parent.mkdir()

    def failing_archive_writer(**_: Any) -> None:
        raise subprocess.CalledProcessError(
            128,
            ["git", "archive", "raw-secret-argument"],
            stderr=b"raw-secret-content",
        )

    monkeypatch.setattr(module, "_write_git_archive", failing_archive_writer)

    caught_error = _caught_exception(
        lambda: module.GitArchiveSnapshotProvider(
            _omf_test_profile(), temp_parent=temp_parent
        ).snapshot(repo, commit_a)
    )

    assert type(caught_error) is module.GitArchiveSnapshotError
    assert "raw-secret" not in str(caught_error)
    assert list(temp_parent.iterdir()) == []
