"""Typed SQLAlchemy mappings for the OMF Retrieval application schema."""

import uuid
from datetime import date, datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from pgvector.sqlalchemy import VECTOR
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from omf_retrieval.infrastructure.database.base import Base


class SourceProfile(Base):
    """A configured document source and its active index snapshot."""

    __tablename__ = "source_profiles"
    __table_args__ = (
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
        sa.ForeignKeyConstraint(
            ["id", "active_index_run_id"],
            ["index_runs.source_profile_id", "index_runs.id"],
            name="fk_source_profiles_active_index_run",
            ondelete="RESTRICT",
        ),
        sa.Index(
            "ix_source_profiles_active_index_run_id",
            "active_index_run_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(postgresql.UUID(), default=uuid.uuid4)
    source_key: Mapped[str] = mapped_column(sa.Text())
    include_patterns: Mapped[list[str]] = mapped_column(
        postgresql.JSONB(), server_default=sa.text("'[]'::jsonb")
    )
    exclude_patterns: Mapped[list[str]] = mapped_column(
        postgresql.JSONB(), server_default=sa.text("'[]'::jsonb")
    )
    active_index_run_id: Mapped[UUID | None] = mapped_column(postgresql.UUID())


class SearchPolicyManifest(Base):
    """An immutable canonical query-time search policy snapshot."""

    __tablename__ = "search_policy_manifests"
    __table_args__ = (
        sa.CheckConstraint(
            "config_hash ~ '^[0-9a-f]{64}$'",
            name="ck_search_policy_manifests_config_hash_sha256",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(snapshot) = 'object'",
            name="ck_search_policy_manifests_snapshot_object",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_search_policy_manifests"),
        sa.UniqueConstraint(
            "config_hash",
            name="uq_search_policy_manifests_config_hash",
        ),
    )

    id: Mapped[UUID] = mapped_column(postgresql.UUID(), default=uuid.uuid4)
    config_hash: Mapped[str] = mapped_column(sa.String(64))
    snapshot: Mapped[dict[str, Any]] = mapped_column(postgresql.JSONB())
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.text("now()")
    )


class IndexConfig(Base):
    """An immutable parser, chunking, embedding, and ranking configuration."""

    __tablename__ = "index_configs"
    __table_args__ = (
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

    id: Mapped[UUID] = mapped_column(postgresql.UUID(), default=uuid.uuid4)
    config_hash: Mapped[str] = mapped_column(sa.String(64))
    parser_config: Mapped[dict[str, Any]] = mapped_column(postgresql.JSONB())
    chunk_config: Mapped[dict[str, Any]] = mapped_column(postgresql.JSONB())
    tokenizer_config: Mapped[dict[str, Any]] = mapped_column(postgresql.JSONB())
    embedding_config: Mapped[dict[str, Any]] = mapped_column(postgresql.JSONB())
    rrf_config: Mapped[dict[str, Any]] = mapped_column(postgresql.JSONB())


class IndexRun(Base):
    """One indexing snapshot built from a source and configuration."""

    __tablename__ = "index_runs"
    __table_args__ = (
        sa.CheckConstraint(
            "commit_sha ~ '^[0-9a-f]{40}$'",
            name="ck_index_runs_commit_sha_git",
        ),
        sa.CheckConstraint(
            "status IN ('building', 'ready', 'active', 'previous', 'archived', "
            "'failed')",
            name="ck_index_runs_status",
        ),
        sa.CheckConstraint(
            "status NOT IN ('active', 'previous', 'archived') "
            "OR activated_at IS NOT NULL",
            name="ck_index_runs_lifecycle_activated_at",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(stats) = 'object'",
            name="ck_index_runs_stats_object",
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
            "source_profile_id",
            "id",
            name="uq_index_runs_source_profile_id_id",
        ),
        sa.Index("ix_index_runs_source_status", "source_profile_id", "status"),
        sa.Index("ix_index_runs_index_config_id", "index_config_id"),
        sa.Index(
            "uq_index_runs_one_active_per_source",
            "source_profile_id",
            unique=True,
            postgresql_where=sa.text("status = 'active'"),
        ),
        sa.Index(
            "uq_index_runs_one_previous_per_source",
            "source_profile_id",
            unique=True,
            postgresql_where=sa.text("status = 'previous'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(postgresql.UUID(), default=uuid.uuid4)
    source_profile_id: Mapped[UUID] = mapped_column(postgresql.UUID())
    index_config_id: Mapped[UUID] = mapped_column(postgresql.UUID())
    commit_sha: Mapped[str] = mapped_column(sa.String(40))
    status: Mapped[str] = mapped_column(sa.Text(), server_default=sa.text("'building'"))
    started_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.text("now()")
    )
    indexed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    activated_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    stats: Mapped[dict[str, Any]] = mapped_column(
        postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")
    )
    failure_code: Mapped[str | None] = mapped_column(sa.Text())
    failure_detail: Mapped[str | None] = mapped_column(sa.Text())


class DocumentContent(Base):
    """Canonical content bytes shared by document occurrences."""

    __tablename__ = "document_contents"
    __table_args__ = (
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_document_contents_content_hash_sha256",
        ),
        sa.CheckConstraint(
            "byte_size >= 0",
            name="ck_document_contents_byte_size_nonnegative",
        ),
        sa.CheckConstraint(
            "byte_size = octet_length(content)",
            name="ck_document_contents_byte_size_matches_content",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_document_contents"),
        sa.UniqueConstraint(
            "content_hash",
            name="uq_document_contents_content_hash",
        ),
    )

    id: Mapped[UUID] = mapped_column(postgresql.UUID(), default=uuid.uuid4)
    content_hash: Mapped[str] = mapped_column(sa.String(64))
    content: Mapped[str] = mapped_column(sa.Text())
    byte_size: Mapped[int] = mapped_column(sa.BigInteger())


class DocumentOccurrence(Base):
    """A source path observed in a specific index snapshot."""

    __tablename__ = "document_occurrences"
    __table_args__ = (
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
            "run_id",
            "source_path",
            name="uq_document_occurrences_run_id_source_path",
        ),
        sa.UniqueConstraint(
            "run_id",
            "id",
            name="uq_document_occurrences_run_id_id",
        ),
        sa.Index("ix_document_occurrences_content_id", "content_id"),
    )

    id: Mapped[UUID] = mapped_column(postgresql.UUID(), default=uuid.uuid4)
    run_id: Mapped[UUID] = mapped_column(postgresql.UUID())
    content_id: Mapped[UUID] = mapped_column(postgresql.UUID())
    source_path: Mapped[str] = mapped_column(sa.Text())
    version_scope: Mapped[str] = mapped_column(sa.Text())
    document_date: Mapped[date | None] = mapped_column(sa.Date())
    document_version: Mapped[str | None] = mapped_column(sa.Text())
    decision_state: Mapped[str] = mapped_column(sa.Text())
    owner_domain: Mapped[str] = mapped_column(sa.Text())


class DocumentParse(Base):
    """A reusable parse of canonical document content."""

    __tablename__ = "document_parses"
    __table_args__ = (
        sa.CheckConstraint(
            "parser_version ~ '[^[:space:]]'",
            name="ck_document_parses_parser_version_non_blank",
        ),
        sa.CheckConstraint(
            "chunk_config_hash ~ '^[0-9a-f]{64}$'",
            name="ck_document_parses_chunk_config_hash_sha256",
        ),
        sa.CheckConstraint(
            "section_count > 0",
            name="ck_document_parses_section_count_positive",
        ),
        sa.CheckConstraint(
            "chunk_count >= 0",
            name="ck_document_parses_chunk_count_nonnegative",
        ),
        sa.CheckConstraint(
            "artifact_hash ~ '^[0-9a-f]{64}$'",
            name="ck_document_parses_artifact_hash_sha256",
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

    id: Mapped[UUID] = mapped_column(postgresql.UUID(), default=uuid.uuid4)
    content_id: Mapped[UUID] = mapped_column(postgresql.UUID())
    parser_version: Mapped[str] = mapped_column(sa.Text())
    chunk_config_hash: Mapped[str] = mapped_column(sa.String(64))
    section_count: Mapped[int] = mapped_column(sa.Integer())
    chunk_count: Mapped[int] = mapped_column(sa.Integer())
    artifact_hash: Mapped[str] = mapped_column(sa.String(64))


class Section(Base):
    """A Markdown section in a parsed document."""

    __tablename__ = "sections"
    __table_args__ = (
        sa.CheckConstraint("ordinal >= 0", name="ck_sections_ordinal_nonnegative"),
        sa.CheckConstraint(
            "level >= 0 AND level <= 6",
            name="ck_sections_level_range",
        ),
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
        sa.UniqueConstraint(
            "parse_id",
            "ordinal",
            name="uq_sections_parse_id_ordinal",
        ),
        sa.UniqueConstraint("parse_id", "id", name="uq_sections_parse_id_id"),
        sa.Index("ix_sections_parent_section_id", "parent_section_id"),
    )

    id: Mapped[UUID] = mapped_column(postgresql.UUID(), default=uuid.uuid4)
    parse_id: Mapped[UUID] = mapped_column(postgresql.UUID())
    parent_section_id: Mapped[UUID | None] = mapped_column(postgresql.UUID())
    ordinal: Mapped[int] = mapped_column(sa.Integer())
    level: Mapped[int] = mapped_column(sa.SmallInteger())
    heading: Mapped[str | None] = mapped_column(sa.Text())
    heading_path: Mapped[list[str]] = mapped_column(postgresql.ARRAY(sa.Text()))
    body: Mapped[str] = mapped_column(sa.Text())
    line_start: Mapped[int] = mapped_column(sa.Integer())
    line_end: Mapped[int] = mapped_column(sa.Integer())


class Chunk(Base):
    """A searchable child fragment of a document section."""

    __tablename__ = "chunks"
    __table_args__ = (
        sa.CheckConstraint("ordinal >= 0", name="ck_chunks_ordinal_nonnegative"),
        sa.CheckConstraint(
            "token_count >= 0",
            name="ck_chunks_token_count_nonnegative",
        ),
        sa.CheckConstraint(
            "line_start >= 1 AND line_end >= line_start",
            name="ck_chunks_line_range",
        ),
        sa.CheckConstraint(
            "chunk_hash ~ '^[0-9a-f]{64}$'",
            name="ck_chunks_chunk_hash_sha256",
        ),
        sa.ForeignKeyConstraint(
            ["section_id"],
            ["sections.id"],
            name="fk_chunks_section_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_chunks"),
        sa.UniqueConstraint(
            "section_id",
            "ordinal",
            name="uq_chunks_section_id_ordinal",
        ),
        sa.Index(
            "ix_chunks_search_text_trgm",
            "search_text",
            postgresql_using="gin",
            postgresql_ops={"search_text": "gin_trgm_ops"},
        ),
    )

    id: Mapped[UUID] = mapped_column(postgresql.UUID(), default=uuid.uuid4)
    section_id: Mapped[UUID] = mapped_column(postgresql.UUID())
    ordinal: Mapped[int] = mapped_column(sa.Integer())
    raw_text: Mapped[str] = mapped_column(sa.Text())
    search_text: Mapped[str] = mapped_column(sa.Text())
    token_count: Mapped[int] = mapped_column(sa.Integer())
    line_start: Mapped[int] = mapped_column(sa.Integer())
    line_end: Mapped[int] = mapped_column(sa.Integer())
    chunk_hash: Mapped[str] = mapped_column(sa.String(64))


class ChunkEmbedding(Base):
    """A vector representation of one searchable chunk."""

    __tablename__ = "chunk_embeddings"
    __table_args__ = (
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
            "dimension > 0",
            name="ck_chunk_embeddings_dimension_positive",
        ),
        sa.CheckConstraint(
            "status = 'ready'",
            name="ck_chunk_embeddings_status_ready",
        ),
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

    id: Mapped[UUID] = mapped_column(postgresql.UUID(), default=uuid.uuid4)
    chunk_id: Mapped[UUID] = mapped_column(postgresql.UUID())
    embedding_config_hash: Mapped[str] = mapped_column(sa.String(64))
    model_name: Mapped[str] = mapped_column(sa.Text())
    model_revision: Mapped[str] = mapped_column(sa.Text())
    dimension: Mapped[int] = mapped_column(sa.Integer())
    embedding: Mapped[list[float]] = mapped_column(VECTOR())
    status: Mapped[str] = mapped_column(sa.Text(), server_default=sa.text("'ready'"))


class DocumentRelation(Base):
    """A supersession or potential-conflict edge within one snapshot."""

    __tablename__ = "document_relations"
    __table_args__ = (
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
        sa.Index(
            "ix_document_relations_from",
            "run_id",
            "from_occurrence_id",
        ),
        sa.Index(
            "ix_document_relations_to",
            "run_id",
            "to_occurrence_id",
        ),
        sa.Index(
            "ix_document_relations_evidence",
            "run_id",
            "evidence_source_path",
        ),
    )

    id: Mapped[UUID] = mapped_column(postgresql.UUID(), default=uuid.uuid4)
    run_id: Mapped[UUID] = mapped_column(postgresql.UUID())
    from_occurrence_id: Mapped[UUID] = mapped_column(postgresql.UUID())
    to_occurrence_id: Mapped[UUID] = mapped_column(postgresql.UUID())
    relation_type: Mapped[str] = mapped_column(sa.Text())
    evidence_source_path: Mapped[str] = mapped_column(sa.Text())
    evidence_line_start: Mapped[int] = mapped_column(sa.Integer())
    evidence_line_end: Mapped[int] = mapped_column(sa.Integer())


class ApiClient(Base):
    """A caller identity represented only by a token hash."""

    __tablename__ = "api_clients"
    __table_args__ = (
        sa.CheckConstraint(
            "name ~ '[^[:space:]]'",
            name="ck_api_clients_name_non_blank",
        ),
        sa.CheckConstraint(
            "char_length(key_id) = 16",
            name="ck_api_clients_key_id_length",
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

    id: Mapped[UUID] = mapped_column(postgresql.UUID(), default=uuid.uuid4)
    name: Mapped[str] = mapped_column(sa.Text())
    key_id: Mapped[str] = mapped_column(sa.String(16))
    token_hash: Mapped[bytes] = mapped_column(postgresql.BYTEA())
    status: Mapped[str] = mapped_column(sa.Text(), server_default=sa.text("'active'"))
    expires_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.text("now()")
    )


class ClientSourceGrant(Base):
    """An API client's authorization to search one source profile."""

    __tablename__ = "client_source_grants"
    __table_args__ = (
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
            "client_id",
            "source_profile_id",
            name="pk_client_source_grants",
        ),
        sa.Index(
            "ix_client_source_grants_source_profile_id",
            "source_profile_id",
        ),
    )

    client_id: Mapped[UUID] = mapped_column(postgresql.UUID())
    source_profile_id: Mapped[UUID] = mapped_column(postgresql.UUID())


class SearchAuditEvent(Base):
    """A minimal, privacy-preserving search request audit record."""

    __tablename__ = "search_audit_events"
    __table_args__ = (
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
            "rrf_ms >= 0",
            name="ck_search_audit_events_rrf_ms_nonnegative",
        ),
        sa.CheckConstraint(
            "total_ms >= 0",
            name="ck_search_audit_events_total_ms_nonnegative",
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
        sa.Index("ix_search_audit_events_client_id", "client_id"),
        sa.Index(
            "ix_search_audit_events_source_profile_id",
            "source_profile_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(postgresql.UUID(), default=uuid.uuid4)
    occurred_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.text("now()")
    )
    request_id: Mapped[str] = mapped_column(sa.Text())
    client_id: Mapped[UUID] = mapped_column(postgresql.UUID())
    source_profile_id: Mapped[UUID] = mapped_column(postgresql.UUID())
    query_hmac: Mapped[bytes] = mapped_column(postgresql.BYTEA())
    filters: Mapped[dict[str, Any]] = mapped_column(
        postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")
    )
    returned_chunk_ids: Mapped[list[UUID]] = mapped_column(
        postgresql.ARRAY(postgresql.UUID()),
        server_default=sa.text("'{}'::uuid[]"),
    )
    commit_sha: Mapped[str] = mapped_column(sa.String(40))
    status: Mapped[str] = mapped_column(sa.Text())
    result_count: Mapped[int] = mapped_column(sa.Integer())
    embedding_ms: Mapped[int] = mapped_column(sa.Integer())
    keyword_ms: Mapped[int] = mapped_column(sa.Integer())
    vector_ms: Mapped[int] = mapped_column(sa.Integer())
    rrf_ms: Mapped[int] = mapped_column(sa.Integer())
    total_ms: Mapped[int] = mapped_column(sa.Integer())


__all__ = [
    "ApiClient",
    "Chunk",
    "ChunkEmbedding",
    "ClientSourceGrant",
    "DocumentContent",
    "DocumentOccurrence",
    "DocumentParse",
    "DocumentRelation",
    "IndexConfig",
    "IndexRun",
    "SearchAuditEvent",
    "SearchPolicyManifest",
    "Section",
    "SourceProfile",
]
