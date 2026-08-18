"""Integration tests for the PostgreSQL migration lifecycle."""

import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import Mock

import database_test_utils as database_test_support
import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from database_test_utils import (
    DEFAULT_TEST_DATABASE_URL,
    assert_safe_test_connection,
    create_test_engine,
)
from schema_expectations import (
    EXPECTED_CHECK_CONSTRAINTS,
    EXPECTED_EXPLICIT_INDEXES,
    EXPECTED_KEY_CONSTRAINTS,
    EXPECTED_NON_ID_DEFAULTS,
)
from sqlalchemy import Connection, make_url, text

REQUIRED_EXTENSIONS = {"pg_trgm", "vector"}
EXPECTED_EXTENSION_VERSIONS = {"pg_trgm": "1.6", "vector": "0.8.6"}
EXPECTED_DATABASE_IMAGE = (
    "pgvector/pgvector:0.8.6-pg18-trixie@"
    "sha256:1963bc48febf543433baa1ce3edcc6cc08154de722e22495f86681cc9a849026"
)
TEST_DATABASE_ENV = "OMF_RETRIEVAL_TEST_DATABASE_URL"
GENERIC_DATABASE_ENV = "OMF_RETRIEVAL_DATABASE_URL"
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
APPLICATION_TABLES = {
    "api_clients",
    "chunk_embeddings",
    "chunks",
    "client_source_grants",
    "document_contents",
    "document_occurrences",
    "document_parses",
    "document_relations",
    "index_configs",
    "index_runs",
    "search_audit_events",
    "sections",
    "source_profiles",
}
EXPECTED_COLUMNS = {
    "source_profiles": {
        "id uuid required",
        "source_key text required",
        "include_patterns jsonb required",
        "exclude_patterns jsonb required",
        "active_index_run_id uuid nullable",
    },
    "index_configs": {
        "id uuid required",
        "config_hash character varying(64) required",
        "parser_config jsonb required",
        "chunk_config jsonb required",
        "tokenizer_config jsonb required",
        "embedding_config jsonb required",
        "rrf_config jsonb required",
    },
    "index_runs": {
        "id uuid required",
        "source_profile_id uuid required",
        "index_config_id uuid required",
        "commit_sha character varying(40) required",
        "status text required",
        "started_at timestamp with time zone required",
        "indexed_at timestamp with time zone nullable",
        "stats jsonb required",
        "failure_code text nullable",
        "failure_detail text nullable",
    },
    "document_contents": {
        "id uuid required",
        "content_hash character varying(64) required",
        "content text required",
        "byte_size bigint required",
    },
    "document_occurrences": {
        "id uuid required",
        "run_id uuid required",
        "content_id uuid required",
        "source_path text required",
        "version_scope text required",
        "document_date date nullable",
        "document_version text nullable",
        "decision_state text required",
        "owner_domain text required",
    },
    "document_parses": {
        "id uuid required",
        "content_id uuid required",
        "parser_version text required",
        "chunk_config_hash character varying(64) required",
    },
    "sections": {
        "id uuid required",
        "parse_id uuid required",
        "parent_section_id uuid nullable",
        "ordinal integer required",
        "level smallint required",
        "heading text nullable",
        "heading_path text[] required",
        "body text required",
        "line_start integer required",
        "line_end integer required",
    },
    "chunks": {
        "id uuid required",
        "section_id uuid required",
        "ordinal integer required",
        "raw_text text required",
        "search_text text required",
        "token_count integer required",
        "line_start integer required",
        "line_end integer required",
        "chunk_hash character varying(64) required",
    },
    "chunk_embeddings": {
        "id uuid required",
        "chunk_id uuid required",
        "embedding_config_hash character varying(64) required",
        "model_name text required",
        "model_revision text required",
        "dimension integer required",
        "embedding vector required",
        "status text required",
    },
    "document_relations": {
        "id uuid required",
        "run_id uuid required",
        "from_occurrence_id uuid required",
        "to_occurrence_id uuid required",
        "relation_type text required",
        "evidence_source_path text required",
        "evidence_line_start integer required",
        "evidence_line_end integer required",
    },
    "api_clients": {
        "id uuid required",
        "name text required",
        "key_id character varying(16) required",
        "token_hash bytea required",
        "status text required",
        "expires_at timestamp with time zone nullable",
        "created_at timestamp with time zone required",
    },
    "client_source_grants": {
        "client_id uuid required",
        "source_profile_id uuid required",
    },
    "search_audit_events": {
        "id uuid required",
        "occurred_at timestamp with time zone required",
        "request_id text required",
        "client_id uuid required",
        "source_profile_id uuid required",
        "query_hmac bytea required",
        "filters jsonb required",
        "returned_chunk_ids uuid[] required",
        "commit_sha character varying(40) required",
        "status text required",
        "result_count integer required",
        "embedding_ms integer required",
        "keyword_ms integer required",
        "vector_ms integer required",
        "rrf_ms integer required",
        "total_ms integer required",
    },
}


def _installed_extensions(connection: Connection) -> set[str]:
    return set(
        connection.execute(
            text(
                "SELECT extname FROM pg_extension "
                "WHERE extname IN ('vector', 'pg_trgm')"
            )
        ).scalars()
    )


def _installed_extension_versions(connection: Connection) -> dict[str, str]:
    return dict(
        connection.execute(
            text(
                "SELECT extname, extversion FROM pg_extension "
                "WHERE extname IN ('vector', 'pg_trgm')"
            )
        ).all()
    )


def _application_tables(connection: Connection) -> set[str]:
    return set(
        connection.execute(
            text(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
            )
        ).scalars()
    )


@pytest.fixture
def alembic_config() -> Config:
    """Return Alembic config pinned to the validated test-only database URL."""
    config = Config("alembic.ini")
    config.attributes["database_url"] = database_test_support.test_database_url()
    config.attributes["connection_validator"] = assert_safe_test_connection
    return config


@pytest.fixture
def downgraded_database(
    database_connection: Connection,
    alembic_config: Config,
) -> Iterator[None]:
    """Downgrade for one test and always restore the schema during teardown."""
    try:
        command.downgrade(alembic_config, "base")
        yield
    finally:
        command.upgrade(alembic_config, "head")
        applied_revision = database_connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
        expected_revision = ScriptDirectory.from_config(
            alembic_config
        ).get_current_head()
        assert applied_revision == expected_revision
        assert _application_tables(database_connection) == APPLICATION_TABLES


def test_generic_database_environment_requires_explicit_test_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject a generic application URL without a test-specific acknowledgement."""
    monkeypatch.delenv(TEST_DATABASE_ENV, raising=False)
    monkeypatch.setenv(
        GENERIC_DATABASE_ENV,
        "postgresql+psycopg://production:secret@10.0.0.8:5432/production",
    )
    engine_constructor = Mock()
    monkeypatch.setattr(database_test_support, "create_engine", engine_constructor)

    with pytest.raises(ValueError, match="without an explicit test database URL"):
        create_test_engine()

    engine_constructor.assert_not_called()


def test_safe_test_database_environment_override_is_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Accept a test-specific override after validating its local identity."""
    override_url = f"{DEFAULT_TEST_DATABASE_URL}?application_name=safe-override"
    monkeypatch.setenv(TEST_DATABASE_ENV, override_url)
    monkeypatch.setenv(
        GENERIC_DATABASE_ENV,
        "postgresql+psycopg://production:secret@127.0.0.1:55432/production",
    )
    engine = create_test_engine()

    try:
        assert engine.url == make_url(override_url)
        with engine.connect() as connection:
            application_name = connection.execute(
                text("SHOW application_name")
            ).scalar_one()
        assert application_name == "safe-override"
    finally:
        engine.dispose()


def test_safe_prefixed_test_database_name_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Allow isolated worker databases beneath the approved test prefix."""
    database_server_url, _ = DEFAULT_TEST_DATABASE_URL.rsplit("/", maxsplit=1)
    worker_url = f"{database_server_url}/omf_retrieval_test_worker_1"
    monkeypatch.setenv(TEST_DATABASE_ENV, worker_url)
    engine = create_test_engine()

    try:
        assert engine.url.database == "omf_retrieval_test_worker_1"
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "unsafe_url",
    [
        "postgresql+psycopg://omf_retrieval_test:test@localhost:55432/omf_retrieval_test",
        "postgresql+psycopg://omf_retrieval_test:test@127.0.0.1:5432/omf_retrieval_test",
        "postgresql+psycopg://postgres:test@127.0.0.1:55432/omf_retrieval_test",
        "postgresql+psycopg://omf_retrieval_test:test@127.0.0.1:55432/production",
    ],
)
def test_unsafe_test_database_url_is_rejected_before_engine_creation(
    monkeypatch: pytest.MonkeyPatch,
    unsafe_url: str,
) -> None:
    """Reject unsafe host, port, user, and database identifiers before use."""
    monkeypatch.setenv(TEST_DATABASE_ENV, unsafe_url)
    engine_constructor = Mock()
    monkeypatch.setattr(database_test_support, "create_engine", engine_constructor)

    with pytest.raises(ValueError, match="Unsafe test database URL"):
        create_test_engine()

    engine_constructor.assert_not_called()


@pytest.mark.parametrize(
    ("query_key", "query_value"),
    [
        ("host", "database.internal"),
        ("hostaddr", "127.0.0.2"),
        ("port", "5432"),
        ("dbname", "production"),
        ("database", "production"),
        ("user", "postgres"),
        ("service", "production"),
        ("unknown_target", "production"),
    ],
)
def test_query_target_override_is_rejected_before_engine_creation(
    monkeypatch: pytest.MonkeyPatch,
    query_key: str,
    query_value: str,
) -> None:
    """Reject every query option except the approved application name."""
    monkeypatch.setenv(
        TEST_DATABASE_ENV,
        f"{DEFAULT_TEST_DATABASE_URL}?{query_key}={query_value}",
    )
    engine_constructor = Mock()
    monkeypatch.setattr(database_test_support, "create_engine", engine_constructor)

    with pytest.raises(ValueError, match="Unsafe test database URL query"):
        create_test_engine()

    engine_constructor.assert_not_called()


@pytest.mark.parametrize(
    ("argument_name", "argument_value"),
    [
        ("host", "database.internal"),
        ("port", 5432),
        ("user", "postgres"),
        ("dbname", "production"),
    ],
)
def test_unsafe_effective_connect_argument_is_rejected_before_engine_creation(
    monkeypatch: pytest.MonkeyPatch,
    argument_name: str,
    argument_value: str | int,
) -> None:
    """Validate the dialect's effective target, not only URL authority text."""
    effective_arguments: dict[str, str | int] = {
        "host": "127.0.0.1",
        "port": 55432,
        "user": "omf_retrieval_test",
        "dbname": "omf_retrieval_test",
    }
    effective_arguments[argument_name] = argument_value
    dialect_class = make_url(DEFAULT_TEST_DATABASE_URL).get_dialect()
    monkeypatch.setattr(
        dialect_class,
        "create_connect_args",
        lambda _dialect, _url: ([], effective_arguments),
    )
    engine_constructor = Mock()
    monkeypatch.setattr(database_test_support, "create_engine", engine_constructor)

    with pytest.raises(ValueError, match="Unsafe effective test database target"):
        create_test_engine()

    engine_constructor.assert_not_called()


@pytest.mark.parametrize(
    ("database_name", "database_user"),
    [("production", "omf_retrieval_test"), ("omf_retrieval_test", "postgres")],
)
def test_unsafe_live_identity_is_rejected_before_destructive_sql(
    database_name: str,
    database_user: str,
) -> None:
    """Reject a live database or role that differs from the test identity."""
    connection = Mock(spec=Connection)
    connection.execute.return_value.one.return_value = (
        database_name,
        database_user,
    )

    with pytest.raises(ValueError, match="destructive SQL blocked"):
        assert_safe_test_connection(connection)

    connection.execute.assert_called_once()


def test_alembic_config_pins_url_and_live_validator(
    alembic_config: Config,
) -> None:
    """Pass the test URL and live identity guard through explicit attributes."""
    assert alembic_config.attributes["database_url"] == (
        database_test_support.test_database_url()
    )
    assert alembic_config.attributes["connection_validator"] is (
        assert_safe_test_connection
    )


def test_required_extensions_are_installed(database_connection: Connection) -> None:
    """The initial migration installs the approved PostgreSQL extensions."""

    assert _installed_extensions(database_connection) == REQUIRED_EXTENSIONS


def test_extension_versions_match_compose_contract(
    database_connection: Connection,
) -> None:
    """Pin pgvector and PostgreSQL extension versions used by the test image."""
    assert _installed_extension_versions(database_connection) == (
        EXPECTED_EXTENSION_VERSIONS
    )


def test_rendered_compose_contract_is_exact() -> None:
    """Keep the rendered test database container isolated and reproducible."""
    completed = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            "compose.test.yaml",
            "config",
            "--format",
            "json",
        ],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    rendered_config = json.loads(completed.stdout)

    assert set(rendered_config["services"]) == {"db"}
    database_service = rendered_config["services"]["db"]
    assert database_service["image"] == EXPECTED_DATABASE_IMAGE
    assert database_service["ports"] == [
        {
            "mode": "ingress",
            "host_ip": "127.0.0.1",
            "target": 5432,
            "published": "55432",
            "protocol": "tcp",
        }
    ]
    assert database_service["tmpfs"] == ["/var/lib/postgresql"]
    assert database_service["healthcheck"] == {
        "test": [
            "CMD-SHELL",
            "pg_isready -U omf_retrieval_test -d omf_retrieval_test",
        ],
        "timeout": "5s",
        "interval": "2s",
        "retries": 10,
    }


def test_alembic_revision_is_at_head(
    database_connection: Connection,
    alembic_config: Config,
) -> None:
    """The database revision matches the migration script head."""
    applied_revision = database_connection.execute(
        text("SELECT version_num FROM alembic_version")
    ).scalar_one()
    expected_revision = ScriptDirectory.from_config(alembic_config).get_current_head()

    assert applied_revision == expected_revision


def test_application_table_set_is_exact(database_connection: Connection) -> None:
    """The initial migration creates exactly the approved application tables."""
    table_names = _application_tables(database_connection)

    assert table_names == APPLICATION_TABLES


def test_column_type_and_nullability_catalog_is_exact(
    database_connection: Connection,
) -> None:
    """Every application column has the approved physical type and nullability."""
    rows = database_connection.execute(
        text(
            "SELECT c.relname, a.attname, "
            "format_type(a.atttypid, a.atttypmod), a.attnotnull "
            "FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "JOIN pg_attribute a ON a.attrelid = c.oid "
            "WHERE n.nspname = 'public' AND c.relkind = 'r' "
            "AND c.relname <> 'alembic_version' "
            "AND a.attnum > 0 AND NOT a.attisdropped"
        )
    )
    actual = {table_name: set() for table_name in APPLICATION_TABLES}
    for table_name, column_name, type_name, required in rows:
        nullability = "required" if required else "nullable"
        actual[table_name].add(f"{column_name} {type_name} {nullability}")

    assert actual == EXPECTED_COLUMNS


def test_uuid_identifiers_have_no_database_default(
    database_connection: Connection,
) -> None:
    """Application-assigned UUID identifiers never get a database-side default."""
    defaults = database_connection.execute(
        text(
            "SELECT table_name, column_default FROM information_schema.columns "
            "WHERE table_schema = 'public' AND column_name = 'id' "
            "AND table_name <> 'alembic_version'"
        )
    ).all()

    assert {table_name for table_name, _ in defaults} == APPLICATION_TABLES - {
        "client_source_grants"
    }
    assert all(default is None for _, default in defaults)


def test_constraint_and_explicit_index_catalog_is_exact(
    database_connection: Connection,
) -> None:
    """Constraint and index structure exactly matches the approved physical model."""
    constraint_rows = database_connection.execute(
        text(
            "SELECT con.conname, rel.relname, con.contype, "
            "ARRAY(SELECT att.attname FROM unnest(con.conkey) WITH ORDINALITY "
            "AS key(attnum, ord) JOIN pg_attribute att "
            "ON att.attrelid = con.conrelid AND att.attnum = key.attnum "
            "ORDER BY key.ord), ref.relname, "
            "ARRAY(SELECT att.attname FROM unnest(con.confkey) WITH ORDINALITY "
            "AS key(attnum, ord) JOIN pg_attribute att "
            "ON att.attrelid = con.confrelid AND att.attnum = key.attnum "
            "ORDER BY key.ord), "
            "CASE WHEN con.contype = 'f' THEN con.confdeltype::text END, "
            "pg_get_constraintdef(con.oid, true) "
            "FROM pg_constraint con "
            "JOIN pg_class rel ON rel.oid = con.conrelid "
            "JOIN pg_namespace n ON n.oid = rel.relnamespace "
            "LEFT JOIN pg_class ref ON ref.oid = con.confrelid "
            "WHERE n.nspname = 'public' AND rel.relname <> 'alembic_version' "
            "AND con.contype <> 'n'"
        )
    ).all()
    key_constraints = {}
    check_constraints = {}
    for (
        name,
        table_name,
        constraint_type,
        source_columns,
        referenced_table,
        referenced_columns,
        delete_action,
        definition,
    ) in constraint_rows:
        if constraint_type == "c":
            check_constraints[name] = (
                table_name,
                tuple(source_columns),
                definition,
            )
        else:
            key_constraints[name] = (
                table_name,
                constraint_type,
                tuple(source_columns),
                referenced_table,
                tuple(referenced_columns),
                delete_action,
            )

    index_rows = database_connection.execute(
        text(
            "SELECT idx.relname, rel.relname, am.amname, "
            "ARRAY(SELECT pg_get_indexdef(i.indexrelid, position, true) "
            "FROM generate_series(1, i.indnkeyatts) position), "
            "ARRAY(SELECT opc.opcname FROM unnest(i.indclass::oid[]) "
            "WITH ORDINALITY classes(opcoid, ord) "
            "JOIN pg_opclass opc ON opc.oid = classes.opcoid "
            "WHERE classes.ord <= i.indnkeyatts ORDER BY classes.ord) "
            "FROM pg_index i "
            "JOIN pg_class idx ON idx.oid = i.indexrelid "
            "JOIN pg_class rel ON rel.oid = i.indrelid "
            "JOIN pg_namespace n ON n.oid = rel.relnamespace "
            "JOIN pg_am am ON am.oid = idx.relam "
            "LEFT JOIN pg_constraint con ON con.conindid = i.indexrelid "
            "WHERE n.nspname = 'public' AND rel.relname <> 'alembic_version' "
            "AND con.oid IS NULL"
        )
    ).all()
    indexes = {
        name: (table_name, method, tuple(columns), tuple(opclasses))
        for name, table_name, method, columns, opclasses in index_rows
    }

    default_rows = database_connection.execute(
        text(
            "SELECT table_name, column_name, column_default "
            "FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name <> 'alembic_version' "
            "AND column_name <> 'id' AND column_default IS NOT NULL"
        )
    ).all()
    defaults = {
        (table_name, column_name): default
        for table_name, column_name, default in default_rows
    }

    assert key_constraints == EXPECTED_KEY_CONSTRAINTS
    assert check_constraints == EXPECTED_CHECK_CONSTRAINTS
    assert indexes == EXPECTED_EXPLICIT_INDEXES
    assert defaults == EXPECTED_NON_ID_DEFAULTS


def test_trigram_gin_index_exists_and_ann_indexes_do_not(
    database_connection: Connection,
) -> None:
    """Keyword search uses trigram GIN without any approximate vector index."""
    index_catalog = database_connection.execute(
        text(
            "SELECT idx.relname, am.amname, coalesce(opc.opcname, '') "
            "FROM pg_index i "
            "JOIN pg_class idx ON idx.oid = i.indexrelid "
            "JOIN pg_class rel ON rel.oid = i.indrelid "
            "JOIN pg_am am ON am.oid = idx.relam "
            "LEFT JOIN pg_opclass opc ON opc.oid = i.indclass[0] "
            "JOIN pg_namespace n ON n.oid = rel.relnamespace "
            "WHERE n.nspname = 'public'"
        )
    ).all()

    assert (
        "ix_chunks_search_text_trgm",
        "gin",
        "gin_trgm_ops",
    ) in index_catalog
    assert not any(
        method in {"hnsw", "ivfflat"} or opclass.startswith("vector_")
        for _, method, opclass in index_catalog
    )


def test_extensions_survive_downgrade_and_reupgrade(
    database_connection: Connection,
    downgraded_database: None,
) -> None:
    """Downgrade preserves extensions and the revision upgrades again."""
    assert _installed_extensions(database_connection) == REQUIRED_EXTENSIONS
    assert _application_tables(database_connection) == set()
