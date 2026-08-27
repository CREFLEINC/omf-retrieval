"""Unit tests for incremental indexing orchestration without PostgreSQL."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from uuid import UUID

import pytest

from omf_retrieval.application.indexing.ports import (
    ArchiveFile,
    ChunkDraft,
    ParsedBlock,
    ParsedMarkdown,
    ParsedSection,
    SourceSnapshot,
)
from omf_retrieval.application.indexing.service import (
    IndexResult,
    IndexService,
    ParseArtifacts,
    StoredChunk,
)
from omf_retrieval.domain.enums import IndexRunStatus
from omf_retrieval.domain.models import DocumentMetadata
from omf_retrieval.infrastructure.database.repository_errors import (
    RepositoryInvariantError,
)


class _FakeParser:
    def __init__(
        self,
        failure: Exception | None = None,
        *,
        parser_version: str = "parser-v1",
    ) -> None:
        self.calls = 0
        self.failure = failure
        self.parser_version = parser_version

    def parse(self, source: str) -> ParsedMarkdown:
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        block = ParsedBlock(
            kind="paragraph",
            raw_text=source,
            line_start=1,
            line_end=1,
            children=(),
        )
        section = ParsedSection(
            ordinal=0,
            parent_ordinal=None,
            level=0,
            heading=None,
            heading_path=(),
            body=source,
            line_start=1,
            line_end=1,
            blocks=(block,),
        )
        return ParsedMarkdown(
            parser_version=self.parser_version,
            sections=(section,),
        )


class _FakeChunker:
    def __init__(self, fixed_chunk_hash: str | None = None) -> None:
        self.fixed_chunk_hash = fixed_chunk_hash

    def split(
        self,
        section: ParsedSection,
        *,
        parser_version: str,
    ) -> tuple[ChunkDraft, ...]:
        assert parser_version == "parser-v1"
        digest = (
            self.fixed_chunk_hash
            or hashlib.sha256(section.body.encode("utf-8")).hexdigest()
        )
        return (
            ChunkDraft(
                ordinal=0,
                raw_text=section.body,
                search_text=section.body,
                token_count=len(section.body),
                line_start=1,
                line_end=1,
                chunk_hash=digest,
            ),
        )


class _FakeEmbeddings:
    def __init__(
        self,
        failure: Exception | None = None,
        *,
        fixed_batch: tuple[tuple[float, ...], ...] | None = None,
    ) -> None:
        self.document_calls = 0
        self.failure = failure
        self.fixed_batch = fixed_batch

    def embed_documents(
        self,
        documents: Sequence[str],
    ) -> tuple[tuple[float, ...], ...]:
        self.document_calls += 1
        if self.failure is not None:
            raise self.failure
        if self.fixed_batch is not None:
            return self.fixed_batch
        return tuple((float(len(document)), 1.0) for document in documents)


class _FakeRepository:
    def __init__(
        self,
        *,
        vector_dimension: int | None = None,
        save_embedding_error: Exception | None = None,
    ) -> None:
        self.contents: dict[str, tuple[UUID, str]] = {}
        self.occurrences: list[tuple[UUID, UUID, str, DocumentMetadata]] = []
        self.parses: dict[tuple[UUID, str, str], ParseArtifacts] = {}
        self.chunk_hash_by_id: dict[UUID, str] = {}
        self.reusable_vectors: dict[tuple[str, str], tuple[float, ...]] = {}
        self.embedding_links: dict[tuple[UUID, str], tuple[float, ...]] = {}
        self.building_runs: list[UUID] = []
        self.ready_results: list[IndexResult] = []
        self.failed_results: list[IndexResult] = []
        self.vector_dimension = vector_dimension
        self.save_embedding_error = save_embedding_error

    def create_building_run(self, commit_sha: str) -> UUID:
        assert len(commit_sha) == 40
        run_id = UUID(int=len(self.building_runs) + 1)
        self.building_runs.append(run_id)
        return run_id

    def upsert_content(
        self,
        content_digest: str,
        source: str,
    ) -> UUID:
        stored = self.contents.setdefault(
            content_digest,
            (UUID(int=len(self.contents) + 2), source),
        )
        return stored[0]

    def create_occurrence(
        self,
        run_id: UUID,
        content_id: UUID,
        source_path: str,
        metadata: DocumentMetadata,
    ) -> None:
        self.occurrences.append((run_id, content_id, source_path, metadata))

    def find_parse(
        self,
        content_id: UUID,
        parser_version: str,
        chunk_config_hash: str,
    ) -> ParseArtifacts | None:
        return self.parses.get((content_id, parser_version, chunk_config_hash))

    def save_parse(
        self,
        content_id: UUID,
        parser_version: str,
        chunk_config_hash: str,
        parsed: ParsedMarkdown,
        chunks: tuple[ChunkDraft, ...],
    ) -> ParseArtifacts:
        stored_chunks = tuple(
            StoredChunk(
                chunk_id=UUID(int=100 + len(self.chunk_hash_by_id) + index),
                draft=draft,
            )
            for index, draft in enumerate(chunks, start=1)
        )
        for stored_chunk in stored_chunks:
            self.chunk_hash_by_id[stored_chunk.chunk_id] = stored_chunk.draft.chunk_hash
        artifacts = ParseArtifacts(
            parser_version=parser_version,
            chunk_config_hash=chunk_config_hash,
            parsed=parsed,
            chunks=stored_chunks,
        )
        self.parses[(content_id, parser_version, chunk_config_hash)] = artifacts
        return artifacts

    def find_embedding(
        self,
        chunk_id: UUID,
        embedding_config_hash: str,
    ) -> tuple[float, ...] | None:
        return self.embedding_links.get((chunk_id, embedding_config_hash))

    def find_reusable_embedding(
        self,
        chunk_hash: str,
        embedding_config_hash: str,
    ) -> tuple[float, ...] | None:
        return self.reusable_vectors.get((chunk_hash, embedding_config_hash))

    def save_embedding(
        self,
        chunk_id: UUID,
        embedding_config_hash: str,
        vector: tuple[float, ...],
    ) -> None:
        if self.save_embedding_error is not None:
            raise self.save_embedding_error
        if self.vector_dimension is not None and len(vector) != self.vector_dimension:
            raise ValueError("embedding dimension mismatch")
        self.embedding_links[(chunk_id, embedding_config_hash)] = vector
        chunk_hash = self.chunk_hash_by_id[chunk_id]
        self.reusable_vectors[(chunk_hash, embedding_config_hash)] = vector

    def mark_ready(self, run_id: UUID, result: IndexResult) -> None:
        assert run_id in self.building_runs
        assert result.run_id == run_id
        self.ready_results.append(result)

    def mark_failed(self, run_id: UUID, result: IndexResult) -> None:
        assert run_id in self.building_runs
        assert result.run_id == run_id
        self.failed_results.append(result)


def _snapshot(
    *files: tuple[str, bytes],
    excluded_file_count: int = 0,
) -> SourceSnapshot:
    return SourceSnapshot(
        commit_sha="a" * 40,
        archive_files=tuple(
            ArchiveFile(source_path=source_path, content=content)
            for source_path, content in sorted(files)
        ),
        excluded_file_count=excluded_file_count,
    )


def _service(
    repository: _FakeRepository,
    parser: _FakeParser,
    embeddings: _FakeEmbeddings,
    *,
    chunker: _FakeChunker | None = None,
    embedding_dimension: int = 2,
) -> IndexService:
    return IndexService(
        repository=repository,
        parser=parser,
        chunker=chunker or _FakeChunker(),
        embeddings=embeddings,
        parser_version="parser-v1",
        chunk_config_hash="b" * 64,
        embedding_config_hash="c" * 64,
        embedding_dimension=embedding_dimension,
    )


def _index_without_leaking(
    index_service: IndexService,
    snapshot: SourceSnapshot,
) -> IndexResult:
    try:
        return index_service.index(snapshot)
    except Exception:
        raise AssertionError(
            "IndexService leaked a document-processing exception"
        ) from None


def test_duplicate_content_is_parsed_and_embedded_once() -> None:
    """Removing content-addressed reuse repeats expensive document work."""
    repository = _FakeRepository()
    parser = _FakeParser()
    embeddings = _FakeEmbeddings()
    index_service = _service(repository, parser, embeddings)

    result = index_service.index(
        _snapshot(
            ("docs/research/a.md", b"same source\n"),
            ("docs/research/b.md", b"same source\n"),
        )
    )

    assert result.occurrence_count == 2
    assert result.unique_content_count == 1
    assert result.run_id == UUID(int=1)
    assert parser.calls == 1
    assert embeddings.document_calls == 1
    assert len(repository.embedding_links) == 1
    assert len(repository.occurrences) == 2
    assert len(repository.ready_results) == 1
    assert repository.failed_results == []


def test_excluded_files_are_reported_without_becoming_occurrences() -> None:
    """Profile-rejected files remain a safe count rather than index input."""
    repository = _FakeRepository()
    parser = _FakeParser()
    embeddings = _FakeEmbeddings()

    result = _service(repository, parser, embeddings).index(
        _snapshot(
            ("docs/research/a.md", b"source\n"),
            excluded_file_count=4,
        )
    )

    assert result.status is IndexRunStatus.READY
    assert result.excluded_file_count == 4
    assert result.occurrence_count == 1
    assert len(repository.occurrences) == 1
    assert repository.ready_results == [result]


def test_distinct_documents_each_create_one_occurrence_and_artifact_set() -> None:
    """Treating distinct hashes as one would lose one document's artifacts."""
    repository = _FakeRepository()
    parser = _FakeParser()
    embeddings = _FakeEmbeddings()

    result = _service(repository, parser, embeddings).index(
        _snapshot(
            ("docs/research/a.md", b"first source\n"),
            ("uiux/b.md", b"second source\n"),
        )
    )

    assert result.status is IndexRunStatus.READY
    assert result.occurrence_count == 2
    assert result.unique_content_count == 2
    assert parser.calls == 2
    assert embeddings.document_calls == 2
    assert len(repository.contents) == 2
    assert len(repository.occurrences) == 2


def test_empty_document_records_occurrence_without_artifacts() -> None:
    """Parsing an empty document would manufacture non-source artifacts."""
    repository = _FakeRepository()
    parser = _FakeParser()
    embeddings = _FakeEmbeddings()

    result = _index_without_leaking(
        _service(repository, parser, embeddings),
        _snapshot(("docs/research/empty.md", b"")),
    )

    assert result.status is IndexRunStatus.READY
    assert result.occurrence_count == 1
    assert result.unique_content_count == 1
    assert result.empty_document_count == 1
    assert len(repository.contents) == 1
    assert len(repository.occurrences) == 1
    assert repository.parses == {}
    assert repository.embedding_links == {}
    assert parser.calls == 0
    assert embeddings.document_calls == 0


def test_whitespace_only_document_records_occurrence_without_artifacts() -> None:
    """Whitespace-only Markdown has no source-backed parse or chunk artifact."""
    repository = _FakeRepository()
    parser = _FakeParser()
    embeddings = _FakeEmbeddings()

    result = _service(repository, parser, embeddings).index(
        _snapshot(("docs/research/blank.md", b" \t\n\r\n"))
    )

    assert result.status is IndexRunStatus.READY
    assert result.empty_document_count == 1
    assert len(repository.occurrences) == 1
    assert repository.parses == {}
    assert repository.embedding_links == {}
    assert parser.calls == 0
    assert embeddings.document_calls == 0


def test_non_utf8_document_fails_run_with_decode_counter() -> None:
    """Silently replacing invalid bytes would index content unlike the source."""
    repository = _FakeRepository()
    parser = _FakeParser()
    embeddings = _FakeEmbeddings()

    result = _index_without_leaking(
        _service(repository, parser, embeddings),
        _snapshot(("docs/research/invalid.md", b"\xffprivate")),
    )

    assert result.status is IndexRunStatus.FAILED
    assert result.failure_code == "decode_failure"
    assert result.decode_failure_count == 1
    assert result.failure_detail == "A source document is not valid UTF-8."
    assert repository.ready_results == []
    assert repository.failed_results == [result]
    assert repository.embedding_links == {}
    assert repository.contents == {}
    assert repository.occurrences == []
    assert parser.calls == 0
    assert embeddings.document_calls == 0


def test_parser_exception_fails_run_without_source_or_exception_detail() -> None:
    """Parser exceptions must not leak indexed text or local host coordinates."""
    source = "private source body\n"
    secret_token = "token=parser-secret"
    host_path = "/Users/private/omf/document.md"
    repository = _FakeRepository()
    parser = _FakeParser(RuntimeError(f"{source} {secret_token} {host_path}"))
    embeddings = _FakeEmbeddings()

    result = _index_without_leaking(
        _service(repository, parser, embeddings),
        _snapshot(("docs/research/private.md", source.encode("utf-8"))),
    )

    assert result.status is IndexRunStatus.FAILED
    assert result.failure_code == "parse_failure"
    assert result.parse_failure_count == 1
    assert result.failure_detail == "A source document could not be parsed."
    assert result.occurrence_count == 1
    assert result.unique_content_count == 1
    assert source.strip() not in result.failure_detail
    assert secret_token not in result.failure_detail
    assert host_path not in result.failure_detail
    assert repository.ready_results == []
    assert repository.failed_results == [result]
    assert embeddings.document_calls == 0


def test_embedding_exception_fails_run_without_source_or_exception_detail() -> None:
    """Embedding exceptions must not persist source, tokens, or host paths."""
    source = "confidential retrieval text\n"
    secret_token = "token=embedding-secret"
    host_path = "/srv/private/model-cache"
    repository = _FakeRepository()
    parser = _FakeParser()
    embeddings = _FakeEmbeddings(RuntimeError(f"{source} {secret_token} {host_path}"))

    result = _index_without_leaking(
        _service(repository, parser, embeddings),
        _snapshot(("uiux/private.md", source.encode("utf-8"))),
    )

    assert result.status is IndexRunStatus.FAILED
    assert result.failure_code == "embedding_failure"
    assert result.embedding_failure_count == 1
    assert result.failure_detail == "Document embeddings could not be generated."
    assert result.occurrence_count == 1
    assert result.unique_content_count == 1
    assert source.strip() not in result.failure_detail
    assert secret_token not in result.failure_detail
    assert host_path not in result.failure_detail
    assert repository.ready_results == []
    assert repository.failed_results == [result]


def test_malformed_embedding_batch_fails_run_without_remaining_building() -> None:
    """A short backend batch must never allow a partially embedded ready run."""
    repository = _FakeRepository()
    parser = _FakeParser()
    embeddings = _FakeEmbeddings(fixed_batch=())

    result = _index_without_leaking(
        _service(repository, parser, embeddings),
        _snapshot(("docs/research/a.md", b"source\n")),
    )

    assert result.status is IndexRunStatus.FAILED
    assert result.failure_code == "invariant_failure"
    assert result.invariant_failure_count == 1
    assert result.failure_detail == "Index artifacts violated their identity contract."
    assert result.occurrence_count == 1
    assert result.unique_content_count == 1
    assert repository.ready_results == []
    assert repository.failed_results == [result]
    assert repository.embedding_links == {}


def test_provider_vector_dimension_is_a_sanitized_invariant_failure() -> None:
    """A malformed provider dimension must never reach the repository."""
    repository = _FakeRepository()
    parser = _FakeParser()
    embeddings = _FakeEmbeddings(fixed_batch=((0.1, 0.2),))

    result = _index_without_leaking(
        _service(repository, parser, embeddings, embedding_dimension=3),
        _snapshot(("docs/research/a.md", b"source\n")),
    )

    assert result.status is IndexRunStatus.FAILED
    assert result.failure_code == "invariant_failure"
    assert result.invariant_failure_count == 1
    assert repository.ready_results == []
    assert repository.failed_results == [result]
    assert repository.embedding_links == {}


@pytest.mark.parametrize("coordinate", [True, float("nan"), float("inf"), 1e100])
def test_malformed_provider_coordinate_is_a_sanitized_invariant_failure(
    coordinate: object,
) -> None:
    """Invalid provider coordinates are classified before repository calls."""
    repository = _FakeRepository()
    parser = _FakeParser()
    embeddings = _FakeEmbeddings(
        fixed_batch=((coordinate, 0.2),),  # type: ignore[arg-type]
    )

    result = _service(repository, parser, embeddings).index(
        _snapshot(("docs/research/a.md", b"source\n"))
    )

    assert result.status is IndexRunStatus.FAILED
    assert result.failure_code == "invariant_failure"
    assert repository.embedding_links == {}


def test_repository_invariant_error_propagates_without_failed_result() -> None:
    """Persistence invariants are not malformed provider output."""
    error = RepositoryInvariantError("repository identity conflict")
    repository = _FakeRepository(save_embedding_error=error)
    parser = _FakeParser()
    embeddings = _FakeEmbeddings(fixed_batch=((0.1, 0.2),))

    with pytest.raises(RepositoryInvariantError, match="identity conflict"):
        _service(repository, parser, embeddings).index(
            _snapshot(("docs/research/a.md", b"source\n"))
        )

    assert repository.ready_results == []
    assert repository.failed_results == []


def test_parser_version_mismatch_is_a_sanitized_invariant_failure() -> None:
    """Storing parser-v2 output under a parser-v1 key would corrupt reuse."""
    repository = _FakeRepository()
    parser = _FakeParser(parser_version="parser-v2")
    embeddings = _FakeEmbeddings()

    result = _index_without_leaking(
        _service(repository, parser, embeddings),
        _snapshot(("docs/research/a.md", b"source\n")),
    )

    assert result.status is IndexRunStatus.FAILED
    assert result.failure_code == "invariant_failure"
    assert result.invariant_failure_count == 1
    assert result.failure_detail == "Index artifacts violated their identity contract."
    assert result.occurrence_count == 1
    assert result.unique_content_count == 1
    assert repository.parses == {}
    assert repository.embedding_links == {}
    assert repository.ready_results == []
    assert repository.failed_results == [result]


def test_reused_parse_with_wrong_chunk_config_is_an_invariant_failure() -> None:
    """A repository key mismatch must not publish incompatible chunks as ready."""
    repository = _FakeRepository()
    parser = _FakeParser()
    embeddings = _FakeEmbeddings()
    index_service = _service(repository, parser, embeddings)
    snapshot = _snapshot(("docs/research/a.md", b"source\n"))
    first = index_service.index(snapshot)
    parse_key, stored = next(iter(repository.parses.items()))
    repository.parses[parse_key] = ParseArtifacts(
        parser_version=stored.parser_version,
        chunk_config_hash="d" * 64,
        parsed=stored.parsed,
        chunks=stored.chunks,
    )

    result = index_service.index(snapshot)

    assert first.status is IndexRunStatus.READY
    assert result.status is IndexRunStatus.FAILED
    assert result.failure_code == "invariant_failure"
    assert result.invariant_failure_count == 1
    assert result.failure_detail == "Index artifacts violated their identity contract."
    assert result.occurrence_count == 1
    assert result.unique_content_count == 1
    assert len(repository.ready_results) == 1
    assert repository.failed_results == [result]


def test_out_of_profile_path_is_a_sanitized_invariant_failure() -> None:
    """A source-provider scope violation must not leave its run building."""
    repository = _FakeRepository()
    parser = _FakeParser()
    embeddings = _FakeEmbeddings()

    result = _index_without_leaking(
        _service(repository, parser, embeddings),
        _snapshot(("other/a.md", b"source\n")),
    )

    assert result.status is IndexRunStatus.FAILED
    assert result.failure_code == "invariant_failure"
    assert result.invariant_failure_count == 1
    assert result.failure_detail == "Index artifacts violated their identity contract."
    assert result.occurrence_count == 0
    assert result.unique_content_count == 1
    assert repository.occurrences == []
    assert repository.ready_results == []
    assert repository.failed_results == [result]


def test_existing_parse_and_embedding_skip_work_on_the_next_run() -> None:
    """Reprocessing an unchanged snapshot would defeat incremental indexing."""
    repository = _FakeRepository()
    parser = _FakeParser()
    embeddings = _FakeEmbeddings()
    index_service = _service(repository, parser, embeddings)
    snapshot = _snapshot(("docs/research/a.md", b"source\n"))

    first = index_service.index(snapshot)
    second = index_service.index(snapshot)

    assert first.status is IndexRunStatus.READY
    assert second.status is IndexRunStatus.READY
    assert parser.calls == 1
    assert embeddings.document_calls == 1
    assert len(repository.ready_results) == 2
    assert len(repository.embedding_links) == 1


def test_reused_vector_is_linked_to_every_distinct_stored_chunk() -> None:
    """Hash reuse must not omit the embedding row for a second chunk UUID."""
    repository = _FakeRepository()
    parser = _FakeParser()
    embeddings = _FakeEmbeddings()
    fixed_chunk_hash = "f" * 64

    result = _service(
        repository,
        parser,
        embeddings,
        chunker=_FakeChunker(fixed_chunk_hash),
    ).index(
        _snapshot(
            ("docs/research/a.md", b"first source\n"),
            ("docs/research/b.md", b"second source\n"),
        )
    )

    assert result.status is IndexRunStatus.READY
    assert result.unique_content_count == 2
    assert parser.calls == 2
    assert embeddings.document_calls == 1
    assert len(repository.embedding_links) == 2
    assert len(repository.reusable_vectors) == 1
    assert len({chunk_id for chunk_id, _ in repository.embedding_links}) == 2
    assert set(repository.chunk_hash_by_id.values()) == {fixed_chunk_hash}
