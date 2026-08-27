"""PostgreSQL adapter for atomic index activation and rollback."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, exists, func, select
from sqlalchemy.orm import Session, aliased

from omf_retrieval.application.indexing.activation import (
    ActivationError,
    RollbackCandidate,
    TransitionResult,
)
from omf_retrieval.application.indexing.artifact_identity import (
    parse_artifact_manifest,
)
from omf_retrieval.application.indexing.ports import ChunkDraft, ParsedSection
from omf_retrieval.domain.enums import IndexRunStatus
from omf_retrieval.infrastructure.database.models import (
    Chunk,
    ChunkEmbedding,
    DocumentContent,
    DocumentOccurrence,
    DocumentParse,
    IndexConfig,
    IndexRun,
    Section,
    SourceProfile,
)
from omf_retrieval.infrastructure.database.repositories import _advisory_lock_key
from omf_retrieval.infrastructure.database.repository_config import (
    document_embedding_config_hash,
)

_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_GC_BATCH_SIZE = 1_000


@dataclass(frozen=True, slots=True)
class _ContentClassification:
    occurrence_count: int
    unique_content_count: int
    empty_document_count: int
    nonempty_content_ids: frozenset[UUID]


def _classify_contents(
    rows: tuple[tuple[UUID, str], ...],
) -> _ContentClassification:
    """Apply the exact Task 8 Python ``source.strip()`` empty contract."""
    if type(rows) is not tuple:
        raise ActivationError("Rollback target occurrence metadata is incomplete.")
    content_ids: set[UUID] = set()
    nonempty_content_ids: set[UUID] = set()
    empty_document_count = 0
    for row in rows:
        if (
            type(row) is not tuple
            or len(row) != 2
            or type(row[0]) is not UUID
            or type(row[1]) is not str
        ):
            raise ActivationError("Rollback target occurrence metadata is incomplete.")
        content_id, source = row
        content_ids.add(content_id)
        if source.strip():
            nonempty_content_ids.add(content_id)
        else:
            empty_document_count += 1
    return _ContentClassification(
        occurrence_count=len(rows),
        unique_content_count=len(content_ids),
        empty_document_count=empty_document_count,
        nonempty_content_ids=frozenset(nonempty_content_ids),
    )


def _validate_manifest_rows(
    section_rows: tuple[tuple[object, ...], ...],
    chunk_rows: tuple[tuple[object, ...], ...],
    nonempty_content_ids: frozenset[UUID],
) -> None:
    """Recompute each target parse manifest from bounded set-based query rows."""
    sections_by_content: dict[UUID, list[tuple[object, ...]]] = {}
    for row in section_rows:
        if type(row) is not tuple or len(row) != 15 or type(row[0]) is not UUID:
            raise ActivationError("Rollback target search artifacts are incomplete.")
        if row[0] not in nonempty_content_ids:
            raise ActivationError("Rollback target search artifacts are incomplete.")
        sections_by_content.setdefault(row[0], []).append(row)
    chunks_by_content: dict[UUID, list[tuple[object, ...]]] = {}
    for row in chunk_rows:
        if type(row) is not tuple or len(row) != 11 or type(row[0]) is not UUID:
            raise ActivationError("Rollback target search artifacts are incomplete.")
        if row[0] not in nonempty_content_ids:
            raise ActivationError("Rollback target search artifacts are incomplete.")
        chunks_by_content.setdefault(row[0], []).append(row)

    for content_id in nonempty_content_ids:
        stored_sections = sections_by_content.get(content_id, [])
        if not stored_sections:
            raise ActivationError("Rollback target search artifacts are incomplete.")
        declared = {(row[1], row[2], row[3], row[4]) for row in stored_sections}
        if len(declared) != 1:
            raise ActivationError("Rollback target search artifacts are incomplete.")
        _, section_count, chunk_count, artifact_hash = declared.pop()
        sections: list[ParsedSection] = []
        section_ids: set[UUID] = set()
        for row in stored_sections:
            section_id, parent_id = row[5], row[6]
            if type(section_id) is not UUID:
                raise ActivationError(
                    "Rollback target search artifacts are incomplete."
                )
            if parent_id is not None and (
                type(parent_id) is not UUID or row[8] is None
            ):
                raise ActivationError(
                    "Rollback target search artifacts are incomplete."
                )
            section_ids.add(section_id)
            try:
                sections.append(
                    ParsedSection(
                        ordinal=row[7],
                        parent_ordinal=row[8],
                        level=row[9],
                        heading=row[10],
                        heading_path=tuple(row[11]),
                        body=row[12],
                        line_start=row[13],
                        line_end=row[14],
                        blocks=(),
                    )
                )
            except (TypeError, ValueError):
                raise ActivationError(
                    "Rollback target search artifacts are incomplete."
                ) from None
        if any(
            row[6] is not None and row[6] not in section_ids for row in stored_sections
        ):
            raise ActivationError("Rollback target search artifacts are incomplete.")

        drafts: list[ChunkDraft] = []
        owners: list[int] = []
        for row in chunks_by_content.get(content_id, []):
            if type(row[10]) is not int or row[10] != 1:
                raise ActivationError(
                    "Rollback target search artifacts are incomplete."
                )
            try:
                owners.append(row[1])
                drafts.append(
                    ChunkDraft(
                        ordinal=row[3],
                        raw_text=row[4],
                        search_text=row[5],
                        token_count=row[6],
                        line_start=row[7],
                        line_end=row[8],
                        chunk_hash=row[9],
                    )
                )
            except (TypeError, ValueError):
                raise ActivationError(
                    "Rollback target search artifacts are incomplete."
                ) from None
        try:
            manifest = parse_artifact_manifest(
                tuple(sections),
                tuple(drafts),
                tuple(owners),
            )
        except ValueError:
            raise ActivationError(
                "Rollback target search artifacts are incomplete."
            ) from None
        if (
            type(section_count) is not int
            or type(chunk_count) is not int
            or type(artifact_hash) is not str
            or section_count != manifest.section_count
            or chunk_count != manifest.chunk_count
            or artifact_hash != manifest.artifact_hash
        ):
            raise ActivationError("Rollback target search artifacts are incomplete.")


class PostgresActivationRepository:
    """Mutate one source lifecycle inside a caller-owned transaction."""

    def __init__(self, session: Session) -> None:
        if not isinstance(session, Session):
            required = ("delete", "execute", "flush", "get", "scalar", "scalars")
            if not all(hasattr(session, attribute) for attribute in required):
                raise TypeError("session must provide the SQLAlchemy Session contract")
        self._session = session
        self._prepared_candidate: RollbackCandidate | None = None

    def activate(
        self,
        source_key: str,
        run_id: UUID,
        occurred_at: datetime,
    ) -> TransitionResult:
        """Replace the active generation and archive/prune the older previous."""
        _require_utc(occurred_at)
        if type(run_id) is not UUID:
            raise ActivationError("run_id must be an exact UUID")
        source = self._lock_source(source_key)
        target = self._locked_run(run_id)
        if (
            target is None
            or target.source_profile_id != source.id
            or target.status != IndexRunStatus.READY.value
        ):
            raise ActivationError("Only a READY run from the same source can activate.")

        active = self._unique_status_run(source.id, IndexRunStatus.ACTIVE)
        previous = self._unique_status_run(source.id, IndexRunStatus.PREVIOUS)
        self._assert_pointer(source, active)
        if active is None and previous is not None:
            raise ActivationError("Source lifecycle state is inconsistent.")
        old_content_ids: tuple[UUID, ...] = ()
        if previous is not None:
            if previous.activated_at is None:
                raise ActivationError("Previous lifecycle metadata is incomplete.")
            old_content_ids = tuple(
                self._session.scalars(
                    select(DocumentOccurrence.content_id)
                    .where(DocumentOccurrence.run_id == previous.id)
                    .distinct()
                )
            )
            previous.status = IndexRunStatus.ARCHIVED.value
            self._session.flush()
        if active is not None:
            if active.activated_at is None:
                raise ActivationError("Active lifecycle metadata is incomplete.")
            active.status = IndexRunStatus.PREVIOUS.value
            self._session.flush()

        target.status = IndexRunStatus.ACTIVE.value
        target.activated_at = occurred_at
        source.active_index_run_id = target.id
        self._session.flush()
        if previous is not None:
            self._prune_archived_run(previous.id, old_content_ids)
        return TransitionResult(
            action="activate",
            occurred_at=occurred_at,
            source_key=source.source_key,
            from_run_id=active.id if active is not None else None,
            to_run_id=target.id,
            from_config_hash=(
                self._config_hash(active.index_config_id)
                if active is not None
                else None
            ),
            to_config_hash=self._config_hash(target.index_config_id),
        )

    def prepare_rollback(self, source_key: str) -> RollbackCandidate:
        """Lock and validate the unique active/previous pair without mutation."""
        source = self._lock_source(source_key)
        active = self._unique_status_run(source.id, IndexRunStatus.ACTIVE)
        previous = self._unique_status_run(source.id, IndexRunStatus.PREVIOUS)
        self._assert_pointer(source, active)
        if active is None or previous is None:
            raise ActivationError("Rollback requires one active and one previous run.")
        if active.activated_at is None or previous.activated_at is None:
            raise ActivationError("Rollback lifecycle metadata is incomplete.")
        active_config = self._session.get(IndexConfig, active.index_config_id)
        target_config = self._session.get(IndexConfig, previous.index_config_id)
        if active_config is None or target_config is None:
            raise ActivationError("Rollback configuration metadata is incomplete.")
        self._assert_target_artifacts(previous, target_config)
        candidate = RollbackCandidate(
            source_key=source.source_key,
            active_run_id=active.id,
            active_config_hash=active_config.config_hash,
            target_run_id=previous.id,
            target_config_hash=target_config.config_hash,
            parser_config=dict(target_config.parser_config),
            chunk_config=dict(target_config.chunk_config),
            tokenizer_config=dict(target_config.tokenizer_config),
            embedding_config=dict(target_config.embedding_config),
            rrf_config=dict(target_config.rrf_config),
        )
        self._prepared_candidate = candidate
        return candidate

    def rollback(
        self,
        candidate: RollbackCandidate,
        occurred_at: datetime,
    ) -> TransitionResult:
        """Exchange the locked current active and previous generations."""
        _require_utc(occurred_at)
        if type(candidate) is not RollbackCandidate:
            raise ActivationError("Rollback candidate has an invalid contract.")
        if candidate is not self._prepared_candidate:
            raise ActivationError(
                "Rollback candidate was not prepared under this lock."
            )
        source = self._session.scalar(
            select(SourceProfile).where(
                SourceProfile.source_key == candidate.source_key
            )
        )
        active = self._locked_run(candidate.active_run_id)
        target = self._locked_run(candidate.target_run_id)
        if (
            source is None
            or active is None
            or target is None
            or source.active_index_run_id != active.id
            or active.source_profile_id != source.id
            or target.source_profile_id != source.id
            or active.status != IndexRunStatus.ACTIVE.value
            or target.status != IndexRunStatus.PREVIOUS.value
            or self._config_hash(active.index_config_id) != candidate.active_config_hash
            or self._config_hash(target.index_config_id) != candidate.target_config_hash
        ):
            raise ActivationError("Rollback candidate changed during validation.")
        target.status = IndexRunStatus.ARCHIVED.value
        self._session.flush()
        active.status = IndexRunStatus.PREVIOUS.value
        self._session.flush()
        target.status = IndexRunStatus.ACTIVE.value
        target.activated_at = occurred_at
        source.active_index_run_id = target.id
        self._session.flush()
        self._prepared_candidate = None
        return TransitionResult(
            action="rollback",
            occurred_at=occurred_at,
            source_key=source.source_key,
            from_run_id=active.id,
            to_run_id=target.id,
            from_config_hash=candidate.active_config_hash,
            to_config_hash=candidate.target_config_hash,
        )

    def _lock_source(self, source_key: object) -> SourceProfile:
        if type(source_key) is not str or not source_key.strip():
            raise ActivationError("source_key must be a nonblank string")
        identity = self._session.execute(
            select(SourceProfile.id, SourceProfile.active_index_run_id).where(
                SourceProfile.source_key == source_key
            )
        ).one_or_none()
        if identity is None:
            raise ActivationError("Source profile does not exist.")
        source_id, observed_pointer = identity
        acquired = self._session.scalar(
            select(func.pg_try_advisory_xact_lock(_advisory_lock_key(source_id)))
        )
        if acquired is not True:
            raise ActivationError("Another lifecycle operation is in progress.")
        source = self._session.scalar(
            select(SourceProfile)
            .where(
                SourceProfile.id == source_id,
                SourceProfile.source_key == source_key,
            )
            .with_for_update()
        )
        if source is None:
            raise ActivationError("Source profile identity changed during locking.")
        if source.active_index_run_id != observed_pointer:
            raise ActivationError("Source profile pointer changed during locking.")
        return source

    def _locked_run(self, run_id: UUID) -> IndexRun | None:
        return self._session.scalar(
            select(IndexRun).where(IndexRun.id == run_id).with_for_update()
        )

    def _unique_status_run(
        self,
        source_id: UUID,
        status: IndexRunStatus,
    ) -> IndexRun | None:
        runs = tuple(
            self._session.scalars(
                select(IndexRun)
                .where(
                    IndexRun.source_profile_id == source_id,
                    IndexRun.status == status.value,
                )
                .with_for_update()
            )
        )
        if len(runs) > 1:
            raise ActivationError("Source lifecycle state is ambiguous.")
        return runs[0] if runs else None

    @staticmethod
    def _assert_pointer(source: SourceProfile, active: IndexRun | None) -> None:
        expected = active.id if active is not None else None
        if source.active_index_run_id != expected:
            raise ActivationError("Active pointer and lifecycle status disagree.")

    def _config_hash(self, config_id: UUID) -> str:
        config = self._session.get(IndexConfig, config_id)
        if config is None:
            raise ActivationError("Index configuration metadata is incomplete.")
        return config.config_hash

    def _assert_target_artifacts(
        self,
        target: IndexRun,
        config: IndexConfig,
    ) -> None:
        stats = target.stats
        content_rows = tuple(
            tuple(row)
            for row in self._session.execute(
                select(DocumentOccurrence.content_id, DocumentContent.content)
                .join(
                    DocumentContent,
                    DocumentContent.id == DocumentOccurrence.content_id,
                )
                .where(DocumentOccurrence.run_id == target.id)
            )
        )
        classification = _classify_contents(content_rows)
        expected_stats = (
            stats.get("occurrence_count") if type(stats) is dict else None,
            stats.get("unique_content_count") if type(stats) is dict else None,
            stats.get("empty_document_count") if type(stats) is dict else None,
        )
        actual_stats = (
            classification.occurrence_count,
            classification.unique_content_count,
            classification.empty_document_count,
        )
        if any(type(value) is not int or value < 0 for value in expected_stats):
            raise ActivationError("Rollback target occurrence metadata is incomplete.")
        if expected_stats != actual_stats:
            raise ActivationError("Rollback target occurrence metadata is incomplete.")
        try:
            embedding_hash = document_embedding_config_hash(config.embedding_config)
            parser_version = config.parser_config["version"]
            chunk_config_hash = config.chunk_config["hash"]
            document_config = config.embedding_config["document"]
            if type(parser_version) is not str or not parser_version.strip():
                raise ValueError
            if (
                type(chunk_config_hash) is not str
                or _SHA256_PATTERN.fullmatch(chunk_config_hash) is None
            ):
                raise ValueError
            if type(document_config) is not dict:
                raise ValueError
        except Exception:
            raise ActivationError(
                "Rollback target artifact metadata is incomplete."
            ) from None
        parent_section = aliased(Section)
        section_rows = self._session.execute(
            select(
                DocumentParse.content_id,
                DocumentParse.id,
                DocumentParse.section_count,
                DocumentParse.chunk_count,
                DocumentParse.artifact_hash,
                Section.id,
                Section.parent_section_id,
                Section.ordinal,
                parent_section.ordinal,
                Section.level,
                Section.heading,
                Section.heading_path,
                Section.body,
                Section.line_start,
                Section.line_end,
            )
            .outerjoin(Section, Section.parse_id == DocumentParse.id)
            .outerjoin(
                parent_section,
                parent_section.id == Section.parent_section_id,
            )
            .where(
                exists().where(
                    DocumentOccurrence.run_id == target.id,
                    DocumentOccurrence.content_id == DocumentParse.content_id,
                ),
                DocumentParse.parser_version == parser_version,
                DocumentParse.chunk_config_hash == chunk_config_hash,
            )
            .order_by(DocumentParse.content_id, Section.ordinal)
        ).all()
        chunk_rows = self._session.execute(
            select(
                DocumentParse.content_id,
                Section.ordinal,
                Chunk.id,
                Chunk.ordinal,
                Chunk.raw_text,
                Chunk.search_text,
                Chunk.token_count,
                Chunk.line_start,
                Chunk.line_end,
                Chunk.chunk_hash,
                func.count(func.distinct(ChunkEmbedding.id)),
            )
            .join(Section, Section.parse_id == DocumentParse.id)
            .join(Chunk, Chunk.section_id == Section.id)
            .outerjoin(
                ChunkEmbedding,
                (ChunkEmbedding.chunk_id == Chunk.id)
                & (ChunkEmbedding.embedding_config_hash == embedding_hash)
                & (ChunkEmbedding.model_name == document_config["model_name"])
                & (ChunkEmbedding.model_revision == document_config["revision"])
                & (ChunkEmbedding.dimension == document_config["dimension"])
                & (ChunkEmbedding.status == "ready"),
            )
            .where(
                exists().where(
                    DocumentOccurrence.run_id == target.id,
                    DocumentOccurrence.content_id == DocumentParse.content_id,
                ),
                DocumentParse.parser_version == parser_version,
                DocumentParse.chunk_config_hash == chunk_config_hash,
            )
            .group_by(
                DocumentParse.content_id,
                Section.ordinal,
                Chunk.id,
                Chunk.ordinal,
                Chunk.raw_text,
                Chunk.search_text,
                Chunk.token_count,
                Chunk.line_start,
                Chunk.line_end,
                Chunk.chunk_hash,
            )
            .order_by(DocumentParse.content_id, Section.ordinal, Chunk.ordinal)
        ).all()
        _validate_manifest_rows(
            tuple(tuple(row) for row in section_rows),
            tuple(tuple(row) for row in chunk_rows),
            classification.nonempty_content_ids,
        )

    def _prune_archived_run(
        self,
        run_id: UUID,
        candidate_content_ids: tuple[UUID, ...],
    ) -> None:
        self._session.execute(
            delete(DocumentOccurrence).where(DocumentOccurrence.run_id == run_id)
        )
        self._session.flush()
        for batch_start in range(0, len(candidate_content_ids), _GC_BATCH_SIZE):
            candidate_batch = candidate_content_ids[
                batch_start : batch_start + _GC_BATCH_SIZE
            ]
            self._session.execute(
                delete(DocumentContent).where(
                    DocumentContent.id.in_(candidate_batch),
                    ~exists().where(
                        DocumentOccurrence.content_id == DocumentContent.id
                    ),
                )
            )
            self._session.flush()


def _require_utc(value: object) -> None:
    if type(value) is not datetime or value.tzinfo is not UTC:
        raise ActivationError("event time must be an aware UTC datetime")


__all__ = ["PostgresActivationRepository"]
