"""Orchestrate reusable indexing artifacts for immutable source snapshots."""

import math
import struct
from collections.abc import Sequence
from dataclasses import dataclass
from numbers import Real
from typing import Protocol
from uuid import UUID

from omf_retrieval.application.indexing.hashing import content_hash
from omf_retrieval.application.indexing.metadata import (
    MetadataExtractionError,
    extract_metadata,
)
from omf_retrieval.application.indexing.ports import (
    ChunkDraft,
    ParsedMarkdown,
    ParsedSection,
    SourceSnapshot,
    split_physical_lines,
)
from omf_retrieval.domain.enums import IndexRunStatus
from omf_retrieval.domain.models import DocumentMetadata

_FAILURE_DETAILS = {
    "decode_failure": "A source document is not valid UTF-8.",
    "parse_failure": "A source document could not be parsed.",
    "embedding_failure": "Document embeddings could not be generated.",
    "invariant_failure": "Index artifacts violated their identity contract.",
}

_BOUNDARY_EXCEPTIONS = (OSError, RecursionError, RuntimeError, TypeError, ValueError)


@dataclass(frozen=True, slots=True)
class StoredChunk:
    """Bind one reusable chunk draft to its persistent storage identity."""

    chunk_id: UUID
    draft: ChunkDraft


@dataclass(frozen=True, slots=True)
class ParseArtifacts:
    """Bundle one reusable parsed document with stored retrieval chunks."""

    parser_version: str
    chunk_config_hash: str
    parsed: ParsedMarkdown
    chunks: tuple[StoredChunk, ...]


@dataclass(frozen=True, slots=True)
class IndexResult:
    """Report safe counters and lifecycle state for one indexing run."""

    run_id: UUID
    status: IndexRunStatus
    occurrence_count: int
    unique_content_count: int
    excluded_file_count: int = 0
    empty_document_count: int = 0
    decode_failure_count: int = 0
    parse_failure_count: int = 0
    embedding_failure_count: int = 0
    invariant_failure_count: int = 0
    failure_code: str | None = None
    failure_detail: str | None = None

    def __post_init__(self) -> None:
        """Require an exact persisted run identity."""
        if type(self.run_id) is not UUID:
            raise TypeError("run_id must be an exact UUID")


class _Parser(Protocol):
    def parse(self, source: str) -> ParsedMarkdown:
        """Return deterministic Markdown structure for exact source text."""


class _Chunker(Protocol):
    def split(
        self,
        section: ParsedSection,
        *,
        parser_version: str,
    ) -> tuple[ChunkDraft, ...]:
        """Return deterministic retrieval chunks for one parsed section."""


class _Embeddings(Protocol):
    def embed_documents(
        self,
        documents: Sequence[str],
    ) -> tuple[tuple[float, ...], ...]:
        """Return one embedding vector for each document in input order."""


class _Repository(Protocol):
    def create_building_run(self, commit_sha: str) -> UUID:
        """Create a non-active run and return its storage identifier."""

    def upsert_content(self, content_digest: str, source: str) -> UUID:
        """Return the identifier for content stored by its exact hash."""

    def create_occurrence(
        self,
        run_id: UUID,
        content_id: UUID,
        source_path: str,
        metadata: DocumentMetadata,
    ) -> None:
        """Record one source path observed in this run."""

    def find_parse(
        self,
        content_id: UUID,
        parser_version: str,
        chunk_config_hash: str,
    ) -> ParseArtifacts | None:
        """Find reusable parse artifacts for the exact content and config."""

    def save_parse(
        self,
        content_id: UUID,
        parser_version: str,
        chunk_config_hash: str,
        parsed: ParsedMarkdown,
        chunks: tuple[ChunkDraft, ...],
    ) -> ParseArtifacts:
        """Persist a parsed document and return stored chunk identities."""

    def find_embedding(
        self,
        chunk_id: UUID,
        embedding_config_hash: str,
    ) -> tuple[float, ...] | None:
        """Find the vector already linked to an exact stored chunk."""

    def find_reusable_embedding(
        self,
        chunk_hash: str,
        embedding_config_hash: str,
    ) -> tuple[float, ...] | None:
        """Find a vector computed for matching chunk content and config."""

    def save_embedding(
        self,
        chunk_id: UUID,
        embedding_config_hash: str,
        vector: tuple[float, ...],
    ) -> None:
        """Persist or link a vector for an exact stored chunk and config."""

    def mark_ready(self, run_id: UUID, result: IndexResult) -> None:
        """Record successful completion without activating the run."""

    def mark_failed(self, run_id: UUID, result: IndexResult) -> None:
        """Record a sanitized terminal failure for this non-active run."""


class IndexService:
    """Build one ready index run while reusing content-addressed artifacts."""

    def __init__(
        self,
        *,
        repository: _Repository,
        parser: _Parser,
        chunker: _Chunker,
        embeddings: _Embeddings,
        parser_version: str,
        chunk_config_hash: str,
        embedding_config_hash: str,
        embedding_dimension: int,
    ) -> None:
        """Bind deterministic processing dependencies and config identities."""
        self._repository = repository
        self._parser = parser
        self._chunker = chunker
        self._embeddings = embeddings
        self._parser_version = parser_version
        self._chunk_config_hash = chunk_config_hash
        self._embedding_config_hash = embedding_config_hash
        if type(embedding_dimension) is not int or embedding_dimension <= 0:
            raise ValueError("embedding_dimension must be a positive exact integer")
        self._embedding_dimension = embedding_dimension

    def index(
        self,
        snapshot: SourceSnapshot,
        *,
        run_id: UUID | None = None,
    ) -> IndexResult:
        """Build reusable artifacts for every path in one immutable snapshot."""
        if run_id is None:
            run_id = self._repository.create_building_run(snapshot.commit_sha)
        elif type(run_id) is not UUID:
            raise TypeError("run_id must be an exact UUID")
        content_digests: set[str] = set()
        occurrence_count = 0
        empty_document_count = 0

        for archive_file in snapshot.archive_files:
            try:
                source = archive_file.content.decode("utf-8")
            except UnicodeDecodeError:
                return self._fail(
                    run_id=run_id,
                    occurrence_count=occurrence_count,
                    unique_content_count=len(content_digests),
                    excluded_file_count=snapshot.excluded_file_count,
                    empty_document_count=empty_document_count,
                    failure_code="decode_failure",
                )
            digest = content_hash(archive_file.content)
            content_digests.add(digest)
            content_id = self._repository.upsert_content(digest, source)
            try:
                metadata = extract_metadata(
                    archive_file.source_path,
                    split_physical_lines(source),
                )
            except MetadataExtractionError:
                return self._fail(
                    run_id=run_id,
                    occurrence_count=occurrence_count,
                    unique_content_count=len(content_digests),
                    excluded_file_count=snapshot.excluded_file_count,
                    empty_document_count=empty_document_count,
                    failure_code="invariant_failure",
                )
            self._repository.create_occurrence(
                run_id,
                content_id,
                archive_file.source_path,
                metadata,
            )
            occurrence_count += 1
            if not source.strip():
                empty_document_count += 1
                continue
            artifacts = self._repository.find_parse(
                content_id,
                self._parser_version,
                self._chunk_config_hash,
            )
            if artifacts is None:
                try:
                    parsed = self._parser.parse(source)
                except _BOUNDARY_EXCEPTIONS:
                    # Adapter exceptions may contain source text or host paths.
                    return self._fail(
                        run_id=run_id,
                        occurrence_count=occurrence_count,
                        unique_content_count=len(content_digests),
                        excluded_file_count=snapshot.excluded_file_count,
                        empty_document_count=empty_document_count,
                        failure_code="parse_failure",
                    )
                if (
                    type(parsed) is not ParsedMarkdown
                    or parsed.parser_version != self._parser_version
                ):
                    return self._fail(
                        run_id=run_id,
                        occurrence_count=occurrence_count,
                        unique_content_count=len(content_digests),
                        excluded_file_count=snapshot.excluded_file_count,
                        empty_document_count=empty_document_count,
                        failure_code="invariant_failure",
                    )
                try:
                    chunks = tuple(
                        chunk
                        for section in parsed.sections
                        for chunk in self._chunker.split(
                            section,
                            parser_version=parsed.parser_version,
                        )
                    )
                except _BOUNDARY_EXCEPTIONS:
                    # Adapter exceptions may contain source text or host paths.
                    return self._fail(
                        run_id=run_id,
                        occurrence_count=occurrence_count,
                        unique_content_count=len(content_digests),
                        excluded_file_count=snapshot.excluded_file_count,
                        empty_document_count=empty_document_count,
                        failure_code="parse_failure",
                    )
                artifacts = self._repository.save_parse(
                    content_id,
                    self._parser_version,
                    self._chunk_config_hash,
                    parsed,
                    chunks,
                )
            if not self._valid_parse_artifacts(artifacts):
                return self._fail(
                    run_id=run_id,
                    occurrence_count=occurrence_count,
                    unique_content_count=len(content_digests),
                    excluded_file_count=snapshot.excluded_file_count,
                    empty_document_count=empty_document_count,
                    failure_code="invariant_failure",
                )
            missing_by_hash: dict[str, list[StoredChunk]] = {}
            for stored_chunk in artifacts.chunks:
                if (
                    self._repository.find_embedding(
                        stored_chunk.chunk_id,
                        self._embedding_config_hash,
                    )
                    is not None
                ):
                    continue
                reusable_vector = self._repository.find_reusable_embedding(
                    stored_chunk.draft.chunk_hash,
                    self._embedding_config_hash,
                )
                if reusable_vector is not None:
                    self._repository.save_embedding(
                        stored_chunk.chunk_id,
                        self._embedding_config_hash,
                        reusable_vector,
                    )
                    continue
                missing_by_hash.setdefault(
                    stored_chunk.draft.chunk_hash,
                    [],
                ).append(stored_chunk)
            if missing_by_hash:
                missing_groups = tuple(missing_by_hash.values())
                try:
                    vectors = self._embeddings.embed_documents(
                        tuple(group[0].draft.search_text for group in missing_groups)
                    )
                except _BOUNDARY_EXCEPTIONS:
                    # Backend exceptions may contain source text or host paths.
                    return self._fail(
                        run_id=run_id,
                        occurrence_count=occurrence_count,
                        unique_content_count=len(content_digests),
                        excluded_file_count=snapshot.excluded_file_count,
                        empty_document_count=empty_document_count,
                        failure_code="embedding_failure",
                    )
                if not self._valid_embedding_batch(vectors, len(missing_groups)):
                    return self._fail(
                        run_id=run_id,
                        occurrence_count=occurrence_count,
                        unique_content_count=len(content_digests),
                        excluded_file_count=snapshot.excluded_file_count,
                        empty_document_count=empty_document_count,
                        failure_code="invariant_failure",
                    )
                for group, vector in zip(missing_groups, vectors, strict=True):
                    for stored_chunk in group:
                        self._repository.save_embedding(
                            stored_chunk.chunk_id,
                            self._embedding_config_hash,
                            vector,
                        )

        result = IndexResult(
            run_id=run_id,
            status=IndexRunStatus.READY,
            occurrence_count=occurrence_count,
            unique_content_count=len(content_digests),
            excluded_file_count=snapshot.excluded_file_count,
            empty_document_count=empty_document_count,
        )
        self._repository.mark_ready(run_id, result)
        return result

    def _valid_parse_artifacts(self, artifacts: object) -> bool:
        """Return whether stored artifacts match the active parse identity."""
        return (
            type(artifacts) is ParseArtifacts
            and artifacts.parser_version == self._parser_version
            and artifacts.chunk_config_hash == self._chunk_config_hash
            and artifacts.parsed.parser_version == self._parser_version
            and type(artifacts.chunks) is tuple
            and all(type(chunk) is StoredChunk for chunk in artifacts.chunks)
            and len({chunk.chunk_id for chunk in artifacts.chunks})
            == len(artifacts.chunks)
        )

    def _fail(
        self,
        *,
        run_id: UUID,
        occurrence_count: int,
        unique_content_count: int,
        excluded_file_count: int,
        empty_document_count: int,
        failure_code: str,
    ) -> IndexResult:
        """Persist one source-free failed result and return it to the caller."""
        result = IndexResult(
            run_id=run_id,
            status=IndexRunStatus.FAILED,
            occurrence_count=occurrence_count,
            unique_content_count=unique_content_count,
            excluded_file_count=excluded_file_count,
            empty_document_count=empty_document_count,
            decode_failure_count=int(failure_code == "decode_failure"),
            parse_failure_count=int(failure_code == "parse_failure"),
            embedding_failure_count=int(failure_code == "embedding_failure"),
            invariant_failure_count=int(failure_code == "invariant_failure"),
            failure_code=failure_code,
            failure_detail=_FAILURE_DETAILS[failure_code],
        )
        self._repository.mark_failed(run_id, result)
        return result

    def _valid_embedding_batch(self, vectors: object, expected_count: int) -> bool:
        """Validate provider-owned vector shape before repository persistence."""
        return (
            type(vectors) is tuple
            and len(vectors) == expected_count
            and all(
                type(vector) is tuple
                and len(vector) == self._embedding_dimension
                and all(self._valid_embedding_coordinate(value) for value in vector)
                for vector in vectors
            )
        )

    @staticmethod
    def _valid_embedding_coordinate(coordinate: object) -> bool:
        """Return whether a provider coordinate fits PostgreSQL float32 safely."""
        if isinstance(coordinate, bool) or not isinstance(coordinate, Real):
            return False
        try:
            value = float(coordinate)
            struct.pack("!f", value)
        except (OverflowError, TypeError, ValueError):
            return False
        return math.isfinite(value)
