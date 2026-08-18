"""Read immutable source snapshots from Git tar archives."""

import os
import re
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import BinaryIO, Callable

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
_MAX_PAX_METADATA_BYTES = 4 * 1024 * 1024
_GIT_BATCH_HEADER_BYTES = 128


def _isolated_git_environment() -> dict[str, str]:
    return {
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "LC_ALL": "C",
        "PATH": os.environ.get("PATH", os.defpath),
    }


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
        git_environment = _isolated_git_environment()
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
                _verify_archive_provenance(
                    repo=repo,
                    commit_sha=resolved_commit,
                    archive_files=archive_files,
                    profile=self._profile,
                    git_environment=git_environment,
                    max_members=self._max_members,
                    max_file_bytes=self._max_file_bytes,
                    max_total_bytes=self._max_total_bytes,
                    max_metadata_bytes=self._max_archive_bytes,
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
        _validate_tar_structure(
            archive_path,
            max_members=self._max_members,
            max_metadata_bytes=min(
                _MAX_PAX_METADATA_BYTES,
                self._max_archive_bytes,
            ),
        )
        archive_files: list[ArchiveFile] = []
        seen_paths: set[str] = set()
        regular_paths: set[str] = set()
        included_bytes = 0
        with tarfile.open(archive_path, mode="r:") as archive:
            for member in archive:
                source_path = canonical_source_path(member.name)
                if source_path in seen_paths:
                    raise GitArchiveSnapshotError(
                        "Tar archive contains a duplicate path"
                    )
                if member.size < 0:
                    raise GitArchiveSnapshotError(
                        "Tar archive contains an invalid declared size"
                    )
                if member.issparse():
                    raise GitArchiveSnapshotError(
                        "Tar archive contains forbidden sparse metadata"
                    )
                is_regular = member.type in {tarfile.REGTYPE, tarfile.AREGTYPE}
                is_directory = member.type == tarfile.DIRTYPE
                if not is_regular and not is_directory:
                    raise GitArchiveSnapshotError(
                        "Tar archive contains a forbidden member type"
                    )
                if any(
                    ancestor in regular_paths
                    for ancestor in _source_path_ancestors(source_path)
                ) or (
                    is_regular
                    and any(
                        seen_path.startswith(f"{source_path}/")
                        for seen_path in seen_paths
                    )
                ):
                    raise GitArchiveSnapshotError(
                        "Tar archive contains a file tree conflict"
                    )
                seen_paths.add(source_path)
                if is_regular:
                    regular_paths.add(source_path)
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


def _source_path_ancestors(source_path: str) -> tuple[str, ...]:
    parts = source_path.split("/")
    return tuple("/".join(parts[:index]) for index in range(1, len(parts)))


def _write_git_archive(
    *,
    repo: Path,
    commit_sha: str,
    archive_output: BinaryIO,
    git_environment: dict[str, str],
    max_archive_bytes: int,
) -> None:
    process: subprocess.Popen[bytes] | None = None
    operation_succeeded = False
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
        operation_succeeded = True
    except (OSError, ValueError) as error:
        raise GitArchiveSnapshotError("Git archive creation failed") from error
    finally:
        if process is not None:
            cleanup_failed = _cleanup_git_process(process, (process.stdout,))
            if operation_succeeded and cleanup_failed:
                raise GitArchiveSnapshotError("Git archive cleanup failed")


def _stop_process(process: subprocess.Popen[bytes]) -> bool:
    cleanup_failed = False
    try:
        if process.poll() is not None:
            return False
    except OSError:
        cleanup_failed = True
    try:
        process.terminate()
    except OSError:
        cleanup_failed = True
    try:
        process.wait(timeout=1)
        return cleanup_failed
    except subprocess.TimeoutExpired:
        pass
    except OSError:
        cleanup_failed = True
    try:
        process.kill()
    except OSError:
        cleanup_failed = True
    try:
        process.wait()
    except (OSError, subprocess.TimeoutExpired):
        cleanup_failed = True
    return cleanup_failed


def _cleanup_git_process(
    process: subprocess.Popen[bytes],
    pipes: tuple[BinaryIO | None, ...],
) -> bool:
    cleanup_failed = False
    for pipe in pipes:
        if _close_git_pipe(pipe):
            cleanup_failed = True
    if _stop_process(process):
        cleanup_failed = True
    return cleanup_failed


def _verify_archive_provenance(
    *,
    repo: Path,
    commit_sha: str,
    archive_files: list[ArchiveFile],
    profile: SourceProfileConfig,
    git_environment: dict[str, str],
    max_members: int,
    max_file_bytes: int,
    max_total_bytes: int,
    max_metadata_bytes: int,
) -> None:
    expected_blobs = _read_selected_tree_blobs(
        repo=repo,
        commit_sha=commit_sha,
        profile=profile,
        git_environment=git_environment,
        max_members=max_members,
        max_metadata_bytes=max_metadata_bytes,
    )
    archive_by_path = {
        archive_file.source_path: archive_file.content for archive_file in archive_files
    }
    if set(archive_by_path) != set(expected_blobs):
        raise GitArchiveSnapshotError("Git archive provenance path mismatch")

    ordered_paths = sorted(expected_blobs)
    unique_object_ids = tuple(
        dict.fromkeys(expected_blobs[source_path] for source_path in ordered_paths)
    )
    blob_cache = _read_git_blobs_batch(
        repo=repo,
        object_ids=unique_object_ids,
        git_environment=git_environment,
        max_file_bytes=max_file_bytes,
        max_total_bytes=max_total_bytes,
    )
    total_bytes = 0
    for source_path in ordered_paths:
        object_id = expected_blobs[source_path]
        content = blob_cache[object_id]
        total_bytes += len(content)
        if total_bytes > max_total_bytes:
            raise GitArchiveSnapshotError("Included total byte limit exceeded")
        if archive_by_path[source_path] != content:
            raise GitArchiveSnapshotError("Git archive provenance content mismatch")


def _read_selected_tree_blobs(
    *,
    repo: Path,
    commit_sha: str,
    profile: SourceProfileConfig,
    git_environment: dict[str, str],
    max_members: int,
    max_metadata_bytes: int,
) -> dict[str, str]:
    selected_blobs: dict[str, str] = {}

    def consume_tree_record(record: bytes) -> None:
        metadata, separator, raw_path = record.partition(b"\t")
        fields = metadata.split()
        if not separator or len(fields) != 3:
            raise GitArchiveSnapshotError("Git tree provenance is malformed")
        object_type = fields[1]
        try:
            object_id = fields[2].decode("ascii")
            source_path = raw_path.decode("utf-8", "surrogateescape")
        except UnicodeError as error:
            raise GitArchiveSnapshotError("Git tree provenance is malformed") from error
        if not profile.includes(source_path):
            return
        if object_type != b"blob":
            raise GitArchiveSnapshotError("Git tree provenance type mismatch")
        selected_blobs[source_path] = object_id
        if len(selected_blobs) > max_members:
            raise GitArchiveSnapshotError("Tar archive member limit exceeded")

    _stream_git_nul_records(
        argv=["git", "ls-tree", "-r", "-z", "--full-tree", commit_sha],
        repo=repo,
        git_environment=git_environment,
        max_output_bytes=max_metadata_bytes,
        consume_record=consume_tree_record,
    )
    return selected_blobs


def _stream_git_nul_records(
    *,
    argv: list[str],
    repo: Path,
    git_environment: dict[str, str],
    max_output_bytes: int,
    consume_record: Callable[[bytes], None],
) -> None:
    process: subprocess.Popen[bytes] | None = None
    operation_succeeded = False
    try:
        process = subprocess.Popen(
            argv,
            cwd=repo,
            env=git_environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=False,
        )
        if process.stdout is None:
            raise GitArchiveSnapshotError("Git provenance stream is unavailable")
        pending = bytearray()
        output_bytes = 0
        while chunk := process.stdout.read(_ARCHIVE_CHUNK_BYTES):
            output_bytes += len(chunk)
            if output_bytes > max_output_bytes:
                raise GitArchiveSnapshotError("Git provenance metadata limit exceeded")
            pending.extend(chunk)
            while (separator := pending.find(0)) >= 0:
                consume_record(bytes(pending[:separator]))
                del pending[: separator + 1]
        if pending:
            raise GitArchiveSnapshotError("Git tree provenance is malformed")
        if process.wait() != 0:
            raise GitArchiveSnapshotError("Git provenance command failed")
        operation_succeeded = True
    except (OSError, ValueError) as error:
        raise GitArchiveSnapshotError("Git provenance command failed") from error
    finally:
        if process is not None:
            cleanup_failed = _cleanup_git_process(process, (process.stdout,))
            if operation_succeeded and cleanup_failed:
                raise GitArchiveSnapshotError("Git provenance cleanup failed")


def _read_git_blobs_batch(
    *,
    repo: Path,
    object_ids: tuple[str, ...],
    git_environment: dict[str, str],
    max_file_bytes: int,
    max_total_bytes: int,
) -> dict[str, bytes]:
    if not object_ids:
        return {}
    process: subprocess.Popen[bytes] | None = None
    operation_succeeded = False
    try:
        process = subprocess.Popen(
            ["git", "cat-file", "--batch"],
            cwd=repo,
            env=git_environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=False,
        )
        if process.stdin is None or process.stdout is None:
            raise GitArchiveSnapshotError("Git provenance stream is unavailable")
        blobs: dict[str, bytes] = {}
        total_bytes = 0
        for object_id in object_ids:
            request = f"{object_id}\n".encode("ascii")
            if process.stdin.write(request) != len(request):
                raise GitArchiveSnapshotError("Git provenance request failed")
            process.stdin.flush()
            header = _read_git_batch_line(process.stdout)
            fields = header.removesuffix(b"\n").split(b" ")
            if (
                len(fields) != 3
                or fields[0] != object_id.encode("ascii")
                or fields[1] != b"blob"
                or not fields[2].isdigit()
            ):
                raise GitArchiveSnapshotError("Git provenance response is malformed")
            blob_size = int(fields[2])
            if blob_size > max_file_bytes:
                raise GitArchiveSnapshotError("Included file byte limit exceeded")
            total_bytes += blob_size
            if total_bytes > max_total_bytes:
                raise GitArchiveSnapshotError("Included total byte limit exceeded")
            content = _read_git_exact(process.stdout, blob_size)
            if process.stdout.read(1) != b"\n":
                raise GitArchiveSnapshotError("Git provenance response is malformed")
            blobs[object_id] = content
        process.stdin.close()
        if process.stdout.read(1):
            raise GitArchiveSnapshotError("Git provenance response is malformed")
        if process.wait() != 0:
            raise GitArchiveSnapshotError("Git provenance command failed")
        operation_succeeded = True
        return blobs
    except (OSError, ValueError) as error:
        raise GitArchiveSnapshotError("Git provenance command failed") from error
    finally:
        if process is not None:
            cleanup_failed = _cleanup_git_process(
                process,
                (process.stdin, process.stdout),
            )
            if operation_succeeded and cleanup_failed:
                raise GitArchiveSnapshotError("Git provenance cleanup failed")


def _read_git_batch_line(batch_output: BinaryIO) -> bytes:
    line = bytearray()
    while len(line) <= _GIT_BATCH_HEADER_BYTES:
        character = batch_output.read(1)
        if len(character) != 1:
            raise GitArchiveSnapshotError("Git provenance response is malformed")
        line.extend(character)
        if character == b"\n":
            return bytes(line)
    raise GitArchiveSnapshotError("Git provenance response is malformed")


def _close_git_pipe(pipe: BinaryIO | None) -> bool:
    if pipe is None:
        return False
    try:
        pipe.close()
    except (OSError, ValueError):
        return True
    return False


def _read_git_exact(batch_output: BinaryIO, size: int) -> bytes:
    content = bytearray()
    remaining = size
    while remaining:
        chunk = batch_output.read(min(remaining, _ARCHIVE_CHUNK_BYTES))
        if not chunk:
            raise GitArchiveSnapshotError("Git provenance response is incomplete")
        content.extend(chunk)
        remaining -= len(chunk)
    return bytes(content)


def _validate_tar_structure(
    archive_path: Path,
    *,
    max_members: int,
    max_metadata_bytes: int,
) -> None:
    archive_size = archive_path.stat().st_size
    with archive_path.open("rb") as archive_input:
        offset = 0
        raw_member_count = 0
        pending_local_metadata = False
        while True:
            header = archive_input.read(_TAR_BLOCK_BYTES)
            if len(header) != _TAR_BLOCK_BYTES:
                raise GitArchiveSnapshotError("Tar archive is missing its terminator")
            offset += _TAR_BLOCK_BYTES

            if header == bytes(_TAR_BLOCK_BYTES):
                if pending_local_metadata:
                    raise GitArchiveSnapshotError(
                        "Tar archive contains orphaned metadata"
                    )
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

            _validate_tar_checksum(header)
            raw_member_count += 1
            if raw_member_count > max_members:
                raise GitArchiveSnapshotError("Tar archive member limit exceeded")
            member_type = header[156:157]
            if member_type == tarfile.GNUTYPE_LONGLINK:
                raise GitArchiveSnapshotError(
                    "Tar archive contains a forbidden GNU long link"
                )
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

            if member_type == tarfile.SOLARIS_XHDTYPE:
                raise GitArchiveSnapshotError(
                    "Tar archive contains an unsupported extended header"
                )
            is_local_metadata = member_type in {
                tarfile.XHDTYPE,
                tarfile.GNUTYPE_LONGNAME,
            }
            if pending_local_metadata and member_type in {
                tarfile.XHDTYPE,
                tarfile.XGLTYPE,
                tarfile.GNUTYPE_LONGNAME,
            }:
                raise GitArchiveSnapshotError(
                    "Tar archive metadata sequence is invalid"
                )
            if member_type in {
                tarfile.XHDTYPE,
                tarfile.XGLTYPE,
                tarfile.GNUTYPE_LONGNAME,
            }:
                if member_size > max_metadata_bytes:
                    raise GitArchiveSnapshotError("Tar archive metadata limit exceeded")
                if member_type in {tarfile.XHDTYPE, tarfile.XGLTYPE}:
                    _validate_pax_stream(archive_input, member_size)
                else:
                    _read_and_discard(archive_input, member_size)
                _read_and_discard(archive_input, padded_size - member_size)
                pending_local_metadata = is_local_metadata
            else:
                archive_input.seek(padded_size, os.SEEK_CUR)
                pending_local_metadata = False
            offset += padded_size


def _validate_tar_checksum(header: bytes) -> None:
    raw_checksum = header[148:156].strip(b"\x00 ")
    try:
        stored_checksum = int(raw_checksum, 8)
    except ValueError as error:
        raise GitArchiveSnapshotError("Tar archive checksum is invalid") from error
    checksum_header = header[:148] + b" " * 8 + header[156:]
    unsigned_checksum = sum(checksum_header)
    signed_checksum = sum(
        byte if byte < 128 else byte - 256 for byte in checksum_header
    )
    if stored_checksum not in {unsigned_checksum, signed_checksum}:
        raise GitArchiveSnapshotError("Tar archive checksum is invalid")


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


def _validate_pax_stream(archive_input: BinaryIO, payload_size: int) -> None:
    remaining = payload_size
    while remaining:
        raw_record_size = bytearray()
        while True:
            character = archive_input.read(1)
            if len(character) != 1:
                raise GitArchiveSnapshotError("Tar archive member data is truncated")
            remaining -= 1
            if character == b" ":
                break
            if not character.isdigit() or len(raw_record_size) >= 20 or remaining <= 0:
                raise GitArchiveSnapshotError("Tar PAX record is malformed")
            raw_record_size.extend(character)
        if not raw_record_size:
            raise GitArchiveSnapshotError("Tar PAX record is malformed")
        try:
            record_size = int(raw_record_size)
        except ValueError as error:
            raise GitArchiveSnapshotError("Tar PAX record is malformed") from error
        prefix_size = len(raw_record_size) + 1
        body_size = record_size - prefix_size
        if body_size <= 0 or body_size > remaining:
            raise GitArchiveSnapshotError("Tar PAX record is malformed")

        record = _read_exact_chunked(archive_input, body_size)
        remaining -= body_size
        if not record.endswith(b"\n") or b"=" not in record:
            raise GitArchiveSnapshotError("Tar PAX record is malformed")
        key, _ = record[:-1].split(b"=", 1)
        if key.startswith(b"GNU.sparse."):
            raise GitArchiveSnapshotError(
                "Tar archive contains forbidden sparse metadata"
            )
        if key == b"size":
            raise GitArchiveSnapshotError("Tar PAX size override is forbidden")


def _read_exact_chunked(archive_input: BinaryIO, size: int) -> bytes:
    content = bytearray()
    remaining = size
    while remaining:
        chunk = archive_input.read(min(remaining, _ARCHIVE_CHUNK_BYTES))
        if not chunk:
            raise GitArchiveSnapshotError("Tar archive member data is truncated")
        content.extend(chunk)
        remaining -= len(chunk)
    return bytes(content)


def _read_and_discard(archive_input: BinaryIO, size: int) -> None:
    remaining = size
    while remaining:
        chunk = archive_input.read(min(remaining, _ARCHIVE_CHUNK_BYTES))
        if not chunk:
            raise GitArchiveSnapshotError("Tar archive member data is truncated")
        remaining -= len(chunk)
