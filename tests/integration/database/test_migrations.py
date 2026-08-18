"""Integration tests for the PostgreSQL migration lifecycle."""

import os
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, create_engine, make_url, text

TEST_DATABASE_URL = (
    "postgresql+psycopg://omf_retrieval_test:omf_retrieval_test@"
    "127.0.0.1:55432/omf_retrieval_test"
)
REQUIRED_EXTENSIONS = {"pg_trgm", "vector"}
OVERRIDE_DATABASE_URL = f"{TEST_DATABASE_URL}?application_name=override-fixture"
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
EXPECTED_CONSTRAINTS = {
    *(f"pk_{table_name}" for table_name in APPLICATION_TABLES),
    "ck_api_clients_key_id_length",
    "ck_api_clients_name_non_blank",
    "ck_api_clients_status",
    "ck_api_clients_token_hash_length",
    "ck_chunk_embeddings_config_hash_sha256",
    "ck_chunk_embeddings_dimension_positive",
    "ck_chunk_embeddings_model_name_non_blank",
    "ck_chunk_embeddings_model_revision_non_blank",
    "ck_chunk_embeddings_status_ready",
    "ck_chunk_embeddings_vector_dimension",
    "ck_chunks_chunk_hash_sha256",
    "ck_chunks_line_range",
    "ck_chunks_ordinal_nonnegative",
    "ck_chunks_token_count_nonnegative",
    "ck_document_contents_byte_size_matches_content",
    "ck_document_contents_byte_size_nonnegative",
    "ck_document_contents_content_hash_sha256",
    "ck_document_occurrences_decision_state",
    "ck_document_occurrences_owner_domain",
    "ck_document_occurrences_source_path_non_blank",
    "ck_document_occurrences_version_scope",
    "ck_document_parses_chunk_config_hash_sha256",
    "ck_document_parses_parser_version_non_blank",
    "ck_document_relations_evidence_line_range",
    "ck_document_relations_evidence_source_path_non_blank",
    "ck_document_relations_relation_type",
    "ck_index_configs_chunk_config_object",
    "ck_index_configs_config_hash_sha256",
    "ck_index_configs_embedding_config_object",
    "ck_index_configs_parser_config_object",
    "ck_index_configs_rrf_config_object",
    "ck_index_configs_tokenizer_config_object",
    "ck_index_runs_commit_sha_git",
    "ck_index_runs_stats_object",
    "ck_index_runs_status",
    "ck_search_audit_events_commit_sha_git",
    "ck_search_audit_events_embedding_ms_nonnegative",
    "ck_search_audit_events_filters_object",
    "ck_search_audit_events_keyword_ms_nonnegative",
    "ck_search_audit_events_query_hmac_length",
    "ck_search_audit_events_request_id_non_blank",
    "ck_search_audit_events_result_count_nonnegative",
    "ck_search_audit_events_rrf_ms_nonnegative",
    "ck_search_audit_events_status",
    "ck_search_audit_events_total_ms_nonnegative",
    "ck_search_audit_events_vector_ms_nonnegative",
    "ck_sections_level_range",
    "ck_sections_line_range",
    "ck_sections_ordinal_nonnegative",
    "ck_source_profiles_exclude_patterns_array",
    "ck_source_profiles_include_patterns_array",
    "ck_source_profiles_source_key_non_blank",
    "fk_chunk_embeddings_chunk_id",
    "fk_chunks_section_id",
    "fk_client_source_grants_client_id",
    "fk_client_source_grants_source_profile_id",
    "fk_document_occurrences_content_id",
    "fk_document_occurrences_run_id",
    "fk_document_parses_content_id",
    "fk_document_relations_evidence_occurrence",
    "fk_document_relations_from_occurrence",
    "fk_document_relations_run_id",
    "fk_document_relations_to_occurrence",
    "fk_index_runs_index_config_id",
    "fk_index_runs_source_profile_id",
    "fk_search_audit_events_client_id",
    "fk_search_audit_events_source_profile_id",
    "fk_sections_parse_id",
    "fk_sections_parse_id_parent_section_id",
    "fk_source_profiles_active_index_run",
    "uq_api_clients_key_id",
    "uq_chunk_embeddings_chunk_id_config_hash",
    "uq_chunks_section_id_ordinal",
    "uq_document_contents_content_hash",
    "uq_document_occurrences_run_id_id",
    "uq_document_occurrences_run_id_source_path",
    "uq_document_parses_content_parser_chunk_config",
    "uq_index_configs_config_hash",
    "uq_index_runs_source_profile_id_id",
    "uq_sections_parse_id_id",
    "uq_sections_parse_id_ordinal",
    "uq_source_profiles_source_key",
}
EXPLICIT_INDEXES = {
    "ix_chunks_search_text_trgm",
    "ix_client_source_grants_source_profile_id",
    "ix_document_occurrences_content_id",
    "ix_document_relations_evidence",
    "ix_document_relations_from",
    "ix_document_relations_to",
    "ix_index_runs_index_config_id",
    "ix_index_runs_source_status",
    "ix_search_audit_events_client_id",
    "ix_search_audit_events_source_profile_id",
    "ix_sections_parent_section_id",
    "ix_source_profiles_active_index_run_id",
}
EXPECTED_FK_DELETE_ACTIONS = {
    "fk_chunk_embeddings_chunk_id": "c",
    "fk_chunks_section_id": "c",
    "fk_client_source_grants_client_id": "c",
    "fk_client_source_grants_source_profile_id": "r",
    "fk_document_occurrences_content_id": "r",
    "fk_document_occurrences_run_id": "c",
    "fk_document_parses_content_id": "c",
    "fk_document_relations_evidence_occurrence": "c",
    "fk_document_relations_from_occurrence": "c",
    "fk_document_relations_run_id": "c",
    "fk_document_relations_to_occurrence": "c",
    "fk_index_runs_index_config_id": "r",
    "fk_index_runs_source_profile_id": "r",
    "fk_search_audit_events_client_id": "r",
    "fk_search_audit_events_source_profile_id": "r",
    "fk_sections_parse_id": "c",
    "fk_sections_parse_id_parent_section_id": "c",
    "fk_source_profiles_active_index_run": "r",
}


@pytest.fixture
def database_connection() -> Iterator[Connection]:
    """Yield a live connection to the isolated integration-test database."""
    database_url = os.getenv("OMF_RETRIEVAL_DATABASE_URL", TEST_DATABASE_URL)
    engine = create_engine(database_url)
    with engine.connect() as connection:
        yield connection
    engine.dispose()


def _installed_extensions(connection: Connection) -> set[str]:
    return set(
        connection.execute(
            text(
                "SELECT extname FROM pg_extension "
                "WHERE extname IN ('vector', 'pg_trgm')"
            )
        ).scalars()
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
    """Return the repository Alembic configuration."""
    return Config("alembic.ini")


def test_database_connection_prefers_environment_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The integration fixture follows the Alembic database URL override."""
    monkeypatch.setenv("OMF_RETRIEVAL_DATABASE_URL", OVERRIDE_DATABASE_URL)
    connection_iterator = database_connection.__wrapped__()
    connection = next(connection_iterator)

    try:
        assert connection.engine.url == make_url(OVERRIDE_DATABASE_URL)
    finally:
        connection_iterator.close()


def test_required_extensions_are_installed(database_connection: Connection) -> None:
    """The initial migration installs the approved PostgreSQL extensions."""

    assert _installed_extensions(database_connection) == REQUIRED_EXTENSIONS


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
    """All database constraints and explicit indexes have stable approved names."""
    constraint_names = set(
        database_connection.execute(
            text(
                "SELECT con.conname FROM pg_constraint con "
                "JOIN pg_class rel ON rel.oid = con.conrelid "
                "JOIN pg_namespace n ON n.oid = rel.relnamespace "
                "WHERE n.nspname = 'public' AND rel.relname <> 'alembic_version' "
                "AND con.contype <> 'n'"
            )
        ).scalars()
    )
    index_names = set(
        database_connection.execute(
            text(
                "SELECT idx.relname FROM pg_index i "
                "JOIN pg_class idx ON idx.oid = i.indexrelid "
                "JOIN pg_class rel ON rel.oid = i.indrelid "
                "JOIN pg_namespace n ON n.oid = rel.relnamespace "
                "LEFT JOIN pg_constraint con ON con.conindid = i.indexrelid "
                "WHERE n.nspname = 'public' AND rel.relname <> 'alembic_version' "
                "AND con.oid IS NULL"
            )
        ).scalars()
    )
    foreign_key_delete_actions = dict(
        database_connection.execute(
            text(
                "SELECT conname, confdeltype FROM pg_constraint con "
                "JOIN pg_class rel ON rel.oid = con.conrelid "
                "JOIN pg_namespace n ON n.oid = rel.relnamespace "
                "WHERE n.nspname = 'public' AND con.contype = 'f'"
            )
        ).all()
    )

    assert constraint_names == EXPECTED_CONSTRAINTS
    assert index_names == EXPLICIT_INDEXES
    assert foreign_key_delete_actions == EXPECTED_FK_DELETE_ACTIONS


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
    alembic_config: Config,
) -> None:
    """Downgrade preserves extensions and the revision upgrades again."""
    command.downgrade(alembic_config, "base")

    assert _installed_extensions(database_connection) == REQUIRED_EXTENSIONS
    assert _application_tables(database_connection) == set()

    command.upgrade(alembic_config, "head")
    applied_revision = database_connection.execute(
        text("SELECT version_num FROM alembic_version")
    ).scalar_one()
    expected_revision = ScriptDirectory.from_config(alembic_config).get_current_head()

    assert applied_revision == expected_revision
    assert _application_tables(database_connection) == APPLICATION_TABLES
