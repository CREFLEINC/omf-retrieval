"""PostgreSQL integration tests for guarded previous-generation rollback."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session, sessionmaker

from omf_retrieval.application.indexing.activation import (
    ActivationAuditEvent,
    ActivationError,
    ActivationService,
    PostCommitAuditError,
    RollbackCandidate,
    RollbackReadinessError,
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
    "task9_rollback_database_test_utils",
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
def rollback_sessions(
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


def _snapshot(**changes: object) -> EmbeddingConfigSnapshot:
    values: dict[str, object] = {
        "provider": "sentence-transformers",
        "model_name": "test/model",
        "revision": "revision-1",
        "dimension": 3,
        "normalize_embeddings": True,
        "library_name": "sentence-transformers",
        "library_version": "5.7.0",
        "query_instruction": "Instruct: {query}",
    }
    values.update(changes)
    return EmbeddingConfigSnapshot(**values)  # type: ignore[arg-type]


def _config(snapshot: EmbeddingConfigSnapshot | None = None) -> IndexConfig:
    parser = {"version": "parser-v1"}
    chunk = {"hash": "b" * 64}
    tokenizer = {"revision": "revision-1"}
    embedding = (snapshot or _snapshot()).as_config()
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
    def __init__(
        self,
        snapshot: EmbeddingConfigSnapshot | None = None,
        ready: object = True,
    ) -> None:
        self.embedding_config_snapshot = snapshot or _snapshot()
        self.descriptor = self.embedding_config_snapshot.descriptor
        self.ready = ready

    def is_ready(self) -> bool:
        if isinstance(self.ready, BaseException):
            raise self.ready
        return self.ready  # type: ignore[return-value]

    def embed_query(self, query: str) -> tuple[float, ...]:
        raise AssertionError("not used")

    def embed_documents(self, documents: object) -> tuple[tuple[float, ...], ...]:
        raise AssertionError("not used")


class _Logger:
    def __init__(self, failure: bool = False) -> None:
        self.failure = failure
        self.events: list[ActivationAuditEvent] = []

    def write(self, event: ActivationAuditEvent) -> None:
        if self.failure:
            raise RuntimeError("token /host/path")
        self.events.append(event)


def _service(
    sessions: sessionmaker[Session],
    provider: _Provider,
    logger: _Logger,
    repository_type: type[PostgresActivationRepository] = PostgresActivationRepository,
    *,
    now: datetime = NOW,
) -> ActivationService:
    return ActivationService(
        transactions=sessions,
        repository_factory=lambda transaction: repository_type(transaction),
        embedding_provider=provider,
        audit_logger=logger,
        clock=lambda: now,
    )


def _seed_pair(
    session: Session,
    *,
    active_status: IndexRunStatus | None = IndexRunStatus.ACTIVE,
    previous_status: IndexRunStatus | None = IndexRunStatus.PREVIOUS,
    target_config: IndexConfig | None = None,
) -> tuple[str, UUID | None, UUID | None]:
    source = SourceProfile(source_key=f"source-{uuid4().hex}")
    active_config = _config()
    previous_config = target_config or active_config
    session.add_all((source, active_config))
    if previous_config is not active_config:
        session.add(previous_config)
    session.flush()
    active = (
        IndexRun(
            source_profile_id=source.id,
            index_config_id=active_config.id,
            commit_sha="a" * 40,
            status=active_status.value,
            indexed_at=NOW - timedelta(days=2),
            activated_at=NOW - timedelta(days=2),
            stats={
                "occurrence_count": 0,
                "unique_content_count": 0,
                "empty_document_count": 0,
            },
        )
        if active_status is not None
        else None
    )
    previous = (
        IndexRun(
            source_profile_id=source.id,
            index_config_id=previous_config.id,
            commit_sha="b" * 40,
            status=previous_status.value,
            indexed_at=NOW - timedelta(days=3),
            activated_at=(
                NOW - timedelta(days=3)
                if previous_status
                in {
                    IndexRunStatus.ACTIVE,
                    IndexRunStatus.PREVIOUS,
                    IndexRunStatus.ARCHIVED,
                }
                else None
            ),
            stats={
                "occurrence_count": 0,
                "unique_content_count": 0,
                "empty_document_count": 0,
            },
        )
        if previous_status is not None
        else None
    )
    session.add_all(tuple(run for run in (active, previous) if run is not None))
    session.flush()
    source.active_index_run_id = (
        active.id if active_status is IndexRunStatus.ACTIVE else None
    )
    session.flush()
    return (
        source.source_key,
        active.id if active else None,
        previous.id if previous else None,
    )


def _add_target_document(
    session: Session,
    target: IndexRun,
    *,
    source_text: str,
    path: str,
    artifact_stage: str,
) -> None:
    content = DocumentContent(
        content_hash=hashlib.sha256(source_text.encode()).hexdigest(),
        content=source_text,
        byte_size=len(source_text.encode()),
    )
    session.add(content)
    session.flush()
    session.add(
        DocumentOccurrence(
            run_id=target.id,
            content_id=content.id,
            source_path=path,
            version_scope="current",
            decision_state="confirmed",
            owner_domain="docs",
        )
    )
    stats = dict(target.stats)
    stats["occurrence_count"] += 1
    stats["unique_content_count"] += 1
    if not source_text.strip():
        stats["empty_document_count"] += 1
    target.stats = stats
    session.flush()
    if artifact_stage == "occurrence":
        return
    expected_section = ParsedSection(
        ordinal=0,
        parent_ordinal=None,
        level=0,
        heading=None,
        heading_path=(),
        body=source_text,
        line_start=1,
        line_end=1,
        blocks=(),
    )
    expected_chunk = ChunkDraft(
        ordinal=0,
        raw_text=source_text,
        search_text=source_text,
        token_count=1,
        line_start=1,
        line_end=1,
        chunk_hash=hashlib.sha256(source_text.encode()).hexdigest(),
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
    if artifact_stage == "parse":
        return
    section = Section(
        parse_id=parse.id,
        ordinal=0,
        level=0,
        heading=None,
        heading_path=[],
        body=source_text,
        line_start=1,
        line_end=1,
    )
    session.add(section)
    session.flush()
    if artifact_stage == "section":
        return
    chunk = Chunk(
        section_id=section.id,
        ordinal=0,
        raw_text=source_text,
        search_text=source_text,
        token_count=1,
        line_start=1,
        line_end=1,
        chunk_hash=hashlib.sha256(source_text.encode()).hexdigest(),
    )
    session.add(chunk)
    session.flush()
    if artifact_stage == "chunk":
        return
    session.add(
        ChunkEmbedding(
            chunk_id=chunk.id,
            embedding_config_hash=document_embedding_config_hash(
                _snapshot().as_config()
            ),
            model_name="test/model",
            model_revision="revision-1",
            dimension=3,
            embedding=[1.0, 0.0, 0.0],
        )
    )
    session.flush()


def _add_exact_target_document(
    session: Session,
    target: IndexRun,
    *,
    source_text: str,
    path: str,
    section_bodies: tuple[str, ...],
    chunked_sections: frozenset[int],
) -> tuple[UUID, tuple[UUID, ...], tuple[UUID, ...]]:
    content = DocumentContent(
        content_hash=hashlib.sha256(source_text.encode()).hexdigest(),
        content=source_text,
        byte_size=len(source_text.encode()),
    )
    session.add(content)
    session.flush()
    session.add(
        DocumentOccurrence(
            run_id=target.id,
            content_id=content.id,
            source_path=path,
            version_scope="current",
            decision_state="confirmed",
            owner_domain="docs",
        )
    )
    stats = dict(target.stats)
    stats["occurrence_count"] += 1
    stats["unique_content_count"] += 1
    if not source_text.strip():
        stats["empty_document_count"] += 1
    target.stats = stats
    expected_sections = tuple(
        ParsedSection(
            ordinal=ordinal,
            parent_ordinal=None,
            level=1,
            heading=f"H{ordinal}",
            heading_path=(f"H{ordinal}",),
            body=body,
            line_start=ordinal + 1,
            line_end=ordinal + 1,
            blocks=(),
        )
        for ordinal, body in enumerate(section_bodies)
    )
    expected_chunks = tuple(
        ChunkDraft(
            ordinal=0,
            raw_text=body,
            search_text=f"H{ordinal}\n{body}",
            token_count=max(1, len(body)),
            line_start=ordinal + 1,
            line_end=ordinal + 1,
            chunk_hash=hashlib.sha256(body.encode()).hexdigest(),
        )
        for ordinal, body in enumerate(section_bodies)
        if body.strip()
    )
    expected_owners = tuple(
        ordinal for ordinal, body in enumerate(section_bodies) if body.strip()
    )
    manifest = parse_artifact_manifest(
        expected_sections,
        expected_chunks,
        expected_owners,
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
    section_ids: list[UUID] = []
    chunk_ids: list[UUID] = []
    for ordinal, body in enumerate(section_bodies):
        section = Section(
            parse_id=parse.id,
            ordinal=ordinal,
            level=1,
            heading=f"H{ordinal}",
            heading_path=[f"H{ordinal}"],
            body=body,
            line_start=ordinal + 1,
            line_end=ordinal + 1,
        )
        session.add(section)
        session.flush()
        section_ids.append(section.id)
        if ordinal not in chunked_sections:
            continue
        chunk = Chunk(
            section_id=section.id,
            ordinal=0,
            raw_text=body,
            search_text=f"H{ordinal}\n{body}",
            token_count=max(1, len(body)),
            line_start=ordinal + 1,
            line_end=ordinal + 1,
            chunk_hash=hashlib.sha256(body.encode()).hexdigest(),
        )
        session.add(chunk)
        session.flush()
        chunk_ids.append(chunk.id)
        session.add(
            ChunkEmbedding(
                chunk_id=chunk.id,
                embedding_config_hash=document_embedding_config_hash(
                    _snapshot().as_config()
                ),
                model_name="test/model",
                model_revision="revision-1",
                dimension=3,
                embedding=[1.0, 0.0, 0.0],
            )
        )
        session.flush()
    return parse.id, tuple(section_ids), tuple(chunk_ids)


def test_rollback_exchanges_active_previous_and_allows_roll_forward(
    rollback_sessions: sessionmaker[Session],
) -> None:
    provider, logger = _Provider(), _Logger()
    with rollback_sessions.begin() as session:
        source_key, active_id, previous_id = _seed_pair(session)
    assert active_id is not None and previous_id is not None

    first = _service(rollback_sessions, provider, logger).rollback(
        source_key=source_key,
        actor="admin",
    )
    second_time = NOW + timedelta(minutes=1)
    second = _service(
        rollback_sessions,
        provider,
        logger,
        now=second_time,
    ).rollback(source_key=source_key, actor="admin")

    assert (first.from_run_id, first.to_run_id) == (active_id, previous_id)
    assert (second.from_run_id, second.to_run_id) == (previous_id, active_id)
    with rollback_sessions() as session:
        source = session.scalar(
            select(SourceProfile).where(SourceProfile.source_key == source_key)
        )
        active = session.get(IndexRun, active_id)
        previous = session.get(IndexRun, previous_id)
        assert source is not None and source.active_index_run_id == active_id
        assert active is not None and active.status == "active"
        assert active.activated_at == second_time
        assert previous is not None and previous.status == "previous"
    assert [event.action for event in logger.events] == ["rollback", "rollback"]


@pytest.mark.parametrize(
    "provider",
    [
        _Provider(_snapshot(library_name="other")),
        _Provider(_snapshot(query_instruction="Changed: {query}")),
        _Provider(ready=False),
        _Provider(ready=RuntimeError("cache /host/path")),
    ],
)
def test_readiness_mismatch_or_failure_has_zero_database_mutation(
    rollback_sessions: sessionmaker[Session],
    provider: _Provider,
) -> None:
    logger = _Logger()
    with rollback_sessions.begin() as session:
        source_key, active_id, previous_id = _seed_pair(session)

    with pytest.raises(RollbackReadinessError):
        _service(rollback_sessions, provider, logger).rollback(
            source_key=source_key,
            actor="admin",
        )

    with rollback_sessions() as session:
        source = session.scalar(
            select(SourceProfile).where(SourceProfile.source_key == source_key)
        )
        assert source is not None and source.active_index_run_id == active_id
        assert session.get(IndexRun, active_id).status == "active"  # type: ignore[union-attr]
        assert session.get(IndexRun, previous_id).status == "previous"  # type: ignore[union-attr]
    assert logger.events == []


@pytest.mark.parametrize(
    ("active_status", "previous_status"),
    [
        (None, IndexRunStatus.PREVIOUS),
        (IndexRunStatus.ACTIVE, None),
        (IndexRunStatus.ACTIVE, IndexRunStatus.ARCHIVED),
        (IndexRunStatus.ACTIVE, IndexRunStatus.READY),
    ],
)
def test_missing_or_wrong_previous_lifecycle_is_a_domain_error(
    rollback_sessions: sessionmaker[Session],
    active_status: IndexRunStatus | None,
    previous_status: IndexRunStatus | None,
) -> None:
    logger = _Logger()
    with rollback_sessions.begin() as session:
        source_key, _, _ = _seed_pair(
            session,
            active_status=active_status,
            previous_status=previous_status,
        )

    with pytest.raises(ActivationError):
        _service(rollback_sessions, _Provider(), logger).rollback(
            source_key=source_key,
            actor="admin",
        )
    assert logger.events == []


@pytest.mark.parametrize(
    "stat_name",
    ["occurrence_count", "unique_content_count", "empty_document_count"],
)
def test_incomplete_target_metadata_has_zero_mutation(
    rollback_sessions: sessionmaker[Session],
    stat_name: str,
) -> None:
    logger = _Logger()
    with rollback_sessions.begin() as session:
        source_key, active_id, previous_id = _seed_pair(session)
        assert previous_id is not None
        previous = session.get(IndexRun, previous_id)
        assert previous is not None
        mismatched = dict(previous.stats)
        mismatched[stat_name] = 1
        previous.stats = mismatched

    with pytest.raises(ActivationError, match="incomplete"):
        _service(rollback_sessions, _Provider(), logger).rollback(
            source_key=source_key,
            actor="admin",
        )

    with rollback_sessions() as session:
        assert (
            session.scalar(
                select(SourceProfile.active_index_run_id).where(
                    SourceProfile.source_key == source_key
                )
            )
            == active_id
        )


@pytest.mark.parametrize("artifact_stage", ["occurrence", "parse", "section", "chunk"])
def test_incomplete_target_structure_has_zero_mutation(
    rollback_sessions: sessionmaker[Session],
    artifact_stage: str,
) -> None:
    logger = _Logger()
    with rollback_sessions.begin() as session:
        source_key, active_id, previous_id = _seed_pair(session)
        assert previous_id is not None
        previous = session.get(IndexRun, previous_id)
        assert previous is not None
        _add_target_document(
            session,
            previous,
            source_text="nonempty",
            path=f"docs/{artifact_stage}.md",
            artifact_stage=artifact_stage,
        )

    with pytest.raises(ActivationError, match="artifacts are incomplete"):
        _service(rollback_sessions, _Provider(), logger).rollback(
            source_key=source_key,
            actor="admin",
        )

    with rollback_sessions() as session:
        assert (
            session.scalar(
                select(SourceProfile.active_index_run_id).where(
                    SourceProfile.source_key == source_key
                )
            )
            == active_id
        )


def test_unicode_whitespace_only_target_needs_no_search_artifacts(
    rollback_sessions: sessionmaker[Session],
) -> None:
    with rollback_sessions.begin() as session:
        source_key, _, previous_id = _seed_pair(session)
        assert previous_id is not None
        previous = session.get(IndexRun, previous_id)
        assert previous is not None
        _add_target_document(
            session,
            previous,
            source_text="\t\u2003\n",
            path="docs/unicode-empty.md",
            artifact_stage="occurrence",
        )

    result = _service(rollback_sessions, _Provider(), _Logger()).rollback(
        source_key=source_key,
        actor="admin",
    )

    assert result.to_run_id == previous_id


@pytest.mark.parametrize(
    ("case_name", "source_text", "section_bodies", "chunked_sections"),
    [
        ("empty-corpus", None, (), frozenset()),
        ("heading-only", "# H\n", ("",), frozenset()),
        ("blank-body", "# H\n \t\n", (" \t\n",), frozenset()),
        (
            "html-comment-only",
            "# H\n<!-- only -->\n",
            ("<!-- only -->\n",),
            frozenset({0}),
        ),
        ("ordinary-body", "# H\nbody\n", ("body\n",), frozenset({0})),
    ],
)
def test_section_level_artifact_readiness_matches_chunker_contract(
    rollback_sessions: sessionmaker[Session],
    case_name: str,
    source_text: str | None,
    section_bodies: tuple[str, ...],
    chunked_sections: frozenset[int],
) -> None:
    with rollback_sessions.begin() as session:
        source_key, _, previous_id = _seed_pair(session)
        assert previous_id is not None
        previous = session.get(IndexRun, previous_id)
        assert previous is not None
        if source_text is not None:
            _add_exact_target_document(
                session,
                previous,
                source_text=source_text,
                path=f"docs/{case_name}.md",
                section_bodies=section_bodies,
                chunked_sections=chunked_sections,
            )

    result = _service(rollback_sessions, _Provider(), _Logger()).rollback(
        source_key=source_key,
        actor="admin",
    )

    assert result.to_run_id == previous_id


def test_empty_document_with_unexpected_parse_is_rejected(
    rollback_sessions: sessionmaker[Session],
) -> None:
    with rollback_sessions.begin() as session:
        source_key, active_id, previous_id = _seed_pair(session)
        assert previous_id is not None
        previous = session.get(IndexRun, previous_id)
        assert previous is not None
        _add_exact_target_document(
            session,
            previous,
            source_text=" \t\n",
            path="docs/empty-with-parse.md",
            section_bodies=(" \t\n",),
            chunked_sections=frozenset(),
        )

    with pytest.raises(ActivationError, match="artifacts are incomplete"):
        _service(rollback_sessions, _Provider(), _Logger()).rollback(
            source_key=source_key,
            actor="admin",
        )

    with rollback_sessions() as session:
        assert (
            session.scalar(
                select(SourceProfile.active_index_run_id).where(
                    SourceProfile.source_key == source_key
                )
            )
            == active_id
        )


def test_one_missing_chunk_is_not_hidden_by_another_complete_section(
    rollback_sessions: sessionmaker[Session],
) -> None:
    with rollback_sessions.begin() as session:
        source_key, active_id, previous_id = _seed_pair(session)
        assert previous_id is not None
        previous = session.get(IndexRun, previous_id)
        assert previous is not None
        _add_exact_target_document(
            session,
            previous,
            source_text="# A\none\n# B\ntwo\n",
            path="docs/two-sections.md",
            section_bodies=("one\n", "two\n"),
            chunked_sections=frozenset({0}),
        )

    with pytest.raises(ActivationError, match="artifacts are incomplete"):
        _service(rollback_sessions, _Provider(), _Logger()).rollback(
            source_key=source_key,
            actor="admin",
        )

    with rollback_sessions() as session:
        assert (
            session.scalar(
                select(SourceProfile.active_index_run_id).where(
                    SourceProfile.source_key == source_key
                )
            )
            == active_id
        )


@pytest.mark.parametrize(
    "tamper",
    [
        "delete-section",
        "delete-chunk",
        "mutate-section",
        "mutate-chunk",
        "change-parent",
        "stored-manifest",
    ],
)
def test_actual_parse_artifact_tamper_has_zero_rollback_mutation(
    rollback_sessions: sessionmaker[Session],
    tamper: str,
) -> None:
    """Counts plus hash reject missing and same-count persisted-row corruption."""
    with rollback_sessions.begin() as session:
        source_key, active_id, previous_id = _seed_pair(session)
        assert previous_id is not None
        previous = session.get(IndexRun, previous_id)
        assert previous is not None
        parse_id, section_ids, chunk_ids = _add_exact_target_document(
            session,
            previous,
            source_text="# A\none\n# B\ntwo\n",
            path=f"docs/tamper-{tamper}.md",
            section_bodies=("one\n", "two\n"),
            chunked_sections=frozenset({0, 1}),
        )
        if tamper == "delete-section":
            section = session.get(Section, section_ids[1])
            assert section is not None
            session.delete(section)
        elif tamper == "delete-chunk":
            chunk = session.get(Chunk, chunk_ids[1])
            assert chunk is not None
            session.delete(chunk)
        elif tamper == "mutate-section":
            section = session.get(Section, section_ids[1])
            assert section is not None
            section.body = "changed\n"
        elif tamper == "mutate-chunk":
            chunk = session.get(Chunk, chunk_ids[1])
            assert chunk is not None
            chunk.raw_text = "changed\n"
        elif tamper == "change-parent":
            section = session.get(Section, section_ids[1])
            assert section is not None
            section.parent_section_id = section_ids[0]
        else:
            parse = session.get(DocumentParse, parse_id)
            assert parse is not None
            parse.artifact_hash = "f" * 64

    with pytest.raises(ActivationError, match="artifacts are incomplete"):
        _service(rollback_sessions, _Provider(), _Logger()).rollback(
            source_key=source_key,
            actor="admin",
        )

    with rollback_sessions() as session:
        assert (
            session.scalar(
                select(SourceProfile.active_index_run_id).where(
                    SourceProfile.source_key == source_key
                )
            )
            == active_id
        )
        assert session.get(IndexRun, active_id).status == "active"  # type: ignore[union-attr]
        assert session.get(IndexRun, previous_id).status == "previous"  # type: ignore[union-attr]


def test_artifact_validation_uses_constant_query_count(
    rollback_sessions: sessionmaker[Session],
) -> None:
    counts: list[int] = []
    with rollback_sessions.begin() as session:
        _, _, previous_id = _seed_pair(session)
        assert previous_id is not None
        previous = session.get(IndexRun, previous_id)
        assert previous is not None
        config = session.get(IndexConfig, previous.index_config_id)
        assert config is not None
        for document_count in (1, 20):
            for index in range(int(previous.stats["occurrence_count"]), document_count):
                source_text = f"# A\nfirst {index}\n# B\nsecond {index}\n"
                _add_exact_target_document(
                    session,
                    previous,
                    source_text=source_text,
                    path=f"docs/{document_count}-{index}.md",
                    section_bodies=(f"first {index}\n", f"second {index}\n"),
                    chunked_sections=frozenset({0, 1}),
                )
            statement_count = 0

            def count_statement(*args: object, **kwargs: object) -> None:
                nonlocal statement_count
                statement_count += 1

            bind = session.get_bind()
            event.listen(bind, "before_cursor_execute", count_statement)
            try:
                PostgresActivationRepository(session)._assert_target_artifacts(
                    previous,
                    config,
                )
            finally:
                event.remove(bind, "before_cursor_execute", count_statement)
            counts.append(statement_count)

    assert counts == [3, 3]


def test_transaction_failure_rolls_back_pointer_and_both_statuses(
    rollback_sessions: sessionmaker[Session],
) -> None:
    class FailingRollback(PostgresActivationRepository):
        def rollback(
            self, candidate: RollbackCandidate, occurred_at: datetime
        ) -> object:
            super().rollback(candidate, occurred_at)
            raise RuntimeError("after mutation")

    logger = _Logger()
    with rollback_sessions.begin() as session:
        source_key, active_id, previous_id = _seed_pair(session)

    with pytest.raises(RuntimeError, match="after mutation"):
        _service(
            rollback_sessions,
            _Provider(),
            logger,
            FailingRollback,
        ).rollback(source_key=source_key, actor="admin")

    with rollback_sessions() as session:
        assert (
            session.scalar(
                select(SourceProfile.active_index_run_id).where(
                    SourceProfile.source_key == source_key
                )
            )
            == active_id
        )
        assert session.get(IndexRun, active_id).status == "active"  # type: ignore[union-attr]
        assert session.get(IndexRun, previous_id).status == "previous"  # type: ignore[union-attr]
    assert logger.events == []


def test_logger_failure_reports_committed_safe_transition(
    rollback_sessions: sessionmaker[Session],
) -> None:
    with rollback_sessions.begin() as session:
        source_key, active_id, previous_id = _seed_pair(session)
    assert previous_id is not None

    with pytest.raises(PostCommitAuditError) as captured:
        _service(rollback_sessions, _Provider(), _Logger(failure=True)).rollback(
            source_key=source_key,
            actor="admin",
        )

    assert captured.value.committed is True
    assert captured.value.result.to_run_id == previous_id
    assert "token" not in str(captured.value)
    assert "/host/path" not in str(captured.value)
    with rollback_sessions() as session:
        assert (
            session.scalar(
                select(SourceProfile.active_index_run_id).where(
                    SourceProfile.source_key == source_key
                )
            )
            == previous_id
        )
        assert session.get(IndexRun, active_id).status == "previous"  # type: ignore[union-attr]
