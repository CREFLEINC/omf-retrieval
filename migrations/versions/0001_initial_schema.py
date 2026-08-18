"""Create the PostgreSQL schema and extensions required by retrieval.

Revision ID: 0001_initial_schema
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial_schema"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Install search extensions and create the retrieval schema."""
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "source_profiles",
        sa.Column("id", postgresql.UUID(), nullable=False),
        sa.Column("source_key", sa.Text(), nullable=False),
        sa.Column(
            "include_patterns",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "exclude_patterns",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("active_index_run_id", postgresql.UUID(), nullable=True),
        sa.CheckConstraint(
            "source_key ~ '[^[:space:]]'",
            name="ck_source_profiles_source_key_non_blank",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(include_patterns) = 'array'",
            name="ck_source_profiles_include_patterns_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(exclude_patterns) = 'array'",
            name="ck_source_profiles_exclude_patterns_array",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_source_profiles"),
        sa.UniqueConstraint("source_key", name="uq_source_profiles_source_key"),
    )
    op.create_table(
        "index_configs",
        sa.Column("id", postgresql.UUID(), nullable=False),
        sa.Column("config_hash", sa.String(length=64), nullable=False),
        sa.Column("parser_config", postgresql.JSONB(), nullable=False),
        sa.Column("chunk_config", postgresql.JSONB(), nullable=False),
        sa.Column("tokenizer_config", postgresql.JSONB(), nullable=False),
        sa.Column("embedding_config", postgresql.JSONB(), nullable=False),
        sa.Column("rrf_config", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint(
            "config_hash ~ '^[0-9a-f]{64}$'",
            name="ck_index_configs_config_hash_sha256",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(parser_config) = 'object'",
            name="ck_index_configs_parser_config_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(chunk_config) = 'object'",
            name="ck_index_configs_chunk_config_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(tokenizer_config) = 'object'",
            name="ck_index_configs_tokenizer_config_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(embedding_config) = 'object'",
            name="ck_index_configs_embedding_config_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(rrf_config) = 'object'",
            name="ck_index_configs_rrf_config_object",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_index_configs"),
        sa.UniqueConstraint("config_hash", name="uq_index_configs_config_hash"),
    )
    op.create_table(
        "index_runs",
        sa.Column("id", postgresql.UUID(), nullable=False),
        sa.Column("source_profile_id", postgresql.UUID(), nullable=False),
        sa.Column("index_config_id", postgresql.UUID(), nullable=False),
        sa.Column("commit_sha", sa.String(length=40), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'building'"),
            nullable=False,
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "stats",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("failure_code", sa.Text(), nullable=True),
        sa.Column("failure_detail", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "commit_sha ~ '^[0-9a-f]{40}$'", name="ck_index_runs_commit_sha_git"
        ),
        sa.CheckConstraint(
            "status IN ('building', 'ready', 'active', 'previous', 'failed')",
            name="ck_index_runs_status",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(stats) = 'object'", name="ck_index_runs_stats_object"
        ),
        sa.ForeignKeyConstraint(
            ["source_profile_id"],
            ["source_profiles.id"],
            name="fk_index_runs_source_profile_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["index_config_id"],
            ["index_configs.id"],
            name="fk_index_runs_index_config_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_index_runs"),
        sa.UniqueConstraint(
            "source_profile_id", "id", name="uq_index_runs_source_profile_id_id"
        ),
    )
    op.create_table(
        "document_contents",
        sa.Column("id", postgresql.UUID(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_document_contents_content_hash_sha256",
        ),
        sa.CheckConstraint(
            "byte_size >= 0", name="ck_document_contents_byte_size_nonnegative"
        ),
        sa.CheckConstraint(
            "byte_size = octet_length(content)",
            name="ck_document_contents_byte_size_matches_content",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_document_contents"),
        sa.UniqueConstraint("content_hash", name="uq_document_contents_content_hash"),
    )
    op.create_table(
        "document_occurrences",
        sa.Column("id", postgresql.UUID(), nullable=False),
        sa.Column("run_id", postgresql.UUID(), nullable=False),
        sa.Column("content_id", postgresql.UUID(), nullable=False),
        sa.Column("source_path", sa.Text(), nullable=False),
        sa.Column("version_scope", sa.Text(), nullable=False),
        sa.Column("document_date", sa.Date(), nullable=True),
        sa.Column("document_version", sa.Text(), nullable=True),
        sa.Column("decision_state", sa.Text(), nullable=False),
        sa.Column("owner_domain", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "source_path ~ '[^[:space:]]'",
            name="ck_document_occurrences_source_path_non_blank",
        ),
        sa.CheckConstraint(
            "version_scope IN ('current', 'historical')",
            name="ck_document_occurrences_version_scope",
        ),
        sa.CheckConstraint(
            "decision_state IN ('confirmed', 'draft', 'unknown')",
            name="ck_document_occurrences_decision_state",
        ),
        sa.CheckConstraint(
            "owner_domain IN ('docs', 'uiux')",
            name="ck_document_occurrences_owner_domain",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["index_runs.id"],
            name="fk_document_occurrences_run_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["content_id"],
            ["document_contents.id"],
            name="fk_document_occurrences_content_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_document_occurrences"),
        sa.UniqueConstraint(
            "run_id", "source_path", name="uq_document_occurrences_run_id_source_path"
        ),
        sa.UniqueConstraint("run_id", "id", name="uq_document_occurrences_run_id_id"),
    )
    op.create_table(
        "document_parses",
        sa.Column("id", postgresql.UUID(), nullable=False),
        sa.Column("content_id", postgresql.UUID(), nullable=False),
        sa.Column("parser_version", sa.Text(), nullable=False),
        sa.Column("chunk_config_hash", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "parser_version ~ '[^[:space:]]'",
            name="ck_document_parses_parser_version_non_blank",
        ),
        sa.CheckConstraint(
            "chunk_config_hash ~ '^[0-9a-f]{64}$'",
            name="ck_document_parses_chunk_config_hash_sha256",
        ),
        sa.ForeignKeyConstraint(
            ["content_id"],
            ["document_contents.id"],
            name="fk_document_parses_content_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_document_parses"),
        sa.UniqueConstraint(
            "content_id",
            "parser_version",
            "chunk_config_hash",
            name="uq_document_parses_content_parser_chunk_config",
        ),
    )
    op.create_table(
        "sections",
        sa.Column("id", postgresql.UUID(), nullable=False),
        sa.Column("parse_id", postgresql.UUID(), nullable=False),
        sa.Column("parent_section_id", postgresql.UUID(), nullable=True),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("level", sa.SmallInteger(), nullable=False),
        sa.Column("heading", sa.Text(), nullable=True),
        sa.Column("heading_path", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("line_start", sa.Integer(), nullable=False),
        sa.Column("line_end", sa.Integer(), nullable=False),
        sa.CheckConstraint("ordinal >= 0", name="ck_sections_ordinal_nonnegative"),
        sa.CheckConstraint("level >= 0 AND level <= 6", name="ck_sections_level_range"),
        sa.CheckConstraint(
            "line_start >= 1 AND line_end >= line_start",
            name="ck_sections_line_range",
        ),
        sa.ForeignKeyConstraint(
            ["parse_id"],
            ["document_parses.id"],
            name="fk_sections_parse_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parse_id", "parent_section_id"],
            ["sections.parse_id", "sections.id"],
            name="fk_sections_parse_id_parent_section_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_sections"),
        sa.UniqueConstraint("parse_id", "ordinal", name="uq_sections_parse_id_ordinal"),
        sa.UniqueConstraint("parse_id", "id", name="uq_sections_parse_id_id"),
    )
    op.create_table(
        "chunks",
        sa.Column("id", postgresql.UUID(), nullable=False),
        sa.Column("section_id", postgresql.UUID(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("search_text", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("line_start", sa.Integer(), nullable=False),
        sa.Column("line_end", sa.Integer(), nullable=False),
        sa.Column("chunk_hash", sa.String(length=64), nullable=False),
        sa.CheckConstraint("ordinal >= 0", name="ck_chunks_ordinal_nonnegative"),
        sa.CheckConstraint(
            "token_count >= 0", name="ck_chunks_token_count_nonnegative"
        ),
        sa.CheckConstraint(
            "line_start >= 1 AND line_end >= line_start",
            name="ck_chunks_line_range",
        ),
        sa.CheckConstraint(
            "chunk_hash ~ '^[0-9a-f]{64}$'", name="ck_chunks_chunk_hash_sha256"
        ),
        sa.ForeignKeyConstraint(
            ["section_id"],
            ["sections.id"],
            name="fk_chunks_section_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_chunks"),
        sa.UniqueConstraint(
            "section_id", "ordinal", name="uq_chunks_section_id_ordinal"
        ),
    )
    op.create_table(
        "chunk_embeddings",
        sa.Column("id", postgresql.UUID(), nullable=False),
        sa.Column("chunk_id", postgresql.UUID(), nullable=False),
        sa.Column("embedding_config_hash", sa.String(length=64), nullable=False),
        sa.Column("model_name", sa.Text(), nullable=False),
        sa.Column("model_revision", sa.Text(), nullable=False),
        sa.Column("dimension", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'ready'"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "embedding_config_hash ~ '^[0-9a-f]{64}$'",
            name="ck_chunk_embeddings_config_hash_sha256",
        ),
        sa.CheckConstraint(
            "model_name ~ '[^[:space:]]'",
            name="ck_chunk_embeddings_model_name_non_blank",
        ),
        sa.CheckConstraint(
            "model_revision ~ '[^[:space:]]'",
            name="ck_chunk_embeddings_model_revision_non_blank",
        ),
        sa.CheckConstraint(
            "dimension > 0", name="ck_chunk_embeddings_dimension_positive"
        ),
        sa.CheckConstraint("status = 'ready'", name="ck_chunk_embeddings_status_ready"),
        sa.CheckConstraint(
            "vector_dims(embedding) = dimension",
            name="ck_chunk_embeddings_vector_dimension",
        ),
        sa.ForeignKeyConstraint(
            ["chunk_id"],
            ["chunks.id"],
            name="fk_chunk_embeddings_chunk_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_chunk_embeddings"),
        sa.UniqueConstraint(
            "chunk_id",
            "embedding_config_hash",
            name="uq_chunk_embeddings_chunk_id_config_hash",
        ),
    )
    op.create_table(
        "document_relations",
        sa.Column("id", postgresql.UUID(), nullable=False),
        sa.Column("run_id", postgresql.UUID(), nullable=False),
        sa.Column("from_occurrence_id", postgresql.UUID(), nullable=False),
        sa.Column("to_occurrence_id", postgresql.UUID(), nullable=False),
        sa.Column("relation_type", sa.Text(), nullable=False),
        sa.Column("evidence_source_path", sa.Text(), nullable=False),
        sa.Column("evidence_line_start", sa.Integer(), nullable=False),
        sa.Column("evidence_line_end", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "relation_type IN ('supersedes', 'potential_conflict')",
            name="ck_document_relations_relation_type",
        ),
        sa.CheckConstraint(
            "evidence_source_path ~ '[^[:space:]]'",
            name="ck_document_relations_evidence_source_path_non_blank",
        ),
        sa.CheckConstraint(
            "evidence_line_start >= 1 AND evidence_line_end >= evidence_line_start",
            name="ck_document_relations_evidence_line_range",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["index_runs.id"],
            name="fk_document_relations_run_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "from_occurrence_id"],
            ["document_occurrences.run_id", "document_occurrences.id"],
            name="fk_document_relations_from_occurrence",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "to_occurrence_id"],
            ["document_occurrences.run_id", "document_occurrences.id"],
            name="fk_document_relations_to_occurrence",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "evidence_source_path"],
            [
                "document_occurrences.run_id",
                "document_occurrences.source_path",
            ],
            name="fk_document_relations_evidence_occurrence",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_document_relations"),
    )
    op.create_table(
        "api_clients",
        sa.Column("id", postgresql.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("key_id", sa.String(length=16), nullable=False),
        sa.Column("token_hash", postgresql.BYTEA(), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "name ~ '[^[:space:]]'", name="ck_api_clients_name_non_blank"
        ),
        sa.CheckConstraint(
            "char_length(key_id) = 16", name="ck_api_clients_key_id_length"
        ),
        sa.CheckConstraint(
            "octet_length(token_hash) = 32",
            name="ck_api_clients_token_hash_length",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'disabled', 'revoked')",
            name="ck_api_clients_status",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_api_clients"),
        sa.UniqueConstraint("key_id", name="uq_api_clients_key_id"),
    )
    op.create_table(
        "client_source_grants",
        sa.Column("client_id", postgresql.UUID(), nullable=False),
        sa.Column("source_profile_id", postgresql.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["api_clients.id"],
            name="fk_client_source_grants_client_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_profile_id"],
            ["source_profiles.id"],
            name="fk_client_source_grants_source_profile_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "client_id", "source_profile_id", name="pk_client_source_grants"
        ),
    )
    op.create_table(
        "search_audit_events",
        sa.Column("id", postgresql.UUID(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("request_id", sa.Text(), nullable=False),
        sa.Column("client_id", postgresql.UUID(), nullable=False),
        sa.Column("source_profile_id", postgresql.UUID(), nullable=False),
        sa.Column("query_hmac", postgresql.BYTEA(), nullable=False),
        sa.Column(
            "filters",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "returned_chunk_ids",
            postgresql.ARRAY(postgresql.UUID()),
            server_default=sa.text("'{}'::uuid[]"),
            nullable=False,
        ),
        sa.Column("commit_sha", sa.String(length=40), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("result_count", sa.Integer(), nullable=False),
        sa.Column("embedding_ms", sa.Integer(), nullable=False),
        sa.Column("keyword_ms", sa.Integer(), nullable=False),
        sa.Column("vector_ms", sa.Integer(), nullable=False),
        sa.Column("rrf_ms", sa.Integer(), nullable=False),
        sa.Column("total_ms", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "request_id ~ '[^[:space:]]'",
            name="ck_search_audit_events_request_id_non_blank",
        ),
        sa.CheckConstraint(
            "octet_length(query_hmac) = 32",
            name="ck_search_audit_events_query_hmac_length",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(filters) = 'object'",
            name="ck_search_audit_events_filters_object",
        ),
        sa.CheckConstraint(
            "commit_sha ~ '^[0-9a-f]{40}$'",
            name="ck_search_audit_events_commit_sha_git",
        ),
        sa.CheckConstraint(
            "status IN ('ok', 'no_evidence')",
            name="ck_search_audit_events_status",
        ),
        sa.CheckConstraint(
            "result_count >= 0",
            name="ck_search_audit_events_result_count_nonnegative",
        ),
        sa.CheckConstraint(
            "embedding_ms >= 0",
            name="ck_search_audit_events_embedding_ms_nonnegative",
        ),
        sa.CheckConstraint(
            "keyword_ms >= 0",
            name="ck_search_audit_events_keyword_ms_nonnegative",
        ),
        sa.CheckConstraint(
            "vector_ms >= 0",
            name="ck_search_audit_events_vector_ms_nonnegative",
        ),
        sa.CheckConstraint(
            "rrf_ms >= 0", name="ck_search_audit_events_rrf_ms_nonnegative"
        ),
        sa.CheckConstraint(
            "total_ms >= 0", name="ck_search_audit_events_total_ms_nonnegative"
        ),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["api_clients.id"],
            name="fk_search_audit_events_client_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_profile_id"],
            ["source_profiles.id"],
            name="fk_search_audit_events_source_profile_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_search_audit_events"),
    )

    op.create_foreign_key(
        "fk_source_profiles_active_index_run",
        "source_profiles",
        "index_runs",
        ["id", "active_index_run_id"],
        ["source_profile_id", "id"],
        ondelete="RESTRICT",
    )

    op.create_index(
        "ix_chunks_search_text_trgm",
        "chunks",
        ["search_text"],
        postgresql_using="gin",
        postgresql_ops={"search_text": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_source_profiles_active_index_run_id",
        "source_profiles",
        ["active_index_run_id"],
    )
    op.create_index(
        "ix_index_runs_source_status",
        "index_runs",
        ["source_profile_id", "status"],
    )
    op.create_index("ix_index_runs_index_config_id", "index_runs", ["index_config_id"])
    op.create_index(
        "ix_document_occurrences_content_id",
        "document_occurrences",
        ["content_id"],
    )
    op.create_index("ix_sections_parent_section_id", "sections", ["parent_section_id"])
    op.create_index(
        "ix_document_relations_from",
        "document_relations",
        ["run_id", "from_occurrence_id"],
    )
    op.create_index(
        "ix_document_relations_to",
        "document_relations",
        ["run_id", "to_occurrence_id"],
    )
    op.create_index(
        "ix_document_relations_evidence",
        "document_relations",
        ["run_id", "evidence_source_path"],
    )
    op.create_index(
        "ix_client_source_grants_source_profile_id",
        "client_source_grants",
        ["source_profile_id"],
    )
    op.create_index(
        "ix_search_audit_events_client_id", "search_audit_events", ["client_id"]
    )
    op.create_index(
        "ix_search_audit_events_source_profile_id",
        "search_audit_events",
        ["source_profile_id"],
    )


def downgrade() -> None:
    """Drop application tables while preserving shared extensions."""
    op.execute(
        "ALTER TABLE source_profiles "
        "DROP CONSTRAINT IF EXISTS fk_source_profiles_active_index_run"
    )
    op.drop_table("search_audit_events")
    op.drop_table("client_source_grants")
    op.drop_table("api_clients")
    op.drop_table("document_relations")
    op.drop_table("chunk_embeddings")
    op.drop_table("chunks")
    op.drop_table("sections")
    op.drop_table("document_parses")
    op.drop_table("document_occurrences")
    op.drop_table("document_contents")
    op.drop_table("index_runs")
    op.drop_table("index_configs")
    op.drop_table("source_profiles")
