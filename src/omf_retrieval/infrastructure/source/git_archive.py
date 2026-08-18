"""Read immutable source snapshots from Git tar archives."""

import os
import re
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import BinaryIO

from omf_retrieval.application.indexing.ports import ArchiveFile, SourceSnapshot
from omf_retrieval.infrastructure.source.profiles import (
    SourceProfileConfig,
    SourceProfileValidationError,
    canonical_source_path,
)

DEFAULT_MAX_MEMBERS = 20_000
DEFAULT_MAX_FILE_BYTES = 4 * 1024 * 1024
DEFAULT_MAX_TOTAL_BYTES = 128 * 1024 * 1024
DEFAULT_MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
_ARCHIVE_CHUNK_BYTES = 64 * 1024
_TAR_BLOCK_BYTES = 512


class GitArchiveSnapshotError(RuntimeError):
    """Raised when a repository or archive cannot produce a safe snapshot."""


class GitArchiveSnapshotProvider:
    """Create profile-filtered snapshots from an explicitly selected Git commit."""

    def __init__(
        self,
        profile: SourceProfileConfig,
        *,
        temp_parent: Path | None = None,
        max_members: int = DEFAULT_MAX_MEMBERS,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
        max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
        max_archive_bytes: int = DEFAULT_MAX_ARCHIVE_BYTES,
    ) -> None:
        """Bind the provider to an immutable source-selection profile."""
        limits = (
            max_members,
            max_file_bytes,
            max_total_bytes,
            max_archive_bytes,
        )
        if any(type(limit) is not int or limit <= 0 for limit in limits):
            raise GitArchiveSnapshotError(
                "Resource limits must be positive exact integers"
            )
        self._profile = profile
        self._temp_parent = temp_parent
        self._max_members = max_members
        self._max_file_bytes = max_file_bytes
        self._max_total_bytes = max_total_bytes
        self._max_archive_bytes = max_archive_bytes

    def snapshot(self, repo: Path, commit_sha: str) -> SourceSnapshot:
        """Return original bytes selected from one Git commit archive."""
        git_environment = os.environ.copy()
        git_environment["GIT_OPTIONAL_LOCKS"] = "0"
        try:
            status = subprocess.run(
                [
                    "git",
                    "status",
                    "--porcelain=v1",
                    "--untracked-files=all",
                    "--ignore-submodules=none",
                ],
                cwd=repo,
                env=git_environment,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                shell=False,
            ).stdout
        except (OSError, subprocess.CalledProcessError) as error:
            raise GitArchiveSnapshotError("Git repository validation failed") from error
        if status:
            raise GitArchiveSnapshotError("Git worktree must be clean")

        if (
            type(commit_sha) is not str
            or re.fullmatch(r"[0-9a-f]{40}", commit_sha) is None
        ):
            raise GitArchiveSnapshotError("Git commit identifier is invalid")

        try:
            resolved_commit = subprocess.run(
                [
                    "git",
                    "rev-parse",
                    "--verify",
                    "--end-of-options",
                    f"{commit_sha}^{{commit}}",
                ],
                cwd=repo,
                env=git_environment,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                shell=False,
                text=True,
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError) as error:
            raise GitArchiveSnapshotError("Git commit validation failed") from error

        if self._temp_parent is not None:
            try:
                self._temp_parent.resolve().relative_to(repo.resolve())
            except ValueError:
                pass
            else:
                raise GitArchiveSnapshotError(
                    "Temporary storage must be outside the source repository"
                )

        try:
            with tempfile.TemporaryDirectory(
                dir=self._temp_parent
            ) as temporary_directory:
                temporary_root = Path(temporary_directory)
                temporary_root.chmod(0o700)
                extraction_root = temporary_root / "extracted"
                extraction_root.mkdir(mode=0o700)
                archive_path = temporary_root / "snapshot.tar"
                with archive_path.open("xb") as archive_output:
                    _write_git_archive(
                        repo=repo,
                        commit_sha=resolved_commit,
                        archive_output=archive_output,
                        git_environment=git_environment,
                        max_archive_bytes=self._max_archive_bytes,
                    )
                archive_files = self._read_archive(
                    archive_path=archive_path,
                    extraction_root=extraction_root,
                )
        except GitArchiveSnapshotError:
            raise
        except subprocess.CalledProcessError as error:
            raise GitArchiveSnapshotError("Git archive creation failed") from error
        except (
            EOFError,
            OSError,
            tarfile.TarError,
            SourceProfileValidationError,
        ) as error:
            raise GitArchiveSnapshotError("Tar archive validation failed") from error

        return SourceSnapshot(
            commit_sha=resolved_commit,
            archive_files=tuple(
                sorted(archive_files, key=lambda item: item.source_path)
            ),
        )

    def _read_archive(
        self,
        *,
        archive_path: Path,
        extraction_root: Path,
    ) -> list[ArchiveFile]:
        _validate_tar_structure(archive_path)
        archive_files: list[ArchiveFile] = []
        seen_paths: set[str] = set()
        member_count = 0
        included_bytes = 0
        with tarfile.open(archive_path, mode="r:") as archive:
            for member in archive:
                member_count += 1
                if member_count > self._max_members:
                    raise GitArchiveSnapshotError("Tar archive member limit exceeded")
                source_path = canonical_source_path(member.name)
                if source_path in seen_paths:
                    raise GitArchiveSnapshotError(
                        "Tar archive contains a duplicate path"
                    )
                seen_paths.add(source_path)
                if member.size < 0:
                    raise GitArchiveSnapshotError(
                        "Tar archive contains an invalid declared size"
                    )
                is_regular = member.type in {tarfile.REGTYPE, tarfile.AREGTYPE}
                is_directory = member.type == tarfile.DIRTYPE
                if not is_regular and not is_directory:
                    raise GitArchiveSnapshotError(
                        "Tar archive contains a forbidden member type"
                    )
                if is_directory or not self._profile.includes(source_path):
                    continue
                if member.size > self._max_file_bytes:
                    raise GitArchiveSnapshotError("Included file byte limit exceeded")
                included_bytes += member.size
                if included_bytes > self._max_total_bytes:
                    raise GitArchiveSnapshotError("Included total byte limit exceeded")
                extracted_file = archive.extractfile(member)
                if extracted_file is None:
                    raise GitArchiveSnapshotError(
                        "Tar archive file content is unavailable"
                    )
                content = extracted_file.read()
                if len(content) != member.size:
                    raise GitArchiveSnapshotError(
                        "Tar archive file content is incomplete"
                    )
                destination = extraction_root.joinpath(*source_path.split("/"))
                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with destination.open("xb") as extracted_output:
                    extracted_output.write(content)
                archive_files.append(
                    ArchiveFile(
                        source_path=source_path,
                        content=destination.read_bytes(),
                    )
                )
        return archive_files


def _write_git_archive(
    *,
    repo: Path,
    commit_sha: str,
    archive_output: BinaryIO,
    git_environment: dict[str, str],
    max_archive_bytes: int,
) -> None:
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            ["git", "archive", "--format=tar", commit_sha],
            cwd=repo,
            env=git_environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=False,
        )
        if process.stdout is None:
            raise GitArchiveSnapshotError("Git archive stream is unavailable")

        archive_bytes = 0
        while chunk := process.stdout.read(_ARCHIVE_CHUNK_BYTES):
            archive_bytes += len(chunk)
            if archive_bytes > max_archive_bytes:
                _stop_process(process)
                raise GitArchiveSnapshotError("Raw tar stream limit exceeded")
            archive_output.write(chunk)

        return_code = process.wait()
        if return_code != 0:
            raise GitArchiveSnapshotError("Git archive creation failed")
    except OSError as error:
        raise GitArchiveSnapshotError("Git archive creation failed") from error
    finally:
        if process is not None:
            if process.stdout is not None:
                process.stdout.close()
            _stop_process(process)


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _validate_tar_structure(archive_path: Path) -> None:
    archive_size = archive_path.stat().st_size
    with archive_path.open("rb") as archive_input:
        offset = 0
        while True:
            header = archive_input.read(_TAR_BLOCK_BYTES)
            if len(header) != _TAR_BLOCK_BYTES:
                raise GitArchiveSnapshotError("Tar archive is missing its terminator")
            offset += _TAR_BLOCK_BYTES

            if header == bytes(_TAR_BLOCK_BYTES):
                second_terminator = archive_input.read(_TAR_BLOCK_BYTES)
                if second_terminator != bytes(_TAR_BLOCK_BYTES):
                    raise GitArchiveSnapshotError(
                        "Tar archive has an invalid terminator"
                    )
                while trailing_bytes := archive_input.read(_ARCHIVE_CHUNK_BYTES):
                    if trailing_bytes.strip(b"\x00"):
                        raise GitArchiveSnapshotError(
                            "Tar archive has data after its terminator"
                        )
                return

            member_size = _parse_tar_size(header[124:136])
            if member_size < 0:
                raise GitArchiveSnapshotError(
                    "Tar archive contains an invalid declared size"
                )
            padded_size = (
                (member_size + _TAR_BLOCK_BYTES - 1) // _TAR_BLOCK_BYTES
            ) * _TAR_BLOCK_BYTES
            if offset + padded_size > archive_size:
                raise GitArchiveSnapshotError("Tar archive member data is truncated")

            if header[156:157] in {tarfile.XHDTYPE, tarfile.XGLTYPE}:
                pax_payload = archive_input.read(member_size)
                _validate_pax_payload(pax_payload)
                archive_input.seek(padded_size - member_size, os.SEEK_CUR)
            else:
                archive_input.seek(padded_size, os.SEEK_CUR)
            offset += padded_size


def _parse_tar_size(raw_size: bytes) -> int:
    if raw_size[0] == 0x80:
        return int.from_bytes(b"\x00" + raw_size[1:], byteorder="big")
    if raw_size[0] == 0xFF:
        return -1
    if raw_size[0] & 0x80:
        raise GitArchiveSnapshotError("Tar archive size encoding is invalid")

    octal_size = raw_size.strip(b"\x00 ")
    if not octal_size:
        return 0
    try:
        return int(octal_size, 8)
    except ValueError as error:
        raise GitArchiveSnapshotError("Tar archive size encoding is invalid") from error


def _validate_pax_payload(payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        separator = payload.find(b" ", offset)
        if separator < 0:
            raise GitArchiveSnapshotError("Tar PAX record is malformed")
        try:
            record_size = int(payload[offset:separator])
        except ValueError as error:
            raise GitArchiveSnapshotError("Tar PAX record is malformed") from error
        if record_size <= 0 or offset + record_size > len(payload):
            raise GitArchiveSnapshotError("Tar PAX record is malformed")

        record = payload[separator + 1 : offset + record_size]
        if not record.endswith(b"\n") or b"=" not in record:
            raise GitArchiveSnapshotError("Tar PAX record is malformed")
        key, value = record[:-1].split(b"=", 1)
        if key == b"size":
            try:
                pax_size = int(value)
            except ValueError as error:
                raise GitArchiveSnapshotError("Tar PAX size is invalid") from error
            if pax_size < 0:
                raise GitArchiveSnapshotError(
                    "Tar archive contains an invalid declared size"
                )
        offset += record_size
