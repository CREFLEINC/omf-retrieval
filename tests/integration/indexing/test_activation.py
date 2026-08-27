"""PostgreSQL integration tests for atomic generation activation."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from omf_retrieval.application.indexing.activation import (
    ActivationAuditEvent,
    ActivationError,
    ActivationService,
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
from omf_retrieval.infrastructure.database.repository_activation import (
    PostgresActivationRepository,
)
from omf_retrieval.infrastructure.database.repository_config import (
    document_embedding_config_hash,
    full_index_config_hash,
)
from omf_retrieval.infrastructure.database.session import (
    create_database_engine,
    create_session_factory,
)
from omf_retrieval.infrastructure.embedding.provider import EmbeddingConfigSnapshot

_DATABASE_SUPPORT_SPEC = spec_from_file_location(
    "task9_activation_database_test_utils",
    Path(__file__).parents[1] / "database" / "database_test_utils.py",
)
assert _DATABASE_SUPPORT_SPEC is not None
assert _DATABASE_SUPPORT_SPEC.loader is not None
_DATABASE_SUPPORT = module_from_spec(_DATABASE_SUPPORT_SPEC)
_DATABASE_SUPPORT_SPEC.loader.exec_module(_DATABASE_SUPPORT)

TABLES = (
    "search_audit_events, client_source_grants, api_clients, document_relations, "
    "chunk_embeddings, chunks, sections, document_parses, document_occurrences, "
    "document_contents, index_runs, index_configs, source_profiles"
)
NOW = datetime(2026, 8, 24, 3, 4, 5, tzinfo=UTC)


@pytest.fixture
def activation_sessions(
    request: pytest.FixtureRequest,
) -> Iterator[sessionmaker[Session]]:
    """Yield an isolated migrated PostgreSQL session factory."""
    engine = create_database_engine(_DATABASE_SUPPORT.test_database_url())
    request.addfinalizer(engine.dispose)
    with engine.begin() as connection:
        _DATABASE_SUPPORT.assert_safe_test_connection(connection)
        connection.execute(text(f"TRUNCATE TABLE {TABLES} CASCADE"))

    def clean() -> None:
        with engine.begin() as connection:
            _DATABASE_SUPPORT.assert_safe_test_connection(connection)
            connection.execute(text(f"TRUNCATE TABLE {TABLES} CASCADE"))

    request.addfinalizer(clean)
    yield create_session_factory(engine)


def _embedding_snapshot() -> EmbeddingConfigSnapshot:
    return EmbeddingConfigSnapshot(
        provider="sentence-transformers",
        model_name="test/model",
        revision="revision-1",
        dimension=3,
        normalize_embeddings=True,
        library_name="sentence-transformers",
        library_version="5.7.0",
        query_instruction="Instruct: {query}",
    )


def _config() -> IndexConfig:
    parser = {"version": "parser-v1"}
    chunk = {"hash": "b" * 64}
    tokenizer = {"revision": "revision-1"}
    embedding = _embedding_snapshot().as_config()
    rrf = {"k": 60}
    return IndexConfig(
        config_hash=full_index_config_hash(
            parser_config=parser,
            chunk_config=chunk,
            tokenizer_config=tokenizer,
            embedding_config=embedding,
            rrf_config=rrf,
        ),
        parser_config=parser,
        chunk_config=chunk,
        tokenizer_config=tokenizer,
        embedding_config=embedding,
        rrf_config=rrf,
    )


class _Provider:
    embedding_config_snapshot = _embedding_snapshot()
    descriptor = embedding_config_snapshot.descriptor

    @staticmethod
    def is_ready() -> bool:
        return True

    @staticmethod
    def embed_query(query: str) -> tuple[float, ...]:
        raise AssertionError("not used")

    @staticmethod
    def embed_documents(documents: object) -> tuple[tuple[float, ...], ...]:
        raise AssertionError("not used")


class _Logger:
    def __init__(self) -> None:
        self.events: list[ActivationAuditEvent] = []

    def write(self, event: ActivationAuditEvent) -> None:
        self.events.append(event)


def _service(
    sessions: sessionmaker[Session],
    logger: _Logger,
    repository_type: type[PostgresActivationRepository] = PostgresActivationRepository,
) -> ActivationService:
    return ActivationService(
        transactions=sessions,
        repository_factory=lambda transaction: repository_type(transaction),
        embedding_provider=_Provider(),
        audit_logger=logger,
        clock=lambda: NOW,
    )


def _run(
    source: SourceProfile,
    config: IndexConfig,
    status: IndexRunStatus,
    ordinal: int,
) -> IndexRun:
    return IndexRun(
        source_profile_id=source.id,
        index_config_id=config.id,
        commit_sha=f"{ordinal:x}" * 40,
        status=status.value,
        indexed_at=NOW - timedelta(days=4 - ordinal),
        activated_at=(
            NOW - timedelta(days=4 - ordinal)
            if status
            in {
                IndexRunStatus.ACTIVE,
                IndexRunStatus.PREVIOUS,
                IndexRunStatus.ARCHIVED,
            }
            else None
        ),
        stats={"occurrence_count": 0},
    )


def _seed_runs(
    session: Session,
    statuses: tuple[IndexRunStatus, ...],
    *,
    source_key: str | None = None,
) -> tuple[SourceProfile, IndexConfig, tuple[IndexRun, ...]]:
    source = SourceProfile(source_key=source_key or f"source-{uuid4().hex}")
    candidate_config = _config()
    config = session.scalar(
        select(IndexConfig).where(
            IndexConfig.config_hash == candidate_config.config_hash
        )
    )
    if config is None:
        config = candidate_config
        session.add(config)
    session.add(source)
    session.flush()
    runs = tuple(
        _run(source, config, status, index + 1) for index, status in enumerate(statuses)
    )
    session.add_all(runs)
    session.flush()
    active = next(
        (run for run in runs if run.status == IndexRunStatus.ACTIVE.value), None
    )
    source.active_index_run_id = active.id if active is not None else None
    session.flush()
    return source, config, runs


def _add_occurrence(
    session: Session,
    run: IndexRun,
    source_text: str,
    path: str,
    *,
    content: DocumentContent | None = None,
) -> DocumentContent:
    stored = content or DocumentContent(
        content_hash=hashlib.sha256(source_text.encode()).hexdigest(),
        content=source_text,
        byte_size=len(source_text.encode()),
    )
    if content is None:
        session.add(stored)
        session.flush()
    session.add(
        DocumentOccurrence(
            run_id=run.id,
            content_id=stored.id,
            source_path=path,
            version_scope="current",
            decision_state="confirmed",
            owner_domain="docs",
        )
    )
    run.stats = {"occurrence_count": int(run.stats["occurrence_count"]) + 1}
    session.flush()
    return stored


def _add_artifacts(
    session: Session,
    content: DocumentContent,
) -> tuple[UUID, UUID, UUID, UUID]:
    expected_section = ParsedSection(
        ordinal=0,
        parent_ordinal=None,
        level=0,
        heading=None,
        heading_path=(),
        body=content.content,
        line_start=1,
        line_end=1,
        blocks=(),
    )
    expected_chunk = ChunkDraft(
        ordinal=0,
        raw_text=content.content,
        search_text=content.content,
        token_count=1,
        line_start=1,
        line_end=1,
        chunk_hash=hashlib.sha256(content.content.encode()).hexdigest(),
    )
    manifest = parse_artifact_manifest(
        (expected_section,),
        (expected_chunk,),
        (0,),
    )
    parse = DocumentParse(
        content_id=content.id,
        parser_version="parser-v1",
        chunk_config_hash="b" * 64,
        section_count=manifest.section_count,
        chunk_count=manifest.chunk_count,
        artifact_hash=manifest.artifact_hash,
    )
    session.add(parse)
    session.flush()
    section = Section(
        parse_id=parse.id,
        ordinal=0,
        level=0,
        heading=None,
        heading_path=[],
        body=content.content,
        line_start=1,
        line_end=1,
    )
    session.add(section)
    session.flush()
    chunk = Chunk(
        section_id=section.id,
        ordinal=0,
        raw_text=content.content,
        search_text=content.content,
        token_count=1,
        line_start=1,
        line_end=1,
        chunk_hash=hashlib.sha256(content.content.encode()).hexdigest(),
    )
    session.add(chunk)
    session.flush()
    embedding = ChunkEmbedding(
        chunk_id=chunk.id,
        embedding_config_hash=document_embedding_config_hash(
            _embedding_snapshot().as_config()
        ),
        model_name="test/model",
        model_revision="revision-1",
        dimension=3,
        embedding=[1.0, 0.0, 0.0],
    )
    session.add(embedding)
    session.flush()
    return parse.id, section.id, chunk.id, embedding.id


def test_first_and_second_activation_update_pointer_and_statuses_atomically(
    activation_sessions: sessionmaker[Session],
) -> None:
    logger = _Logger()
    with activation_sessions.begin() as session:
        source, _, runs = _seed_runs(
            session, (IndexRunStatus.READY, IndexRunStatus.READY)
        )
        source_key, first_id, second_id = source.source_key, runs[0].id, runs[1].id

    first = _service(activation_sessions, logger).activate(
        source_key=source_key,
        run_id=first_id,
        actor="admin",
    )
    second = _service(activation_sessions, logger).activate(
        source_key=source_key,
        run_id=second_id,
        actor="admin",
    )

    with activation_sessions() as session:
        source = session.scalar(
            select(SourceProfile).where(SourceProfile.source_key == source_key)
        )
        stored_first = session.get(IndexRun, first_id)
        stored_second = session.get(IndexRun, second_id)
        assert source is not None and source.active_index_run_id == second_id
        assert stored_first is not None and stored_first.status == "previous"
        assert stored_second is not None and stored_second.status == "active"
        assert stored_first.activated_at == NOW
        assert stored_second.activated_at == NOW
    assert first.from_run_id is None
    assert second.from_run_id == first_id
    assert [event.action for event in logger.events] == ["activate", "activate"]


def test_third_activation_archives_and_prunes_only_unshared_old_search_data(
    activation_sessions: sessionmaker[Session],
) -> None:
    logger = _Logger()
    with activation_sessions.begin() as session:
        source, config, runs = _seed_runs(
            session,
            (IndexRunStatus.PREVIOUS, IndexRunStatus.ACTIVE, IndexRunStatus.READY),
        )
        previous, active, target = runs
        source.active_index_run_id = active.id
        shared = _add_occurrence(session, active, "shared", "docs/active.md")
        _add_occurrence(session, previous, "shared", "docs/shared.md", content=shared)
        unique = _add_occurrence(session, previous, "unique", "docs/unique.md")
        shared_artifacts = _add_artifacts(session, shared)
        unique_artifacts = _add_artifacts(session, unique)
        source_key, previous_id, target_id = source.source_key, previous.id, target.id
        unique_id, shared_id, config_id = unique.id, shared.id, config.id

    _service(activation_sessions, logger).activate(
        source_key=source_key,
        run_id=target_id,
        actor="admin",
    )

    with activation_sessions() as session:
        archived = session.get(IndexRun, previous_id)
        assert archived is not None and archived.status == "archived"
        assert archived.commit_sha and archived.stats == {"occurrence_count": 2}
        assert session.get(IndexConfig, config_id) is not None
        assert (
            session.scalar(
                select(func.count())
                .select_from(DocumentOccurrence)
                .where(DocumentOccurrence.run_id == previous_id)
            )
            == 0
        )
        assert session.get(DocumentContent, unique_id) is None
        assert session.get(DocumentContent, shared_id) is not None
        assert session.get(DocumentParse, unique_artifacts[0]) is None
        assert session.get(Section, unique_artifacts[1]) is None
        assert session.get(Chunk, unique_artifacts[2]) is None
        assert session.get(ChunkEmbedding, unique_artifacts[3]) is None
        assert session.get(DocumentParse, shared_artifacts[0]) is not None
        assert session.get(Section, shared_artifacts[1]) is not None
        assert session.get(Chunk, shared_artifacts[2]) is not None
        assert session.get(ChunkEmbedding, shared_artifacts[3]) is not None


def test_prune_batches_more_than_one_thousand_contents_without_losing_shared_data(
    activation_sessions: sessionmaker[Session],
) -> None:
    logger = _Logger()
    with activation_sessions.begin() as session:
        source, config, runs = _seed_runs(
            session,
            (IndexRunStatus.PREVIOUS, IndexRunStatus.ACTIVE, IndexRunStatus.READY),
        )
        previous, active, target = runs
        source.active_index_run_id = active.id
        orphan_sources = tuple(f"orphan-{index}" for index in range(1_001))
        orphans = tuple(
            DocumentContent(
                content_hash=hashlib.sha256(value.encode()).hexdigest(),
                content=value,
                byte_size=len(value.encode()),
            )
            for value in orphan_sources
        )
        session.add_all(orphans)
        session.flush()
        session.add_all(
            DocumentOccurrence(
                run_id=previous.id,
                content_id=content.id,
                source_path=f"docs/orphan-{index}.md",
                version_scope="current",
                decision_state="confirmed",
                owner_domain="docs",
            )
            for index, content in enumerate(orphans)
        )
        previous.stats = {"occurrence_count": len(orphans)}
        shared = _add_occurrence(
            session,
            active,
            "shared-boundary",
            "docs/active-shared.md",
        )
        _add_occurrence(
            session,
            previous,
            "shared-boundary",
            "docs/previous-shared.md",
            content=shared,
        )
        orphan_artifacts = _add_artifacts(session, orphans[0])
        shared_artifacts = _add_artifacts(session, shared)
        source_key, target_id, previous_id = source.source_key, target.id, previous.id
        config_id, shared_id = config.id, shared.id
        orphan_ids = tuple(content.id for content in orphans)

    delete_batches = 0

    def count_content_delete(
        connection: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: object,
    ) -> None:
        nonlocal delete_batches
        if statement.lstrip().startswith("DELETE FROM document_contents"):
            delete_batches += 1

    with activation_sessions() as session:
        bind = session.get_bind()
    event.listen(bind, "before_cursor_execute", count_content_delete)
    try:
        _service(activation_sessions, logger).activate(
            source_key=source_key,
            run_id=target_id,
            actor="admin",
        )
    finally:
        event.remove(bind, "before_cursor_execute", count_content_delete)

    assert delete_batches == 2
    with activation_sessions() as session:
        archived = session.get(IndexRun, previous_id)
        assert archived is not None and archived.status == "archived"
        assert archived.stats == {"occurrence_count": 1_002}
        assert session.get(IndexConfig, config_id) is not None
        assert (
            session.scalar(
                select(func.count())
                .select_from(DocumentContent)
                .where(DocumentContent.id.in_(orphan_ids))
            )
            == 0
        )
        assert session.get(DocumentContent, shared_id) is not None
        assert session.get(DocumentParse, orphan_artifacts[0]) is None
        assert session.get(Section, orphan_artifacts[1]) is None
        assert session.get(Chunk, orphan_artifacts[2]) is None
        assert session.get(ChunkEmbedding, orphan_artifacts[3]) is None
        assert session.get(DocumentParse, shared_artifacts[0]) is not None
        assert session.get(Section, shared_artifacts[1]) is not None
        assert session.get(Chunk, shared_artifacts[2]) is not None
        assert session.get(ChunkEmbedding, shared_artifacts[3]) is not None


@pytest.mark.parametrize(
    "status",
    [IndexRunStatus.BUILDING, IndexRunStatus.FAILED, IndexRunStatus.ARCHIVED],
)
def test_non_ready_target_is_rejected_without_mutation(
    activation_sessions: sessionmaker[Session],
    status: IndexRunStatus,
) -> None:
    logger = _Logger()
    with activation_sessions.begin() as session:
        source, _, runs = _seed_runs(session, (status,))
        source_key, target_id = source.source_key, runs[0].id

    with pytest.raises(ActivationError):
        _service(activation_sessions, logger).activate(
            source_key=source_key,
            run_id=target_id,
            actor="admin",
        )

    with activation_sessions() as session:
        assert session.get(IndexRun, target_id).status == status.value  # type: ignore[union-attr]
    assert logger.events == []


def test_target_from_another_source_is_rejected(
    activation_sessions: sessionmaker[Session],
) -> None:
    logger = _Logger()
    with activation_sessions.begin() as session:
        source, _, _ = _seed_runs(session, ())
        _, _, foreign = _seed_runs(session, (IndexRunStatus.READY,))
        source_key, foreign_id = source.source_key, foreign[0].id

    with pytest.raises(ActivationError):
        _service(activation_sessions, logger).activate(
            source_key=source_key,
            run_id=foreign_id,
            actor="admin",
        )


def test_activation_rejects_previous_without_active_as_inconsistent(
    activation_sessions: sessionmaker[Session],
) -> None:
    logger = _Logger()
    with activation_sessions.begin() as session:
        source, _, runs = _seed_runs(
            session,
            (IndexRunStatus.PREVIOUS, IndexRunStatus.READY),
        )
        previous, target = runs
        source_key, previous_id, target_id = source.source_key, previous.id, target.id

    with pytest.raises(ActivationError, match="inconsistent"):
        _service(activation_sessions, logger).activate(
            source_key=source_key,
            run_id=target_id,
            actor="admin",
        )

    with activation_sessions() as session:
        assert session.get(IndexRun, previous_id).status == "previous"  # type: ignore[union-attr]
        assert session.get(IndexRun, target_id).status == "ready"  # type: ignore[union-attr]
        assert (
            session.scalar(
                select(SourceProfile.active_index_run_id).where(
                    SourceProfile.source_key == source_key
                )
            )
            is None
        )
    assert logger.events == []


@pytest.mark.parametrize("status", [IndexRunStatus.ACTIVE, IndexRunStatus.PREVIOUS])
def test_partial_unique_indexes_reject_second_active_and_previous(
    activation_sessions: sessionmaker[Session],
    status: IndexRunStatus,
) -> None:
    with activation_sessions.begin() as session:
        source, config, _ = _seed_runs(session, (status,))
        source_id, config_id = source.id, config.id

    with activation_sessions() as session:
        source = session.get(SourceProfile, source_id)
        config = session.get(IndexConfig, config_id)
        assert source is not None and config is not None
        duplicate = _run(source, config, status, 9)
        savepoint = session.begin_nested()
        session.add(duplicate)
        with pytest.raises(IntegrityError):
            session.flush()
        savepoint.rollback()


def test_status_pointer_and_prune_failure_roll_back_together(
    activation_sessions: sessionmaker[Session],
) -> None:
    class FailingPrune(PostgresActivationRepository):
        def _prune_archived_run(
            self, run_id: UUID, candidate_content_ids: tuple[UUID, ...]
        ) -> None:
            raise RuntimeError("prune failed")

    logger = _Logger()
    with activation_sessions.begin() as session:
        source, _, runs = _seed_runs(
            session,
            (IndexRunStatus.PREVIOUS, IndexRunStatus.ACTIVE, IndexRunStatus.READY),
        )
        previous, active, target = runs
        source.active_index_run_id = active.id
        source_key = source.source_key
        ids = previous.id, active.id, target.id

    with pytest.raises(RuntimeError, match="prune failed"):
        _service(activation_sessions, logger, FailingPrune).activate(
            source_key=source_key,
            run_id=ids[2],
            actor="admin",
        )

    with activation_sessions() as session:
        assert [session.get(IndexRun, run_id).status for run_id in ids] == [  # type: ignore[union-attr]
            "previous",
            "active",
            "ready",
        ]
        assert (
            session.scalar(
                select(SourceProfile.active_index_run_id).where(
                    SourceProfile.source_key == source_key
                )
            )
            == ids[1]
        )
    assert logger.events == []


def test_concurrent_lifecycle_loser_does_not_wait_then_another_source_proceeds(
    activation_sessions: sessionmaker[Session],
) -> None:
    logger = _Logger()
    with activation_sessions.begin() as session:
        first_source, _, first_runs = _seed_runs(session, (IndexRunStatus.READY,))
        second_source, _, second_runs = _seed_runs(session, (IndexRunStatus.READY,))
        first_key, first_id = first_source.source_key, first_runs[0].id
        second_key, second_id = second_source.source_key, second_runs[0].id
        first_source_id = first_source.id

    with activation_sessions.begin() as locking_session:
        first_repository = PostgresActivationRepository(locking_session)
        assert first_repository._lock_source(first_key).id == first_source_id
        with activation_sessions.begin() as losing_session:
            losing_session.execute(text("SET LOCAL lock_timeout = '100ms'"))
            with pytest.raises(ActivationError, match="in progress"):
                PostgresActivationRepository(losing_session).activate(
                    first_key,
                    first_id,
                    NOW,
                )
        _service(activation_sessions, logger).activate(
            source_key=second_key,
            run_id=second_id,
            actor="admin",
        )
