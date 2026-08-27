"""Integration tests for explicit SQLAlchemy engine and session construction."""

import subprocess
import sys
from collections.abc import Iterator
from uuid import UUID, uuid4

import database_test_utils as database_test_support
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from omf_retrieval.infrastructure.database import session
from omf_retrieval.infrastructure.database.models import (
    ApiClient,
    Chunk,
    ChunkEmbedding,
    ClientSourceGrant,
    DocumentContent,
    DocumentOccurrence,
    DocumentParse,
    DocumentRelation,
    IndexConfig,
    IndexRun,
    SearchAuditEvent,
    Section,
    SourceProfile,
)

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


@pytest.fixture
def orm_session_factory(
    request: pytest.FixtureRequest,
) -> Iterator[sessionmaker[Session]]:
    """Yield an explicit factory against an empty migrated test schema."""
    engine = session.create_database_engine(database_test_support.test_database_url())
    request.addfinalizer(engine.dispose)
    table_list = ", ".join(APPLICATION_TABLE_NAMES)

    with engine.begin() as connection:
        database_test_support.assert_safe_test_connection(connection)
        connection.execute(text(f"TRUNCATE TABLE {table_list} CASCADE"))

    def clean_tables() -> None:
        with engine.begin() as connection:
            database_test_support.assert_safe_test_connection(connection)
            connection.execute(text(f"TRUNCATE TABLE {table_list} CASCADE"))

    request.addfinalizer(clean_tables)
    yield session.create_session_factory(engine)


def test_session_factory_functions_are_available() -> None:
    """Expose explicit engine and session factory construction functions."""
    assert callable(getattr(session, "create_database_engine", None))
    assert callable(getattr(session, "create_session_factory", None))


def test_engine_and_session_factory_options_are_explicit() -> None:
    """Enable connection liveness checks and preserve committed object state."""
    engine = session.create_database_engine(
        database_test_support.test_database_url(), echo=False
    )

    try:
        factory = session.create_session_factory(engine)

        assert engine.pool._pre_ping is True
        assert factory.kw["expire_on_commit"] is False
        assert factory.kw["bind"] is engine
    finally:
        engine.dispose()


def test_engine_url_redacts_raw_password() -> None:
    """Keep a URL password out of the engine URL string representations."""
    raw_password = "unredacted-test-password"
    engine = session.create_database_engine(
        f"postgresql+psycopg://reader:{raw_password}@127.0.0.1:55432/test"
    )

    try:
        assert raw_password not in str(engine.url)
        assert raw_password not in repr(engine.url)
    finally:
        engine.dispose()


def test_database_module_import_has_no_connection_or_session_side_effect() -> None:
    """Import database modules without constructing an engine or opening state."""
    probe = """
from unittest.mock import patch
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

with (
    patch('sqlalchemy.create_engine') as create_engine,
    patch.object(Engine, 'connect') as connect,
    patch.object(Session, '__init__', return_value=None) as session_init,
):
    import omf_retrieval.infrastructure.database.base
    import omf_retrieval.infrastructure.database.models
    import omf_retrieval.infrastructure.database.session

    assert not create_engine.called
    assert not connect.called
    assert not session_init.called
"""

    completed = subprocess.run(
        [sys.executable, "-c", probe],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_all_models_flush_commit_and_load_with_application_uuid_defaults(
    orm_session_factory: sessionmaker[Session],
) -> None:
    """Persist representative document, vector, auth, and audit records."""
    suffix = uuid4().hex
    source = SourceProfile(source_key=f"omf-{suffix}")
    config = IndexConfig(
        config_hash="a" * 64,
        parser_config={},
        chunk_config={},
        tokenizer_config={},
        embedding_config={},
        rrf_config={},
    )

    assert source.id is None
    assert config.id is None

    with orm_session_factory() as database_session:
        database_session.add_all([source, config])
        database_session.flush()

        assert isinstance(source.id, UUID)
        assert isinstance(config.id, UUID)

        run = IndexRun(
            source_profile_id=source.id,
            index_config_id=config.id,
            commit_sha="b" * 40,
        )
        content = DocumentContent(
            content_hash="c" * 64,
            content="문서",
            byte_size=len("문서".encode()),
        )
        client = ApiClient(
            name="test client",
            key_id=suffix[:16],
            token_hash=b"t" * 32,
        )
        database_session.add_all([run, content, client])
        database_session.flush()

        occurrence = DocumentOccurrence(
            run_id=run.id,
            content_id=content.id,
            source_path="docs/planning/example.md",
            version_scope="current",
            decision_state="confirmed",
            owner_domain="docs",
        )
        document_parse = DocumentParse(
            content_id=content.id,
            parser_version="markdown-it-py",
            chunk_config_hash="d" * 64,
            section_count=1,
            chunk_count=1,
            artifact_hash=(
                "a3e0d7796c24dc68645ab5e97dc3900eaad7bea0bde3d8a9af9be249609d2d1f"
            ),
        )
        grant = ClientSourceGrant(
            client_id=client.id,
            source_profile_id=source.id,
        )
        database_session.add_all([occurrence, document_parse, grant])
        database_session.flush()

        section = Section(
            parse_id=document_parse.id,
            ordinal=0,
            level=1,
            heading="제목",
            heading_path=["제목"],
            body="본문",
            line_start=1,
            line_end=2,
        )
        relation = DocumentRelation(
            run_id=run.id,
            from_occurrence_id=occurrence.id,
            to_occurrence_id=occurrence.id,
            relation_type="supersedes",
            evidence_source_path=occurrence.source_path,
            evidence_line_start=1,
            evidence_line_end=2,
        )
        database_session.add_all([section, relation])
        database_session.flush()

        chunk = Chunk(
            section_id=section.id,
            ordinal=0,
            raw_text="본문",
            search_text="제목 본문",
            token_count=2,
            line_start=1,
            line_end=2,
            chunk_hash="e" * 64,
        )
        database_session.add(chunk)
        database_session.flush()

        embedding = ChunkEmbedding(
            chunk_id=chunk.id,
            embedding_config_hash="f" * 64,
            model_name="test/model",
            model_revision="revision-1",
            dimension=3,
            embedding=[0.1, 0.2, 0.3],
        )
        audit_event = SearchAuditEvent(
            request_id=f"request-{suffix}",
            client_id=client.id,
            source_profile_id=source.id,
            query_hmac=b"q" * 32,
            returned_chunk_ids=[chunk.id],
            commit_sha=run.commit_sha,
            status="ok",
            result_count=1,
            embedding_ms=1,
            keyword_ms=2,
            vector_ms=3,
            rrf_ms=4,
            total_ms=10,
        )
        database_session.add_all([embedding, audit_event])
        database_session.flush()

        mapped_types = {
            type(instance)
            for instance in (
                source,
                config,
                run,
                content,
                occurrence,
                document_parse,
                section,
                chunk,
                embedding,
                relation,
                client,
                grant,
                audit_event,
            )
        }
        source_id = source.id
        database_session.commit()

    assert len(mapped_types) == 13
    assert source.source_key == f"omf-{suffix}"

    with orm_session_factory() as database_session:
        loaded_source = database_session.get(SourceProfile, source_id)

        assert loaded_source is not None
        assert loaded_source.source_key == f"omf-{suffix}"
        assert database_session.scalar(select(func.count()).select_from(Chunk)) == 1


@pytest.mark.parametrize("failure_kind", ["duplicate", "invalid"])
def test_integrity_failure_rollback_allows_fresh_session(
    orm_session_factory: sessionmaker[Session],
    failure_kind: str,
) -> None:
    """Recover the factory after unique and invariant flush failures."""
    source_key = f"recovery-{uuid4().hex}"
    with orm_session_factory() as database_session:
        source = SourceProfile(source_key=source_key)
        database_session.add(source)
        database_session.commit()
        source_id = source.id

    failing_source_key = source_key if failure_kind == "duplicate" else "\t\n"
    with orm_session_factory() as database_session:
        database_session.add(SourceProfile(source_key=failing_source_key))

        with pytest.raises(IntegrityError):
            database_session.flush()

        database_session.rollback()

    with orm_session_factory() as database_session:
        recovered_source = database_session.get(SourceProfile, source_id)

        assert recovered_source is not None
        assert recovered_source.source_key == source_key
