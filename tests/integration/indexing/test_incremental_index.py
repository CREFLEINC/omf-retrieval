"""PostgreSQL integration tests for Task 8 repository and pipeline reuse."""

import hashlib
from collections.abc import Iterator, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from omf_retrieval.application.indexing.artifact_identity import (
    parse_artifact_manifest,
)
from omf_retrieval.application.indexing.metadata import DocumentRelationSpec
from omf_retrieval.application.indexing.pipeline import TransactionalIndexPipeline
from omf_retrieval.application.indexing.ports import (
    ArchiveFile,
    ChunkDraft,
    ParsedBlock,
    ParsedMarkdown,
    ParsedSection,
    SourceSnapshot,
)
from omf_retrieval.application.indexing.service import IndexResult, IndexService
from omf_retrieval.domain.enums import (
    DecisionState,
    IndexRunStatus,
    OwnerDomain,
    RelationType,
    VersionScope,
)
from omf_retrieval.domain.models import (
    DocumentMetadata,
    EmbeddingDescriptor,
    LineRange,
)
from omf_retrieval.infrastructure.database.models import (
    Chunk,
    ChunkEmbedding,
    DocumentContent,
    DocumentOccurrence,
    DocumentParse,
    DocumentRelation,
    IndexConfig,
    IndexRun,
    Section,
    SourceProfile,
)
from omf_retrieval.infrastructure.database.repositories import (
    PostgresIndexRepository,
    RepositoryInvariantError,
)
from omf_retrieval.infrastructure.database.repository_config import (
    EmbeddingAdapterIdentity,
    document_embedding_config_hash,
    embedding_config_snapshot,
    full_index_config_hash,
)
from omf_retrieval.infrastructure.database.session import (
    create_database_engine,
    create_session_factory,
)

_DATABASE_SUPPORT_SPEC = spec_from_file_location(
    "task8b_database_test_utils",
    Path(__file__).parents[1] / "database" / "database_test_utils.py",
)
assert _DATABASE_SUPPORT_SPEC is not None
assert _DATABASE_SUPPORT_SPEC.loader is not None
_DATABASE_SUPPORT = module_from_spec(_DATABASE_SUPPORT_SPEC)
_DATABASE_SUPPORT_SPEC.loader.exec_module(_DATABASE_SUPPORT)
assert_safe_test_connection = _DATABASE_SUPPORT.assert_safe_test_connection
_test_database_url = _DATABASE_SUPPORT.test_database_url

APPLICATION_TABLE_NAMES = (
    "search_audit_events",
    "client_source_grants",
    "api_clients",
    "document_relations",
    "chunk_embeddings",
    "chunks",
    "sections",
    "document_parses",
    "document_occurrences",
    "document_contents",
    "index_runs",
    "index_configs",
    "source_profiles",
)


def _embedding_descriptor() -> EmbeddingDescriptor:
    return EmbeddingDescriptor("test/model", "revision-1", 3)


def _embedding_adapter_identity() -> EmbeddingAdapterIdentity:
    return EmbeddingAdapterIdentity(
        provider="sentence-transformers",
        normalize_embeddings=True,
        library_name="sentence-transformers",
        library_version="5.7.0",
    )


PARSER_VERSION = "parser-v1"
CHUNK_CONFIG_HASH = "b" * 64
EMBEDDING_CONFIG_HASH = document_embedding_config_hash(
    embedding_config_snapshot(
        _embedding_descriptor(),
        _embedding_adapter_identity(),
        "Instruct: {query}",
    )
)
COMMIT_SHA = "d" * 40
ACTIVATED_AT = datetime(2026, 8, 24, tzinfo=UTC)


@pytest.fixture
def repository_session_factory(
    request: pytest.FixtureRequest,
) -> Iterator[sessionmaker[Session]]:
    """Yield a clean session factory against the isolated migrated database."""
    engine = create_database_engine(_test_database_url())
    request.addfinalizer(engine.dispose)
    table_list = ", ".join(APPLICATION_TABLE_NAMES)

    with engine.begin() as connection:
        assert_safe_test_connection(connection)
        connection.execute(text(f"TRUNCATE TABLE {table_list} CASCADE"))

    def clean_tables() -> None:
        with engine.begin() as connection:
            assert_safe_test_connection(connection)
            connection.execute(text(f"TRUNCATE TABLE {table_list} CASCADE"))

    request.addfinalizer(clean_tables)
    yield create_session_factory(engine)


def _seed_source_and_config(
    session: Session,
    *,
    source_key: str | None = None,
) -> tuple[UUID, UUID]:
    source = SourceProfile(source_key=source_key or f"omf-{uuid4().hex}")
    parser_config = {"version": PARSER_VERSION}
    chunk_config = {"hash": CHUNK_CONFIG_HASH}
    tokenizer_config: dict[str, object] = {}
    embedding_config = embedding_config_snapshot(
        _embedding_descriptor(),
        _embedding_adapter_identity(),
        "Instruct: {query}",
    )
    rrf_config: dict[str, object] = {}
    config = IndexConfig(
        config_hash=full_index_config_hash(
            parser_config=parser_config,
            chunk_config=chunk_config,
            tokenizer_config=tokenizer_config,
            embedding_config=embedding_config,
            rrf_config=rrf_config,
        ),
        parser_config=parser_config,
        chunk_config=chunk_config,
        tokenizer_config=tokenizer_config,
        embedding_config=embedding_config,
        rrf_config=rrf_config,
    )
    session.add_all((source, config))
    session.flush()
    return source.id, config.id


def _repository(
    session: Session,
    source_profile_id: UUID,
    index_config_id: UUID,
) -> PostgresIndexRepository:
    return PostgresIndexRepository(
        session=session,
        source_profile_id=source_profile_id,
        index_config_id=index_config_id,
        embedding_descriptor=_embedding_descriptor(),
        embedding_adapter_identity=_embedding_adapter_identity(),
    )


def _metadata(*, version: str | None = "1.0") -> DocumentMetadata:
    return DocumentMetadata(
        document_date=None,
        version=version,
        version_scope=VersionScope.CURRENT,
        decision_state=DecisionState.CONFIRMED,
        owner_domain=OwnerDomain.DOCS,
    )


def _parsed_two_sections() -> tuple[ParsedMarkdown, tuple[ChunkDraft, ...]]:
    sections = (
        ParsedSection(0, None, 1, "Same", ("Same",), "body\n", 1, 2, ()),
        ParsedSection(1, None, 1, "Same", ("Same",), "body\n", 3, 4, ()),
    )
    chunks = tuple(
        ChunkDraft(
            ordinal=0,
            raw_text="body\n",
            search_text="# Same\nbody\n",
            token_count=3,
            line_start=line_start,
            line_end=line_start,
            chunk_hash="e" * 64,
        )
        for line_start in (2, 4)
    )
    return ParsedMarkdown(PARSER_VERSION, sections), chunks


def _result(
    status: IndexRunStatus,
    *,
    run_id: UUID,
    **changes: object,
) -> IndexResult:
    result = IndexResult(
        run_id=run_id,
        status=status,
        occurrence_count=2,
        unique_content_count=1,
        excluded_file_count=3,
    )
    return replace(result, **changes)


class _SnapshotProvider:
    def __init__(self, snapshots: dict[str, SourceSnapshot]) -> None:
        self.snapshots = snapshots

    def snapshot(self, repo: Path, commit_sha: str) -> SourceSnapshot:
        del repo
        return self.snapshots[commit_sha]


class _CountingParser:
    def __init__(self, *, failure_text: str | None = None) -> None:
        self.calls = 0
        self.failure_text = failure_text

    def parse(self, source: str) -> ParsedMarkdown:
        self.calls += 1
        if self.failure_text is not None and self.failure_text in source:
            raise RuntimeError("private parser detail")
        line_end = max(1, len(source.splitlines()))
        block = ParsedBlock("paragraph", source, 1, line_end, ())
        section = ParsedSection(
            0,
            None,
            0,
            None,
            (),
            source,
            1,
            line_end,
            (block,),
        )
        return ParsedMarkdown(PARSER_VERSION, (section,))


class _CountingChunker:
    def __init__(
        self,
        identity: str,
        *,
        failure_text: str | None = None,
    ) -> None:
        self.identity = identity
        self.failure_text = failure_text
        self.calls = 0

    def split(
        self,
        section: ParsedSection,
        *,
        parser_version: str,
    ) -> tuple[ChunkDraft, ...]:
        assert parser_version == PARSER_VERSION
        self.calls += 1
        if self.failure_text is not None and self.failure_text in section.body:
            raise RuntimeError("private chunker detail")
        return (
            ChunkDraft(
                0,
                section.body,
                section.body,
                len(section.body),
                section.line_start,
                section.line_end,
                hashlib.sha256(f"{self.identity}\0{section.body}".encode()).hexdigest(),
            ),
        )


class _CountingEmbeddings:
    def __init__(
        self,
        dimension: int,
        *,
        failure_text: str | None = None,
        returned_dimension: int | None = None,
    ) -> None:
        self.dimension = dimension
        self.failure_text = failure_text
        self.returned_dimension = returned_dimension or dimension
        self.document_calls = 0
        self.document_count = 0

    def embed_documents(
        self,
        documents: Sequence[str],
    ) -> tuple[tuple[float, ...], ...]:
        self.document_calls += 1
        self.document_count += len(documents)
        if self.failure_text is not None and any(
            self.failure_text in document for document in documents
        ):
            raise RuntimeError("private embedding detail")
        return tuple(
            tuple(float(index + 1) / 10 for index in range(self.returned_dimension))
            for _ in documents
        )


def _snapshot(
    commit_sha: str,
    files: dict[str, bytes],
    *,
    excluded_file_count: int = 0,
) -> SourceSnapshot:
    return SourceSnapshot(
        commit_sha,
        tuple(ArchiveFile(path, content) for path, content in sorted(files.items())),
        excluded_file_count,
    )


def _seed_config(
    session: Session,
    *,
    descriptor: EmbeddingDescriptor,
    chunk_config_hash: str = CHUNK_CONFIG_HASH,
    query_instruction: str = "Instruct: {query}",
    rrf_config: dict[str, object] | None = None,
) -> tuple[UUID, str]:
    parser_config = {"version": PARSER_VERSION}
    chunk_config = {"hash": chunk_config_hash}
    tokenizer_config: dict[str, object] = {}
    embedding_config = embedding_config_snapshot(
        descriptor,
        _embedding_adapter_identity(),
        query_instruction,
    )
    rrf = rrf_config or {}
    config = IndexConfig(
        config_hash=full_index_config_hash(
            parser_config=parser_config,
            chunk_config=chunk_config,
            tokenizer_config=tokenizer_config,
            embedding_config=embedding_config,
            rrf_config=rrf,
        ),
        parser_config=parser_config,
        chunk_config=chunk_config,
        tokenizer_config=tokenizer_config,
        embedding_config=embedding_config,
        rrf_config=rrf,
    )
    session.add(config)
    session.flush()
    return config.id, document_embedding_config_hash(embedding_config)


def _pipeline(
    session_factory: sessionmaker[Session],
    *,
    source_id: UUID,
    config_id: UUID,
    descriptor: EmbeddingDescriptor,
    embedding_config_hash: str,
    chunk_config_hash: str,
    snapshots: dict[str, SourceSnapshot],
    parser: _CountingParser,
    chunker: _CountingChunker,
    embeddings: _CountingEmbeddings,
) -> TransactionalIndexPipeline:
    def repository_factory(session: object) -> PostgresIndexRepository:
        assert isinstance(session, Session)
        return PostgresIndexRepository(
            session=session,
            source_profile_id=source_id,
            index_config_id=config_id,
            embedding_descriptor=descriptor,
            embedding_adapter_identity=_embedding_adapter_identity(),
        )

    return TransactionalIndexPipeline(
        transactions=session_factory,
        repository_factory=repository_factory,
        service_factory=lambda repository: IndexService(
            repository=repository,  # type: ignore[arg-type]
            parser=parser,
            chunker=chunker,
            embeddings=embeddings,
            parser_version=PARSER_VERSION,
            chunk_config_hash=chunk_config_hash,
            embedding_config_hash=embedding_config_hash,
            embedding_dimension=descriptor.dimension,
        ),
        snapshot_provider=_SnapshotProvider(snapshots),
        source_repo=Path("/read-only/source"),
    )


def test_building_run_content_and_occurrence_are_idempotently_persisted(
    repository_session_factory: sessionmaker[Session],
) -> None:
    """Run FKs stay exact while duplicate content and occurrences are reused."""
    with repository_session_factory.begin() as session:
        source_id, config_id = _seed_source_and_config(session)
        repository = _repository(session, source_id, config_id)
        run_id = repository.create_building_run(COMMIT_SHA)
        first_content_id = repository.upsert_content("a" * 64, "문서")
        second_content_id = repository.upsert_content("a" * 64, "문서")
        repository.create_occurrence(
            run_id, first_content_id, "docs/planning/example.md", _metadata()
        )
        repository.create_occurrence(
            run_id, first_content_id, "docs/planning/example.md", _metadata()
        )

        run = session.get(IndexRun, run_id)
        assert run is not None
        assert (run.source_profile_id, run.index_config_id, run.status) == (
            source_id,
            config_id,
            "building",
        )
        assert first_content_id == second_content_id
        assert session.scalar(select(func.count()).select_from(DocumentContent)) == 1
        assert session.scalar(select(func.count()).select_from(DocumentOccurrence)) == 1

        other_content_id = repository.upsert_content("f" * 64, "다른 문서")
        with pytest.raises(RepositoryInvariantError, match="source_path"):
            repository.create_occurrence(
                run_id,
                other_content_id,
                "docs/planning/example.md",
                _metadata(),
            )


def test_parse_identity_reuses_sections_chunks_and_rejects_empty_parse_rows(
    repository_session_factory: sessionmaker[Session],
) -> None:
    """Exact parse identity is reused; empty parse/chunk groups remain valid."""
    with repository_session_factory.begin() as session:
        source_id, config_id = _seed_source_and_config(session)
        repository = _repository(session, source_id, config_id)
        content_id = repository.upsert_content("a" * 64, "# Same\nbody\n")
        parsed, chunks = _parsed_two_sections()

        first = repository.save_parse(
            content_id, PARSER_VERSION, CHUNK_CONFIG_HASH, parsed, chunks
        )
        repeated = repository.save_parse(
            content_id, PARSER_VERSION, CHUNK_CONFIG_HASH, parsed, chunks
        )

        assert repeated == first
        assert (
            repository.find_parse(content_id, PARSER_VERSION, CHUNK_CONFIG_HASH)
            == first
        )
        assert repository.find_parse(content_id, PARSER_VERSION, "0" * 64) is None
        assert session.scalar(select(func.count()).select_from(DocumentParse)) == 1
        stored_parse = session.scalar(select(DocumentParse))
        expected_manifest = parse_artifact_manifest(
            parsed.sections,
            chunks,
            (0, 1),
        )
        assert stored_parse is not None
        assert (
            stored_parse.section_count,
            stored_parse.chunk_count,
            stored_parse.artifact_hash,
        ) == (
            expected_manifest.section_count,
            expected_manifest.chunk_count,
            expected_manifest.artifact_hash,
        )
        conflicting_section = replace(parsed.sections[0], body="changed\n")
        with pytest.raises(RepositoryInvariantError, match="conflicting parse replay"):
            repository.save_parse(
                content_id,
                PARSER_VERSION,
                CHUNK_CONFIG_HASH,
                ParsedMarkdown(
                    PARSER_VERSION,
                    (conflicting_section, parsed.sections[1]),
                ),
                chunks,
            )

        empty_content_id = repository.upsert_content("0" * 64, "")
        with pytest.raises(RepositoryInvariantError, match="manifest"):
            repository.save_parse(
                empty_content_id,
                PARSER_VERSION,
                CHUNK_CONFIG_HASH,
                ParsedMarkdown(PARSER_VERSION, ()),
                (),
            )


def test_find_parse_rejects_stored_manifest_tamper(
    repository_session_factory: sessionmaker[Session],
) -> None:
    with repository_session_factory.begin() as session:
        source_id, config_id = _seed_source_and_config(session)
        repository = _repository(session, source_id, config_id)
        content_id = repository.upsert_content("a" * 64, "# Same\nbody\n")
        parsed, chunks = _parsed_two_sections()
        repository.save_parse(
            content_id,
            PARSER_VERSION,
            CHUNK_CONFIG_HASH,
            parsed,
            chunks,
        )
        stored_parse = session.scalar(select(DocumentParse))
        assert stored_parse is not None
        stored_parse.artifact_hash = "f" * 64
        session.flush()

        with pytest.raises(RepositoryInvariantError, match="manifest"):
            repository.find_parse(content_id, PARSER_VERSION, CHUNK_CONFIG_HASH)


@pytest.mark.parametrize(
    "tamper",
    ["delete-section", "delete-chunk", "mutate-section", "mutate-chunk", "parent"],
)
def test_find_parse_recomputes_actual_projection_before_reuse(
    repository_session_factory: sessionmaker[Session],
    tamper: str,
) -> None:
    with repository_session_factory.begin() as session:
        source_id, config_id = _seed_source_and_config(session)
        repository = _repository(session, source_id, config_id)
        content_id = repository.upsert_content("a" * 64, "# Same\nbody\n")
        parsed, chunks = _parsed_two_sections()
        repository.save_parse(
            content_id,
            PARSER_VERSION,
            CHUNK_CONFIG_HASH,
            parsed,
            chunks,
        )
        sections = tuple(session.scalars(select(Section).order_by(Section.ordinal)))
        stored_chunks = tuple(
            session.scalars(
                select(Chunk).join(Section).order_by(Section.ordinal, Chunk.ordinal)
            )
        )
        assert len(sections) == 2 and len(stored_chunks) == 2
        if tamper == "delete-section":
            session.delete(sections[1])
        elif tamper == "delete-chunk":
            session.delete(stored_chunks[1])
        elif tamper == "mutate-section":
            sections[1].body = "changed\n"
        elif tamper == "mutate-chunk":
            stored_chunks[1].search_text = "# Same\nchanged\n"
        else:
            sections[1].parent_section_id = sections[0].id
        session.flush()

        with pytest.raises(RepositoryInvariantError, match="manifest"):
            repository.find_parse(content_id, PARSER_VERSION, CHUNK_CONFIG_HASH)


def test_embedding_vector_reuse_creates_a_link_for_each_chunk(
    repository_session_factory: sessionmaker[Session],
) -> None:
    """One vector calculation can back distinct chunk/config link rows."""
    with repository_session_factory.begin() as session:
        source_id, config_id = _seed_source_and_config(session)
        repository = _repository(session, source_id, config_id)
        content_id = repository.upsert_content("a" * 64, "repeated")
        parsed, chunks = _parsed_two_sections()
        artifacts = repository.save_parse(
            content_id, PARSER_VERSION, CHUNK_CONFIG_HASH, parsed, chunks
        )
        first_chunk, second_chunk = artifacts.chunks

        repository.save_embedding(
            first_chunk.chunk_id, EMBEDDING_CONFIG_HASH, (0.1, 0.2, 0.3)
        )
        reusable = repository.find_reusable_embedding(
            second_chunk.draft.chunk_hash, EMBEDDING_CONFIG_HASH
        )
        assert reusable == pytest.approx((0.1, 0.2, 0.3))
        with pytest.raises(RepositoryInvariantError, match="shared chunk hash"):
            repository.save_embedding(
                second_chunk.chunk_id,
                EMBEDDING_CONFIG_HASH,
                (0.1, 0.2, 0.4),
            )
        repository.save_embedding(
            second_chunk.chunk_id, EMBEDDING_CONFIG_HASH, reusable
        )
        repository.save_embedding(
            second_chunk.chunk_id, EMBEDDING_CONFIG_HASH, reusable
        )

        assert repository.find_embedding(
            first_chunk.chunk_id, EMBEDDING_CONFIG_HASH
        ) == pytest.approx((0.1, 0.2, 0.3))
        assert repository.find_embedding(
            second_chunk.chunk_id, EMBEDDING_CONFIG_HASH
        ) == pytest.approx((0.1, 0.2, 0.3))
        assert session.scalar(select(func.count()).select_from(ChunkEmbedding)) == 2


def test_ready_failed_stats_relations_and_active_pointer_are_explicit(
    repository_session_factory: sessionmaker[Session],
) -> None:
    """Terminal updates and explicit relation writes never activate a run."""
    with repository_session_factory.begin() as session:
        source_id, config_id = _seed_source_and_config(session)
        source = session.get_one(SourceProfile, source_id)
        active = IndexRun(
            source_profile_id=source_id,
            index_config_id=config_id,
            commit_sha="1" * 40,
            status="active",
            activated_at=ACTIVATED_AT,
        )
        session.add(active)
        session.flush()
        source.active_index_run_id = active.id
        session.flush()

        repository = _repository(session, source_id, config_id)
        ready_id = repository.create_building_run(COMMIT_SHA)
        content_id = repository.upsert_content("a" * 64, "new\nold\n")
        repository.create_occurrence(
            ready_id, content_id, "docs/planning/new.md", _metadata()
        )
        repository.create_occurrence(
            ready_id, content_id, "docs/planning/old.md", _metadata()
        )
        assert repository.save_relations(ready_id, ()) == 0
        relation = DocumentRelationSpec(
            from_source_path="docs/planning/new.md",
            to_source_path="docs/planning/old.md",
            relation_type=RelationType.SUPERSEDES,
            evidence_source_path="docs/planning/new.md",
            evidence_line_range=LineRange(1, 1),
        )
        assert repository.save_relations(ready_id, (relation,)) == 1
        assert repository.save_relations(ready_id, (relation,)) == 0
        conflicting_relation = replace(
            relation,
            evidence_line_range=LineRange(2, 2),
        )
        with pytest.raises(RepositoryInvariantError, match="evidence line"):
            repository.save_relations(ready_id, (conflicting_relation,))
        with pytest.raises(RepositoryInvariantError, match="run_id"):
            repository.mark_ready(
                ready_id,
                _result(IndexRunStatus.READY, run_id=uuid4()),
            )
        repository.mark_ready(
            ready_id,
            _result(IndexRunStatus.READY, run_id=ready_id),
        )

        failed_id = repository.create_building_run("2" * 40)
        unsafe = _result(
            IndexRunStatus.FAILED,
            run_id=failed_id,
            parse_failure_count=1,
            failure_code="parse_failure",
            failure_detail="secret source /Users/private token=x",
        )
        with pytest.raises(RepositoryInvariantError, match="sanitized"):
            repository.mark_failed(failed_id, unsafe)
        safe = replace(
            unsafe,
            failure_detail="A source document could not be parsed.",
        )
        repository.mark_failed(failed_id, safe)

        ready = session.get_one(IndexRun, ready_id)
        failed = session.get_one(IndexRun, failed_id)
        assert ready.status == "ready"
        assert ready.stats["occurrence_count"] == 2
        assert ready.stats["excluded_file_count"] == 3
        assert failed.status == "failed"
        assert failed.failure_code == "parse_failure"
        assert session.scalar(select(func.count()).select_from(DocumentRelation)) == 1
        assert source.active_index_run_id == active.id


def test_same_source_advisory_lock_is_exclusive_but_other_source_is_independent(
    repository_session_factory: sessionmaker[Session],
) -> None:
    """Transaction locks serialize one source without globally serializing all."""
    with repository_session_factory.begin() as session:
        source_id, config_id = _seed_source_and_config(session)
        other_source = SourceProfile(source_key=f"other-{uuid4().hex}")
        session.add(other_source)
        session.flush()
        other_source_id = other_source.id

    with (
        repository_session_factory() as first_session,
        repository_session_factory() as second_session,
        first_session.begin(),
        second_session.begin(),
    ):
        first = _repository(first_session, source_id, config_id)
        same = _repository(second_session, source_id, config_id)
        other = _repository(second_session, other_source_id, config_id)

        assert first.try_acquire_indexing_lock() is True
        assert same.try_acquire_indexing_lock() is False
        assert other.try_acquire_indexing_lock() is True

    with repository_session_factory.begin() as released_session:
        released = _repository(released_session, source_id, config_id)
        assert released.try_acquire_indexing_lock() is True


def test_invalid_parse_and_vector_invariants_do_not_make_run_ready(
    repository_session_factory: sessionmaker[Session],
) -> None:
    """Duplicate ordinals and wrong vector shape are rejected before READY."""
    with repository_session_factory.begin() as session:
        source_id, config_id = _seed_source_and_config(session)
        repository = _repository(session, source_id, config_id)
        run_id = repository.create_building_run(COMMIT_SHA)
        content_id = repository.upsert_content("a" * 64, "body")
        parsed = ParsedMarkdown(
            PARSER_VERSION,
            (ParsedSection(0, None, 0, None, (), "body", 1, 1, ()),),
        )
        duplicate_chunks = tuple(
            ChunkDraft(
                ordinal=0,
                raw_text="body",
                search_text="body",
                token_count=1,
                line_start=1,
                line_end=1,
                chunk_hash=character * 64,
            )
            for character in ("1", "2")
        )
        with pytest.raises(RepositoryInvariantError, match="ordinal"):
            repository.save_parse(
                content_id,
                PARSER_VERSION,
                CHUNK_CONFIG_HASH,
                parsed,
                duplicate_chunks,
            )
        with pytest.raises(RepositoryInvariantError, match="dimension"):
            repository.save_embedding(uuid4(), EMBEDDING_CONFIG_HASH, (0.1, 0.2))

        assert session.get_one(IndexRun, run_id).status == "building"
        assert session.scalar(select(func.count()).select_from(DocumentParse)) == 0


def test_failed_transaction_rolls_back_partial_run_and_preserves_active_pointer(
    repository_session_factory: sessionmaker[Session],
) -> None:
    """Caller-owned transactions make repository writes atomic on exceptions."""
    with repository_session_factory.begin() as session:
        source_id, config_id = _seed_source_and_config(session)
        source = session.get_one(SourceProfile, source_id)
        active = IndexRun(
            source_profile_id=source_id,
            index_config_id=config_id,
            commit_sha="1" * 40,
            status="active",
            activated_at=ACTIVATED_AT,
        )
        session.add(active)
        session.flush()
        source.active_index_run_id = active.id
        active_id = active.id

    with pytest.raises(RuntimeError, match="force rollback"):
        with repository_session_factory.begin() as session:
            repository = _repository(session, source_id, config_id)
            repository.create_building_run(COMMIT_SHA)
            repository.upsert_content("a" * 64, "partial")
            raise RuntimeError("force rollback")

    with repository_session_factory() as session:
        source = session.get_one(SourceProfile, source_id)
        assert source.active_index_run_id == active_id
        assert session.scalar(select(func.count()).select_from(IndexRun)) == 1
        assert session.scalar(select(func.count()).select_from(DocumentContent)) == 0


def test_config_tamper_descriptor_and_document_hash_mismatch_precede_artifacts(
    repository_session_factory: sessionmaker[Session],
) -> None:
    """All three configuration identity boundaries reject before artifact writes."""
    with repository_session_factory.begin() as session:
        source_id, config_id = _seed_source_and_config(session)
        config = session.get_one(IndexConfig, config_id)
        original_hash = config.config_hash
        config.config_hash = "f" * 64

    with repository_session_factory.begin() as session:
        with pytest.raises(RepositoryInvariantError, match="config_hash"):
            _repository(session, source_id, config_id)
        config = session.get_one(IndexConfig, config_id)
        config.config_hash = original_hash

    with repository_session_factory.begin() as session:
        with pytest.raises(RepositoryInvariantError, match="descriptor"):
            PostgresIndexRepository(
                session=session,
                source_profile_id=source_id,
                index_config_id=config_id,
                embedding_descriptor=EmbeddingDescriptor(
                    "other/model", "revision-1", 3
                ),
                embedding_adapter_identity=_embedding_adapter_identity(),
            )
        repository = _repository(session, source_id, config_id)
        with pytest.raises(RepositoryInvariantError, match="embedding_config_hash"):
            repository.save_embedding(uuid4(), "0" * 64, (0.1, 0.2, 0.3))
        assert session.scalar(select(func.count()).select_from(ChunkEmbedding)) == 0


def test_invalid_foreign_key_aborts_building_run_insert(
    repository_session_factory: sessionmaker[Session],
) -> None:
    """Unknown source/config identities are rejected by approved DB FKs."""
    with repository_session_factory() as session:
        with session.begin():
            _, config_id = _seed_source_and_config(session)
        with pytest.raises(IntegrityError), session.begin():
            repository = _repository(session, uuid4(), config_id)
            repository.create_building_run(COMMIT_SHA)


def test_pipeline_reuses_unchanged_and_duplicate_content_across_runs(
    repository_session_factory: sessionmaker[Session],
) -> None:
    """Every path is recorded while only changed unique bytes repeat model work."""
    first_sha, second_sha = "3" * 40, "4" * 40
    snapshots = {
        first_sha: _snapshot(
            first_sha,
            {
                "docs/research/a.md": b"same\n",
                "docs/research/b.md": b"same\n",
            },
            excluded_file_count=2,
        ),
        second_sha: _snapshot(
            second_sha,
            {
                "docs/research/a.md": b"same\n",
                "docs/research/b.md": b"changed\n",
            },
            excluded_file_count=1,
        ),
    }
    with repository_session_factory.begin() as session:
        source = SourceProfile(source_key=f"pipeline-{uuid4().hex}")
        session.add(source)
        session.flush()
        config_id, embedding_hash = _seed_config(
            session,
            descriptor=_embedding_descriptor(),
        )
        source_id = source.id
    parser = _CountingParser()
    embeddings = _CountingEmbeddings(3)
    pipeline = _pipeline(
        repository_session_factory,
        source_id=source_id,
        config_id=config_id,
        descriptor=_embedding_descriptor(),
        embedding_config_hash=embedding_hash,
        chunk_config_hash=CHUNK_CONFIG_HASH,
        snapshots=snapshots,
        parser=parser,
        chunker=_CountingChunker(CHUNK_CONFIG_HASH),
        embeddings=embeddings,
    )

    first = pipeline.index(first_sha)
    second = pipeline.index(second_sha)

    assert (first.status, second.status) == (
        IndexRunStatus.READY,
        IndexRunStatus.READY,
    )
    assert first.run_id != second.run_id
    assert (first.occurrence_count, second.occurrence_count) == (2, 2)
    assert (first.excluded_file_count, second.excluded_file_count) == (2, 1)
    assert parser.calls == 2
    assert embeddings.document_count == 2
    with repository_session_factory() as session:
        assert {
            run.id
            for run in session.scalars(
                select(IndexRun).where(IndexRun.status == IndexRunStatus.READY.value)
            )
        } == {first.run_id, second.run_id}
        assert session.scalar(select(func.count()).select_from(IndexRun)) == 2
        assert session.scalar(select(func.count()).select_from(DocumentOccurrence)) == 4
        assert session.scalar(select(func.count()).select_from(DocumentContent)) == 2
        assert session.scalar(select(func.count()).select_from(DocumentParse)) == 2
        assert session.scalar(select(func.count()).select_from(Chunk)) == 2
        assert session.scalar(select(func.count()).select_from(ChunkEmbedding)) == 2


@pytest.mark.parametrize(
    ("change", "expected_parses", "expected_chunks", "expected_embeddings"),
    [
        ("revision", 1, 1, 2),
        ("dimension", 1, 1, 2),
        ("chunk", 2, 2, 2),
        ("query", 1, 1, 1),
        ("rrf", 1, 1, 1),
    ],
)
def test_pipeline_invalidates_only_artifacts_owned_by_changed_config(
    repository_session_factory: sessionmaker[Session],
    change: str,
    expected_parses: int,
    expected_chunks: int,
    expected_embeddings: int,
) -> None:
    """Parser, document-vector, query, and RRF identities stay independent."""
    first_sha, second_sha = "5" * 40, "6" * 40
    snapshots = {
        first_sha: _snapshot(first_sha, {"docs/research/a.md": b"source\n"}),
        second_sha: _snapshot(second_sha, {"docs/research/a.md": b"source\n"}),
    }
    first_descriptor = _embedding_descriptor()
    second_descriptor = (
        EmbeddingDescriptor("test/model", "revision-2", 3)
        if change == "revision"
        else EmbeddingDescriptor("test/model", "revision-1", 4)
        if change == "dimension"
        else first_descriptor
    )
    second_chunk_hash = "c" * 64 if change == "chunk" else CHUNK_CONFIG_HASH
    with repository_session_factory.begin() as session:
        source = SourceProfile(source_key=f"config-{change}-{uuid4().hex}")
        session.add(source)
        session.flush()
        first_config_id, first_embedding_hash = _seed_config(
            session,
            descriptor=first_descriptor,
        )
        second_config_id, second_embedding_hash = _seed_config(
            session,
            descriptor=second_descriptor,
            chunk_config_hash=second_chunk_hash,
            query_instruction=(
                "Changed: {query}" if change == "query" else "Instruct: {query}"
            ),
            rrf_config=({"k": 60} if change == "rrf" else None),
        )
        source_id = source.id
    parser = _CountingParser()
    first_embeddings = _CountingEmbeddings(first_descriptor.dimension)
    second_embeddings = _CountingEmbeddings(second_descriptor.dimension)
    first_pipeline = _pipeline(
        repository_session_factory,
        source_id=source_id,
        config_id=first_config_id,
        descriptor=first_descriptor,
        embedding_config_hash=first_embedding_hash,
        chunk_config_hash=CHUNK_CONFIG_HASH,
        snapshots=snapshots,
        parser=parser,
        chunker=_CountingChunker(CHUNK_CONFIG_HASH),
        embeddings=first_embeddings,
    )
    second_pipeline = _pipeline(
        repository_session_factory,
        source_id=source_id,
        config_id=second_config_id,
        descriptor=second_descriptor,
        embedding_config_hash=second_embedding_hash,
        chunk_config_hash=second_chunk_hash,
        snapshots=snapshots,
        parser=parser,
        chunker=_CountingChunker(second_chunk_hash),
        embeddings=second_embeddings,
    )

    assert first_pipeline.index(first_sha).status is IndexRunStatus.READY
    assert second_pipeline.index(second_sha).status is IndexRunStatus.READY

    assert parser.calls == expected_parses
    assert first_embeddings.document_count + second_embeddings.document_count == (
        expected_embeddings
    )
    with repository_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(DocumentParse)) == (
            expected_parses
        )
        assert session.scalar(select(func.count()).select_from(Section)) == (
            expected_parses
        )
        assert (
            session.scalar(select(func.count()).select_from(Chunk)) == expected_chunks
        )
        assert session.scalar(select(func.count()).select_from(ChunkEmbedding)) == (
            expected_embeddings
        )


@pytest.mark.parametrize(
    ("failure_stage", "failure_code"),
    [
        ("decode", "decode_failure"),
        ("parser", "parse_failure"),
        ("chunker", "parse_failure"),
        ("embedding", "embedding_failure"),
        ("dimension", "invariant_failure"),
    ],
)
def test_pipeline_rolls_back_partial_artifacts_and_preserves_active_pointer(
    repository_session_factory: sessionmaker[Session],
    failure_stage: str,
    failure_code: str,
) -> None:
    """Expected document failures retain only a sanitized FAILED run."""
    commit_sha = "7" * 40
    bad_content = b"\xff" if failure_stage == "decode" else b"bad\n"
    snapshots = {
        commit_sha: _snapshot(
            commit_sha,
            {
                "docs/research/a-good.md": b"good\n",
                "docs/research/z-bad.md": bad_content,
            },
            excluded_file_count=3,
        )
    }
    with repository_session_factory.begin() as session:
        source = SourceProfile(source_key=f"failure-{failure_stage}-{uuid4().hex}")
        session.add(source)
        session.flush()
        config_id, embedding_hash = _seed_config(
            session,
            descriptor=_embedding_descriptor(),
        )
        active = IndexRun(
            source_profile_id=source.id,
            index_config_id=config_id,
            commit_sha="8" * 40,
            status=IndexRunStatus.ACTIVE.value,
            activated_at=ACTIVATED_AT,
        )
        session.add(active)
        session.flush()
        source.active_index_run_id = active.id
        session.flush()
        source_id, active_id = source.id, active.id
    parser = _CountingParser(failure_text="bad" if failure_stage == "parser" else None)
    chunker = _CountingChunker(
        CHUNK_CONFIG_HASH,
        failure_text="bad" if failure_stage == "chunker" else None,
    )
    embeddings = _CountingEmbeddings(
        3,
        failure_text="bad" if failure_stage == "embedding" else None,
        returned_dimension=2 if failure_stage == "dimension" else None,
    )
    pipeline = _pipeline(
        repository_session_factory,
        source_id=source_id,
        config_id=config_id,
        descriptor=_embedding_descriptor(),
        embedding_config_hash=embedding_hash,
        chunk_config_hash=CHUNK_CONFIG_HASH,
        snapshots=snapshots,
        parser=parser,
        chunker=chunker,
        embeddings=embeddings,
    )

    result = pipeline.index(commit_sha)

    assert result.status is IndexRunStatus.FAILED
    assert result.failure_code == failure_code
    assert result.failure_detail is not None
    assert "bad" not in result.failure_detail
    with repository_session_factory() as session:
        source = session.get_one(SourceProfile, source_id)
        failed = session.scalar(
            select(IndexRun).where(IndexRun.status == IndexRunStatus.FAILED.value)
        )
        assert failed is not None
        assert result.run_id == failed.id
        assert failed.failure_code == failure_code
        assert failed.stats["excluded_file_count"] == 3
        assert source.active_index_run_id == active_id
        assert session.scalar(select(func.count()).select_from(DocumentOccurrence)) == 0
        assert session.scalar(select(func.count()).select_from(DocumentContent)) == 0
        assert session.scalar(select(func.count()).select_from(DocumentParse)) == 0
        assert session.scalar(select(func.count()).select_from(ChunkEmbedding)) == 0


def test_failed_pipeline_keeps_same_source_lock_through_failure_commit(
    repository_session_factory: sessionmaker[Session],
) -> None:
    """A competitor cannot enter between savepoint rollback and FAILED persistence."""
    commit_sha = "b" * 40
    snapshot = _snapshot(
        commit_sha,
        {"docs/research/bad.md": b"bad\n"},
    )
    with repository_session_factory.begin() as session:
        source = SourceProfile(source_key=f"lock-gap-{uuid4().hex}")
        session.add(source)
        session.flush()
        config_id, embedding_hash = _seed_config(
            session,
            descriptor=_embedding_descriptor(),
        )
        source_id = source.id
    competitor_lock_results: list[bool] = []
    repositories: list[PostgresIndexRepository] = []

    class LockObservingRepository(PostgresIndexRepository):
        failure_calls = 0

        def mark_failed(self, run_id: UUID, result: IndexResult) -> None:
            self.failure_calls += 1
            if self.failure_calls == 2:
                with repository_session_factory.begin() as competitor_session:
                    competitor = PostgresIndexRepository(
                        session=competitor_session,
                        source_profile_id=source_id,
                        index_config_id=config_id,
                        embedding_descriptor=_embedding_descriptor(),
                        embedding_adapter_identity=_embedding_adapter_identity(),
                    )
                    competitor_lock_results.append(
                        competitor.try_acquire_indexing_lock()
                    )
            super().mark_failed(run_id, result)

    def repository_factory(session: object) -> PostgresIndexRepository:
        assert isinstance(session, Session)
        repository = LockObservingRepository(
            session=session,
            source_profile_id=source_id,
            index_config_id=config_id,
            embedding_descriptor=_embedding_descriptor(),
            embedding_adapter_identity=_embedding_adapter_identity(),
        )
        repositories.append(repository)
        return repository

    pipeline = TransactionalIndexPipeline(
        transactions=repository_session_factory,
        repository_factory=repository_factory,
        service_factory=lambda repository: IndexService(
            repository=repository,  # type: ignore[arg-type]
            parser=_CountingParser(failure_text="bad"),
            chunker=_CountingChunker(CHUNK_CONFIG_HASH),
            embeddings=_CountingEmbeddings(3),
            parser_version=PARSER_VERSION,
            chunk_config_hash=CHUNK_CONFIG_HASH,
            embedding_config_hash=embedding_hash,
            embedding_dimension=3,
        ),
        snapshot_provider=_SnapshotProvider({commit_sha: snapshot}),
        source_repo=Path("/read-only/source"),
    )

    result = pipeline.index(commit_sha)

    assert result.status is IndexRunStatus.FAILED
    assert competitor_lock_results == [False]
    assert len(repositories) == 1
    assert repositories[0].failure_calls == 2  # type: ignore[attr-defined]
    with repository_session_factory() as session:
        failed_runs = tuple(
            session.scalars(
                select(IndexRun).where(IndexRun.status == IndexRunStatus.FAILED.value)
            )
        )
        assert len(failed_runs) == 1
        assert failed_runs[0].id == result.run_id
        assert session.scalar(select(func.count()).select_from(DocumentOccurrence)) == 0
        assert session.scalar(select(func.count()).select_from(DocumentContent)) == 0


def test_repository_invariant_during_embedding_records_safe_failed_run(
    repository_session_factory: sessionmaker[Session],
) -> None:
    """Repository identity errors roll back artifacts and retain a FAILED run."""
    commit_sha = "c" * 40
    snapshot = _snapshot(
        commit_sha,
        {"docs/research/a.md": b"source\n"},
    )
    with repository_session_factory.begin() as session:
        source = SourceProfile(source_key=f"repository-error-{uuid4().hex}")
        session.add(source)
        session.flush()
        config_id, embedding_hash = _seed_config(
            session,
            descriptor=_embedding_descriptor(),
        )
        source_id = source.id

    class FailingEmbeddingRepository(PostgresIndexRepository):
        def save_embedding(
            self,
            chunk_id: UUID,
            embedding_config_hash: str,
            vector: tuple[float, ...],
        ) -> None:
            del chunk_id, embedding_config_hash, vector
            raise RepositoryInvariantError("forced repository identity conflict")

    def repository_factory(session: object) -> PostgresIndexRepository:
        assert isinstance(session, Session)
        return FailingEmbeddingRepository(
            session=session,
            source_profile_id=source_id,
            index_config_id=config_id,
            embedding_descriptor=_embedding_descriptor(),
            embedding_adapter_identity=_embedding_adapter_identity(),
        )

    pipeline = TransactionalIndexPipeline(
        transactions=repository_session_factory,
        repository_factory=repository_factory,
        service_factory=lambda repository: IndexService(
            repository=repository,  # type: ignore[arg-type]
            parser=_CountingParser(),
            chunker=_CountingChunker(CHUNK_CONFIG_HASH),
            embeddings=_CountingEmbeddings(3),
            parser_version=PARSER_VERSION,
            chunk_config_hash=CHUNK_CONFIG_HASH,
            embedding_config_hash=embedding_hash,
            embedding_dimension=3,
        ),
        snapshot_provider=_SnapshotProvider({commit_sha: snapshot}),
        source_repo=Path("/read-only/source"),
    )

    result = pipeline.index(commit_sha)

    with repository_session_factory() as session:
        failed = session.get_one(IndexRun, result.run_id)
        assert result.status is IndexRunStatus.FAILED
        assert result.failure_code == "invariant_failure"
        assert failed.status == IndexRunStatus.FAILED.value
        assert failed.failure_detail == (
            "Index artifacts violated their identity contract."
        )
        assert session.scalar(select(func.count()).select_from(IndexRun)) == 1
        assert session.scalar(select(func.count()).select_from(DocumentOccurrence)) == 0
        assert session.scalar(select(func.count()).select_from(DocumentContent)) == 0
        assert session.scalar(select(func.count()).select_from(DocumentParse)) == 0
        assert session.scalar(select(func.count()).select_from(ChunkEmbedding)) == 0


def test_pipeline_distinguishes_empty_snapshot_and_empty_document_stats(
    repository_session_factory: sessionmaker[Session],
) -> None:
    """Empty source sets and empty Markdown remain distinct successful inputs."""
    first_sha, second_sha = "9" * 40, "a" * 40
    snapshots = {
        first_sha: _snapshot(first_sha, {}, excluded_file_count=4),
        second_sha: _snapshot(
            second_sha,
            {"docs/research/empty.md": b""},
            excluded_file_count=5,
        ),
    }
    with repository_session_factory.begin() as session:
        source = SourceProfile(source_key=f"empty-{uuid4().hex}")
        session.add(source)
        session.flush()
        config_id, embedding_hash = _seed_config(
            session,
            descriptor=_embedding_descriptor(),
        )
        source_id = source.id
    pipeline = _pipeline(
        repository_session_factory,
        source_id=source_id,
        config_id=config_id,
        descriptor=_embedding_descriptor(),
        embedding_config_hash=embedding_hash,
        chunk_config_hash=CHUNK_CONFIG_HASH,
        snapshots=snapshots,
        parser=_CountingParser(),
        chunker=_CountingChunker(CHUNK_CONFIG_HASH),
        embeddings=_CountingEmbeddings(3),
    )

    first = pipeline.index(first_sha)
    second = pipeline.index(second_sha)

    assert (first.occurrence_count, first.empty_document_count) == (0, 0)
    assert first.run_id != second.run_id
    assert (first.excluded_file_count, second.excluded_file_count) == (4, 5)
    assert (second.occurrence_count, second.empty_document_count) == (1, 1)
    with repository_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(DocumentContent)) == 1
        assert session.scalar(select(func.count()).select_from(DocumentParse)) == 0
        assert session.scalar(select(func.count()).select_from(ChunkEmbedding)) == 0
