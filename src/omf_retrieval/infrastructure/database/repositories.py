"""PostgreSQL persistence adapter for reusable indexing artifacts."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from omf_retrieval.application.indexing.metadata import DocumentRelationSpec
from omf_retrieval.application.indexing.ports import (
    ChunkDraft,
    ParsedMarkdown,
)
from omf_retrieval.application.indexing.service import (
    IndexResult,
    ParseArtifacts,
)
from omf_retrieval.domain.enums import IndexRunStatus
from omf_retrieval.domain.models import DocumentMetadata, EmbeddingDescriptor
from omf_retrieval.infrastructure.database.models import (
    DocumentContent,
    DocumentOccurrence,
    DocumentRelation,
    IndexConfig,
    IndexRun,
)
from omf_retrieval.infrastructure.database.repository_artifacts import (
    ReusableArtifactStore,
)
from omf_retrieval.infrastructure.database.repository_config import (
    EmbeddingAdapterIdentity,
    IndexConfigValidationError,
    validate_persisted_index_config,
)
from omf_retrieval.infrastructure.database.repository_errors import (
    RepositoryInvariantError,
)

_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_SAFE_FAILURE_DETAILS = {
    "decode_failure": "A source document is not valid UTF-8.",
    "parse_failure": "A source document could not be parsed.",
    "embedding_failure": "Document embeddings could not be generated.",
    "invariant_failure": "Index artifacts violated their identity contract.",
}


def _advisory_lock_key(source_profile_id: UUID) -> int:
    """Derive a stable signed PostgreSQL bigint lock key for one source."""
    if type(source_profile_id) is not UUID:
        raise RepositoryInvariantError("source_profile_id must be an exact UUID")
    digest = hashlib.blake2b(
        source_profile_id.bytes,
        digest_size=8,
        person=b"omf-index",
    ).digest()
    return int.from_bytes(digest, byteorder="big", signed=True)


class PostgresIndexRepository:
    """Persist one source profile's artifacts in a caller-owned transaction."""

    def __init__(
        self,
        *,
        session: Session,
        source_profile_id: UUID,
        index_config_id: UUID,
        embedding_descriptor: EmbeddingDescriptor,
        embedding_adapter_identity: EmbeddingAdapterIdentity,
    ) -> None:
        """Bind an explicit session and immutable run/artifact identities."""
        if not isinstance(session, Session):
            required = ("add", "execute", "flush", "get", "scalar", "scalars")
            if not all(hasattr(session, attribute) for attribute in required):
                raise TypeError("session must provide the SQLAlchemy Session contract")
        if type(source_profile_id) is not UUID or type(index_config_id) is not UUID:
            raise RepositoryInvariantError("repository identities must be exact UUIDs")
        if type(embedding_descriptor) is not EmbeddingDescriptor:
            raise RepositoryInvariantError(
                "embedding_descriptor must use the exact domain contract"
            )
        if type(embedding_adapter_identity) is not EmbeddingAdapterIdentity:
            raise RepositoryInvariantError(
                "embedding adapter must use the exact immutable contract"
            )
        stored_config = session.get(IndexConfig, index_config_id)
        if stored_config is None:
            raise RepositoryInvariantError("index_config_id does not resolve")
        try:
            embedding_config_hash = validate_persisted_index_config(
                stored_config_hash=stored_config.config_hash,
                parser_config=stored_config.parser_config,
                chunk_config=stored_config.chunk_config,
                tokenizer_config=stored_config.tokenizer_config,
                embedding_config=stored_config.embedding_config,
                rrf_config=stored_config.rrf_config,
                descriptor=embedding_descriptor,
                adapter=embedding_adapter_identity,
            )
        except IndexConfigValidationError as error:
            raise RepositoryInvariantError(str(error)) from error
        self._session = session
        self._source_profile_id = source_profile_id
        self._index_config_id = index_config_id
        self._artifacts = ReusableArtifactStore(
            session,
            embedding_descriptor,
            embedding_config_hash,
        )

    def try_acquire_indexing_lock(self) -> bool:
        """Try to acquire this source's transaction-scoped advisory lock."""
        acquired = self._session.scalar(
            select(
                func.pg_try_advisory_xact_lock(
                    _advisory_lock_key(self._source_profile_id)
                )
            )
        )
        return acquired is True

    def create_building_run(self, commit_sha: str) -> UUID:
        """Create and flush one non-active building run."""
        if (
            type(commit_sha) is not str
            or re.fullmatch(r"[0-9a-f]{40}", commit_sha) is None
        ):
            raise RepositoryInvariantError(
                "commit_sha must be a full lowercase Git SHA"
            )
        run = IndexRun(
            source_profile_id=self._source_profile_id,
            index_config_id=self._index_config_id,
            commit_sha=commit_sha,
            status=IndexRunStatus.BUILDING.value,
        )
        self._session.add(run)
        self._session.flush()
        return run.id

    def upsert_content(self, content_digest: str, source: str) -> UUID:
        """Insert exact UTF-8 content once and reject a hash/content collision."""
        _require_sha256(content_digest, "content_digest")
        if type(source) is not str:
            raise RepositoryInvariantError("source content must be an exact string")
        byte_size = len(source.encode("utf-8"))
        statement = (
            insert(DocumentContent)
            .values(
                id=uuid4(),
                content_hash=content_digest,
                content=source,
                byte_size=byte_size,
            )
            .on_conflict_do_nothing(index_elements=[DocumentContent.content_hash])
            .returning(DocumentContent.id)
        )
        stored_id = self._session.scalar(statement)
        if stored_id is not None:
            return stored_id
        stored = self._session.scalar(
            select(DocumentContent).where(
                DocumentContent.content_hash == content_digest
            )
        )
        if stored is None:
            raise RepositoryInvariantError("content upsert did not resolve an identity")
        if stored.content != source or stored.byte_size != byte_size:
            raise RepositoryInvariantError(
                "content hash collision changed exact source"
            )
        return stored.id

    def create_occurrence(
        self,
        run_id: UUID,
        content_id: UUID,
        source_path: str,
        metadata: DocumentMetadata,
    ) -> None:
        """Create one path occurrence, treating an exact replay as idempotent."""
        self._require_owned_run(run_id)
        if type(content_id) is not UUID or type(source_path) is not str:
            raise RepositoryInvariantError("occurrence identities have invalid types")
        if type(metadata) is not DocumentMetadata:
            raise RepositoryInvariantError(
                "metadata must use the exact domain contract"
            )
        existing = self._session.scalar(
            select(DocumentOccurrence).where(
                DocumentOccurrence.run_id == run_id,
                DocumentOccurrence.source_path == source_path,
            )
        )
        expected = (
            content_id,
            metadata.version_scope.value,
            metadata.document_date,
            metadata.version,
            metadata.decision_state.value,
            metadata.owner_domain.value,
        )
        if existing is not None:
            actual = (
                existing.content_id,
                existing.version_scope,
                existing.document_date,
                existing.document_version,
                existing.decision_state,
                existing.owner_domain,
            )
            if actual != expected:
                raise RepositoryInvariantError(
                    "run source_path already has conflicting occurrence metadata"
                )
            return
        self._session.add(
            DocumentOccurrence(
                run_id=run_id,
                content_id=content_id,
                source_path=source_path,
                version_scope=metadata.version_scope.value,
                document_date=metadata.document_date,
                document_version=metadata.version,
                decision_state=metadata.decision_state.value,
                owner_domain=metadata.owner_domain.value,
            )
        )
        self._session.flush()

    def find_parse(
        self,
        content_id: UUID,
        parser_version: str,
        chunk_config_hash: str,
    ) -> ParseArtifacts | None:
        """Load reusable section and chunk artifacts for one parse identity."""
        return self._artifacts.find_parse(
            content_id,
            parser_version,
            chunk_config_hash,
        )

    def save_parse(
        self,
        content_id: UUID,
        parser_version: str,
        chunk_config_hash: str,
        parsed: ParsedMarkdown,
        chunks: tuple[ChunkDraft, ...],
    ) -> ParseArtifacts:
        """Persist a deterministic parse once and reject conflicting replay."""
        return self._artifacts.save_parse(
            content_id,
            parser_version,
            chunk_config_hash,
            parsed,
            chunks,
        )

    def find_embedding(
        self,
        chunk_id: UUID,
        embedding_config_hash: str,
    ) -> tuple[float, ...] | None:
        """Load the vector linked to one exact chunk and embedding config."""
        return self._artifacts.find_embedding(chunk_id, embedding_config_hash)

    def find_reusable_embedding(
        self,
        chunk_hash: str,
        embedding_config_hash: str,
    ) -> tuple[float, ...] | None:
        """Load one calculation shared by identical chunk content and config."""
        return self._artifacts.find_reusable_embedding(
            chunk_hash,
            embedding_config_hash,
        )

    def save_embedding(
        self,
        chunk_id: UUID,
        embedding_config_hash: str,
        vector: tuple[float, ...],
    ) -> None:
        """Persist or idempotently link an exact vector to one stored chunk."""
        self._artifacts.save_embedding(
            chunk_id,
            embedding_config_hash,
            vector,
        )

    def save_relations(
        self,
        run_id: UUID,
        relations: tuple[DocumentRelationSpec, ...],
    ) -> int:
        """Persist only explicit path-resolved relations; exact replay is a no-op."""
        self._require_owned_run(run_id)
        if type(relations) is not tuple or not all(
            type(relation) is DocumentRelationSpec for relation in relations
        ):
            raise RepositoryInvariantError(
                "relations must be an exact DocumentRelationSpec tuple"
            )
        occurrences = tuple(
            self._session.scalars(
                select(DocumentOccurrence).where(DocumentOccurrence.run_id == run_id)
            )
        )
        occurrence_by_path = {
            occurrence.source_path: occurrence for occurrence in occurrences
        }
        created = 0
        for relation in relations:
            try:
                from_occurrence = occurrence_by_path[relation.from_source_path]
                to_occurrence = occurrence_by_path[relation.to_source_path]
                occurrence_by_path[relation.evidence_source_path]
            except KeyError as error:
                raise RepositoryInvariantError(
                    "relation path must resolve inside the same index run"
                ) from error
            line_range = relation.evidence_line_range
            existing = self._session.scalar(
                select(DocumentRelation).where(
                    DocumentRelation.run_id == run_id,
                    DocumentRelation.from_occurrence_id == from_occurrence.id,
                    DocumentRelation.to_occurrence_id == to_occurrence.id,
                    DocumentRelation.relation_type == relation.relation_type.value,
                    DocumentRelation.evidence_source_path
                    == relation.evidence_source_path,
                )
            )
            if existing is not None:
                if (
                    existing.evidence_line_start != line_range.line_start
                    or existing.evidence_line_end != line_range.line_end
                ):
                    raise RepositoryInvariantError(
                        "relation replay changed its evidence line range"
                    )
                continue
            self._session.add(
                DocumentRelation(
                    run_id=run_id,
                    from_occurrence_id=from_occurrence.id,
                    to_occurrence_id=to_occurrence.id,
                    relation_type=relation.relation_type.value,
                    evidence_source_path=relation.evidence_source_path,
                    evidence_line_start=line_range.line_start,
                    evidence_line_end=line_range.line_end,
                )
            )
            self._session.flush()
            created += 1
        return created

    def mark_ready(self, run_id: UUID, result: IndexResult) -> None:
        """Record successful counters without changing the active pointer."""
        if type(result) is not IndexResult or result.status is not IndexRunStatus.READY:
            raise RepositoryInvariantError("ready transition requires a READY result")
        if result.run_id != run_id:
            raise RepositoryInvariantError("ready result run_id must match its run")
        if result.failure_code is not None or result.failure_detail is not None:
            raise RepositoryInvariantError("ready result cannot contain failure detail")
        run = self._require_building_run(run_id)
        run.status = IndexRunStatus.READY.value
        run.indexed_at = datetime.now(UTC)
        run.stats = _result_stats(result)
        run.failure_code = None
        run.failure_detail = None
        self._session.flush()

    def mark_failed(self, run_id: UUID, result: IndexResult) -> None:
        """Record only an approved fixed failure code/detail and safe counters."""
        if (
            type(result) is not IndexResult
            or result.status is not IndexRunStatus.FAILED
        ):
            raise RepositoryInvariantError("failed transition requires a FAILED result")
        if result.run_id != run_id:
            raise RepositoryInvariantError("failed result run_id must match its run")
        if (
            result.failure_code not in _SAFE_FAILURE_DETAILS
            or result.failure_detail != _SAFE_FAILURE_DETAILS[result.failure_code]
        ):
            raise RepositoryInvariantError("failure detail must be fixed and sanitized")
        run = self._require_building_run(run_id)
        run.status = IndexRunStatus.FAILED.value
        run.indexed_at = datetime.now(UTC)
        run.stats = _result_stats(result)
        run.failure_code = result.failure_code
        run.failure_detail = result.failure_detail
        self._session.flush()

    def _require_owned_run(self, run_id: UUID) -> IndexRun:
        if type(run_id) is not UUID:
            raise RepositoryInvariantError("run_id must be an exact UUID")
        run = self._session.get(IndexRun, run_id)
        if run is None or (
            run.source_profile_id != self._source_profile_id
            or run.index_config_id != self._index_config_id
        ):
            raise RepositoryInvariantError(
                "run does not belong to repository identities"
            )
        return run

    def _require_building_run(self, run_id: UUID) -> IndexRun:
        run = self._require_owned_run(run_id)
        if run.status != IndexRunStatus.BUILDING.value:
            raise RepositoryInvariantError("run transition requires building status")
        return run


def _require_sha256(value: object, field: str) -> None:
    if type(value) is not str or _SHA256_PATTERN.fullmatch(value) is None:
        raise RepositoryInvariantError(f"{field} must be a lowercase SHA-256 hash")


def _result_stats(result: IndexResult) -> dict[str, int]:
    return {
        "occurrence_count": result.occurrence_count,
        "unique_content_count": result.unique_content_count,
        "excluded_file_count": result.excluded_file_count,
        "empty_document_count": result.empty_document_count,
        "decode_failure_count": result.decode_failure_count,
        "parse_failure_count": result.parse_failure_count,
        "embedding_failure_count": result.embedding_failure_count,
        "invariant_failure_count": result.invariant_failure_count,
    }


__all__ = ["PostgresIndexRepository", "RepositoryInvariantError"]
