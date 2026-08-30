"""Add immutable search policy manifests and backfill legacy policy identity.

Revision ID: 0003_search_policy_manifest
Revises: 0002_index_run_lifecycle
"""

import hashlib
import json
from collections.abc import Sequence
from math import isfinite
from uuid import UUID, uuid5

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_search_policy_manifest"
down_revision: str | Sequence[str] | None = "0002_index_run_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_POLICY_NAMESPACE = UUID("fe37daba-35bd-4cf3-b85f-a610bd93e09f")
_DOCUMENT_KEYS = {
    "provider",
    "model_name",
    "revision",
    "dimension",
    "normalize_embeddings",
    "library_name",
    "library_version",
}
_EMBEDDING_KEYS = {"document", "query"}
_QUERY_KEYS = {"instruction"}
_RETRIEVAL_KEYS = {
    "k",
    "keyword_weight",
    "vector_weight",
    "keyword_similarity_floor",
    "vector_similarity_floor",
    "evidence_floor_status",
}
_CALIBRATION_STATES = {"calibration_pending", "calibrated"}


def _invalid() -> None:
    raise ValueError("Legacy search policy is invalid")


def _frozen_legacy_policy_snapshot(
    embedding_config: object,
    retrieval_config: object,
) -> tuple[dict[str, object], str]:
    """Project v2.0 combined snapshots without mutable application imports."""
    if (
        type(embedding_config) is not dict
        or set(embedding_config) != _EMBEDDING_KEYS
        or type(retrieval_config) is not dict
        or set(retrieval_config) != _RETRIEVAL_KEYS
    ):
        _invalid()
    document = embedding_config["document"]
    query = embedding_config["query"]
    if (
        type(document) is not dict
        or set(document) != _DOCUMENT_KEYS
        or type(query) is not dict
        or set(query) != _QUERY_KEYS
    ):
        _invalid()
    for key in ("model_name", "revision"):
        if type(document[key]) is not str or not document[key].strip():
            _invalid()
    if (
        type(document["dimension"]) is not int
        or document["dimension"] <= 0
        or type(document["normalize_embeddings"]) is not bool
        or type(query["instruction"]) is not str
        or not query["instruction"].strip()
        or type(retrieval_config["k"]) is not int
        or retrieval_config["k"] <= 0
    ):
        _invalid()
    for key in ("keyword_weight", "vector_weight"):
        value = retrieval_config[key]
        if type(value) is not float or not isfinite(value) or value <= 0.0:
            _invalid()
    for key in ("keyword_similarity_floor", "vector_similarity_floor"):
        value = retrieval_config[key]
        if type(value) is not float or not isfinite(value) or not 0.0 <= value <= 1.0:
            _invalid()
    status = retrieval_config["evidence_floor_status"]
    if type(status) is not str or status not in _CALIBRATION_STATES:
        _invalid()
    snapshot: dict[str, object] = {
        "query_embedding_model_name": document["model_name"],
        "query_embedding_revision": document["revision"],
        "query_embedding_dimension": document["dimension"],
        "query_embedding_normalize_embeddings": document["normalize_embeddings"],
        "query_instruction": query["instruction"],
        "keyword_candidate_limit": 50,
        "vector_candidate_limit": 50,
        "rrf_k": retrieval_config["k"],
        "keyword_weight": retrieval_config["keyword_weight"],
        "vector_weight": retrieval_config["vector_weight"],
        "keyword_similarity_floor": (
            0.0
            if retrieval_config["keyword_similarity_floor"] == 0.0
            else retrieval_config["keyword_similarity_floor"]
        ),
        "vector_similarity_floor": (
            0.0
            if retrieval_config["vector_similarity_floor"] == 0.0
            else retrieval_config["vector_similarity_floor"]
        ),
        "calibration_status": status,
    }
    canonical = json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return snapshot, hashlib.sha256(canonical).hexdigest()


def _backfill_legacy_policies(connection: sa.Connection) -> None:
    rows = connection.execute(
        sa.text(
            "SELECT DISTINCT config.id, config.embedding_config, config.rrf_config "
            "FROM index_configs AS config "
            "JOIN index_runs AS run ON run.index_config_id = config.id "
            "ORDER BY config.id"
        )
    ).mappings()
    table = sa.table(
        "search_policy_manifests",
        sa.column("id", postgresql.UUID()),
        sa.column("config_hash", sa.String(64)),
        sa.column("snapshot", postgresql.JSONB()),
    )
    for row in rows:
        snapshot, digest = _frozen_legacy_policy_snapshot(
            row["embedding_config"], row["rrf_config"]
        )
        statement = (
            postgresql.insert(table)
            .values(
                id=uuid5(_POLICY_NAMESPACE, digest),
                config_hash=digest,
                snapshot=snapshot,
            )
            .on_conflict_do_nothing(index_elements=[table.c.config_hash])
        )
        connection.execute(statement)
        stored = connection.execute(
            sa.text(
                "SELECT snapshot FROM search_policy_manifests "
                "WHERE config_hash = :config_hash"
            ),
            {"config_hash": digest},
        ).scalar_one()
        if stored != snapshot:
            raise ValueError("Legacy search policy is invalid")


def upgrade() -> None:
    """Create append-only policy storage and deduplicate legacy snapshots."""
    op.create_table(
        "search_policy_manifests",
        sa.Column("id", postgresql.UUID(), nullable=False),
        sa.Column("config_hash", sa.String(length=64), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
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
    _backfill_legacy_policies(op.get_bind())
    op.execute(
        """
CREATE FUNCTION reject_search_policy_manifest_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION USING
        ERRCODE = '23514',
        MESSAGE = 'search policy manifests are append-only';
END;
$$
"""
    )
    op.execute(
        """
CREATE TRIGGER trg_search_policy_manifests_append_only
BEFORE UPDATE OR DELETE ON search_policy_manifests
FOR EACH ROW EXECUTE FUNCTION reject_search_policy_manifest_mutation()
"""
    )


def downgrade() -> None:
    """Remove only v2.1 policy manifest assets; preserve every legacy row."""
    op.execute(
        "DROP TRIGGER trg_search_policy_manifests_append_only "
        "ON search_policy_manifests"
    )
    op.execute("DROP FUNCTION reject_search_policy_manifest_mutation()")
    op.drop_table("search_policy_manifests")
