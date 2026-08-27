"""Add explicit two-generation activation lifecycle state.

Revision ID: 0002_index_run_lifecycle
Revises: 0001_initial_schema
"""

import hashlib
import json
from collections import defaultdict
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0002_index_run_lifecycle"
down_revision: str | Sequence[str] | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PARSE_ARTIFACT_MANIFEST_VERSION = "document-parse-artifacts-v1"
_PARSE_BACKFILL_BATCH_SIZE = 500
_SECTION_KEYS = {
    "ordinal",
    "parent_ordinal",
    "level",
    "heading",
    "heading_path",
    "body",
    "line_start",
    "line_end",
}
_CHUNK_KEYS = {
    "section_ordinal",
    "ordinal",
    "raw_text",
    "search_text",
    "token_count",
    "line_start",
    "line_end",
    "chunk_hash",
}


def _frozen_artifact_manifest(
    sections: tuple[dict[str, Any], ...],
    chunks: tuple[dict[str, Any], ...],
) -> tuple[int, int, str]:
    """Frozen v1 backfill algorithm; never import mutable application code."""
    if type(sections) is not tuple or not sections:
        raise ValueError("a persisted parse requires at least one section")
    if type(chunks) is not tuple:
        raise ValueError("artifact chunks must be an exact tuple")
    for expected, section in enumerate(sections):
        if type(section) is not dict or set(section) != _SECTION_KEYS:
            raise ValueError("artifact section fields are incomplete")
        if type(section["ordinal"]) is not int or section["ordinal"] != expected:
            raise ValueError("artifact section order must be exact and sequential")
        parent = section["parent_ordinal"]
        if parent is not None and (
            type(parent) is not int or parent < 0 or parent >= expected
        ):
            raise ValueError("artifact parent hierarchy is invalid")
        if (
            type(section["level"]) is not int
            or not 0 <= section["level"] <= 6
            or (section["heading"] is not None and type(section["heading"]) is not str)
            or type(section["heading_path"]) is not list
            or not all(type(part) is str for part in section["heading_path"])
            or type(section["body"]) is not str
            or type(section["line_start"]) is not int
            or type(section["line_end"]) is not int
            or section["line_start"] < 1
            or section["line_end"] < section["line_start"]
        ):
            raise ValueError("artifact section fields are invalid")
        if section["level"] == 0:
            if section["heading"] is not None or section["heading_path"]:
                raise ValueError("synthetic root section heading is invalid")
        elif (
            type(section["heading"]) is not str
            or not section["heading_path"]
            or section["heading_path"][-1] != section["heading"]
        ):
            raise ValueError("heading section path is invalid")

    chunk_counts = [0] * len(sections)
    previous_owner = -1
    expected_chunk_ordinal = 0
    for chunk in chunks:
        if type(chunk) is not dict or set(chunk) != _CHUNK_KEYS:
            raise ValueError("artifact chunk fields are incomplete")
        owner = chunk["section_ordinal"]
        if (
            type(owner) is not int
            or owner < 0
            or owner >= len(sections)
            or owner < previous_owner
        ):
            raise ValueError("artifact chunk order must follow section order")
        if owner != previous_owner:
            expected_chunk_ordinal = 0
        if (
            type(chunk["ordinal"]) is not int
            or chunk["ordinal"] != expected_chunk_ordinal
        ):
            raise ValueError("artifact chunk order must be exact and sequential")
        if (
            type(chunk["raw_text"]) is not str
            or not chunk["raw_text"]
            or type(chunk["search_text"]) is not str
            or not chunk["search_text"]
            or type(chunk["token_count"]) is not int
            or chunk["token_count"] <= 0
            or type(chunk["line_start"]) is not int
            or type(chunk["line_end"]) is not int
            or chunk["line_start"] < 1
            or chunk["line_end"] < chunk["line_start"]
            or type(chunk["chunk_hash"]) is not str
            or len(chunk["chunk_hash"]) != 64
            or any(
                character not in "0123456789abcdef" for character in chunk["chunk_hash"]
            )
        ):
            raise ValueError("artifact chunk fields are invalid")
        chunk_counts[owner] += 1
        previous_owner = owner
        expected_chunk_ordinal += 1
    for section, chunk_count in zip(sections, chunk_counts, strict=True):
        if section["body"].strip():
            if chunk_count == 0:
                raise ValueError("searchable section requires at least one chunk")
        elif chunk_count != 0:
            raise ValueError("blank section must not persist chunks")

    payload = {
        "version": _PARSE_ARTIFACT_MANIFEST_VERSION,
        "sections": list(sections),
        "chunks": list(chunks),
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return len(sections), len(chunks), hashlib.sha256(canonical).hexdigest()


def _backfill_parse_artifact_manifests(connection: Any) -> None:
    """Backfill in bounded keyset batches with one executemany update per batch.

    Memory and roundtrips scale with the fixed parse batch rather than total rows.
    One unusually large parse can still exceed the nominal memory bound because a
    single document's canonical projection must be hashed as one exact artifact.
    """
    after_id: object | None = None
    while True:
        if after_id is None:
            id_statement = sa.text(
                "SELECT id FROM document_parses ORDER BY id LIMIT :batch_size"
            )
            id_parameters = {"batch_size": _PARSE_BACKFILL_BATCH_SIZE}
        else:
            id_statement = sa.text(
                "SELECT id FROM document_parses "
                "WHERE id > CAST(:after_id AS uuid) "
                "ORDER BY id LIMIT :batch_size"
            )
            id_parameters = {
                "after_id": after_id,
                "batch_size": _PARSE_BACKFILL_BATCH_SIZE,
            }
        parse_ids = tuple(connection.execute(id_statement, id_parameters).scalars())
        if not parse_ids:
            return

        section_rows: dict[object, list[dict[str, Any]]] = defaultdict(list)
        for row in connection.execute(
            sa.text(
                "SELECT section.parse_id, section.ordinal, "
                "parent.ordinal AS parent_ordinal, section.level, section.heading, "
                "section.heading_path, section.body, section.line_start, "
                "section.line_end FROM sections AS section "
                "LEFT JOIN sections AS parent "
                "ON parent.id = section.parent_section_id "
                "WHERE section.parse_id = ANY(CAST(:parse_ids AS uuid[])) "
                "ORDER BY section.parse_id, section.ordinal"
            ),
            {"parse_ids": list(parse_ids)},
        ).mappings():
            section_rows[row["parse_id"]].append(
                {key: row[key] for key in _SECTION_KEYS}
            )
        chunk_rows: dict[object, list[dict[str, Any]]] = defaultdict(list)
        for row in connection.execute(
            sa.text(
                "SELECT section.parse_id, section.ordinal AS section_ordinal, "
                "chunk.ordinal, chunk.raw_text, chunk.search_text, "
                "chunk.token_count, chunk.line_start, chunk.line_end, "
                "chunk.chunk_hash FROM chunks AS chunk "
                "JOIN sections AS section ON section.id = chunk.section_id "
                "WHERE section.parse_id = ANY(CAST(:parse_ids AS uuid[])) "
                "ORDER BY section.parse_id, section.ordinal, chunk.ordinal"
            ),
            {"parse_ids": list(parse_ids)},
        ).mappings():
            chunk_rows[row["parse_id"]].append({key: row[key] for key in _CHUNK_KEYS})

        updates: list[dict[str, object]] = []
        for parse_id in parse_ids:
            try:
                section_count, chunk_count, artifact_hash = _frozen_artifact_manifest(
                    tuple(section_rows[parse_id]),
                    tuple(chunk_rows[parse_id]),
                )
            except ValueError as error:
                raise RuntimeError(
                    "invalid legacy document parse artifacts; "
                    "migration cannot infer truth"
                ) from error
            updates.append(
                {
                    "parse_id": parse_id,
                    "section_count": section_count,
                    "chunk_count": chunk_count,
                    "artifact_hash": artifact_hash,
                }
            )
        connection.execute(
            sa.text(
                "UPDATE document_parses SET section_count = :section_count, "
                "chunk_count = :chunk_count, artifact_hash = :artifact_hash "
                "WHERE id = :parse_id"
            ),
            updates,
        )
        after_id = parse_ids[-1]


def upgrade() -> None:
    """Backfill known lifecycle time and enforce one active/previous slot."""
    op.execute(
        sa.text(
            """
DO $$
DECLARE
    violation text;
BEGIN
    WITH lifecycle AS (
        SELECT
            source.id AS source_id,
            source.active_index_run_id,
            count(run.id) FILTER (WHERE run.status = 'active') AS active_count,
            count(run.id) FILTER (WHERE run.status = 'previous') AS previous_count,
            (array_agg(run.id) FILTER (WHERE run.status = 'active'))[1]
                AS active_run_id,
            pointed.status AS pointed_status,
            pointed.source_profile_id AS pointed_source_id
        FROM source_profiles AS source
        LEFT JOIN index_runs AS run ON run.source_profile_id = source.id
        LEFT JOIN index_runs AS pointed ON pointed.id = source.active_index_run_id
        GROUP BY source.id, source.active_index_run_id,
                 pointed.status, pointed.source_profile_id
    )
    SELECT CASE
        WHEN active_count > 1 THEN 'multiple ACTIVE rows'
        WHEN previous_count > 1 THEN 'multiple PREVIOUS rows'
        WHEN active_index_run_id IS NULL AND active_count > 0
            THEN 'ACTIVE row without pointer'
        WHEN active_index_run_id IS NOT NULL AND pointed_status <> 'active'
            THEN 'pointer target is not ACTIVE'
        WHEN active_index_run_id IS NOT NULL AND pointed_status IS NULL
            THEN 'pointer target is missing'
        WHEN active_index_run_id IS NULL AND previous_count > 0
            THEN 'PREVIOUS row without ACTIVE pointer'
        WHEN active_count = 1 AND active_index_run_id <> active_run_id
            THEN 'pointer and ACTIVE row disagree'
        WHEN active_index_run_id IS NOT NULL AND active_count <> 1
            THEN 'pointer does not resolve to one ACTIVE row'
        WHEN pointed_source_id IS NOT NULL AND pointed_source_id <> source_id
            THEN 'pointer target belongs to another source'
    END
    INTO violation
    FROM lifecycle
    WHERE active_count > 1
       OR previous_count > 1
       OR (active_index_run_id IS NULL AND active_count > 0)
       OR (active_index_run_id IS NOT NULL AND pointed_status <> 'active')
       OR (active_index_run_id IS NOT NULL AND pointed_status IS NULL)
       OR (active_index_run_id IS NULL AND previous_count > 0)
       OR (active_count = 1 AND active_index_run_id <> active_run_id)
       OR (active_index_run_id IS NOT NULL AND active_count <> 1)
       OR (pointed_source_id IS NOT NULL AND pointed_source_id <> source_id)
    LIMIT 1;

    IF violation IS NOT NULL THEN
        RAISE EXCEPTION 'invalid legacy index lifecycle: %', violation;
    END IF;
END $$
"""
        )
    )
    op.execute(
        sa.text(
            """
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM document_parses AS parse
        WHERE NOT EXISTS (
            SELECT 1 FROM sections AS section WHERE section.parse_id = parse.id
        )
    ) THEN
        RAISE EXCEPTION
            'invalid legacy document parse artifacts: parse without sections';
    END IF;
END $$
"""
        )
    )
    op.add_column(
        "index_runs",
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        "UPDATE index_runs "
        "SET activated_at = COALESCE(indexed_at, started_at) "
        "WHERE status IN ('active', 'previous')"
    )
    op.drop_constraint("ck_index_runs_status", "index_runs", type_="check")
    op.create_check_constraint(
        "ck_index_runs_status",
        "index_runs",
        "status IN ('building', 'ready', 'active', 'previous', 'archived', 'failed')",
    )
    op.create_check_constraint(
        "ck_index_runs_lifecycle_activated_at",
        "index_runs",
        "status NOT IN ('active', 'previous', 'archived') OR activated_at IS NOT NULL",
    )
    # Deliberately allow CREATE UNIQUE INDEX to fail on ambiguous existing data.
    # Migration must never choose or delete a lifecycle row on the user's behalf.
    op.create_index(
        "uq_index_runs_one_active_per_source",
        "index_runs",
        ["source_profile_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "uq_index_runs_one_previous_per_source",
        "index_runs",
        ["source_profile_id"],
        unique=True,
        postgresql_where=sa.text("status = 'previous'"),
    )
    op.add_column(
        "document_parses",
        sa.Column("section_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "document_parses",
        sa.Column("chunk_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "document_parses",
        sa.Column("artifact_hash", sa.String(length=64), nullable=True),
    )
    _backfill_parse_artifact_manifests(op.get_bind())
    op.alter_column("document_parses", "section_count", nullable=False)
    op.alter_column("document_parses", "chunk_count", nullable=False)
    op.alter_column("document_parses", "artifact_hash", nullable=False)
    op.create_check_constraint(
        "ck_document_parses_section_count_positive",
        "document_parses",
        "section_count > 0",
    )
    op.create_check_constraint(
        "ck_document_parses_chunk_count_nonnegative",
        "document_parses",
        "chunk_count >= 0",
    )
    op.create_check_constraint(
        "ck_document_parses_artifact_hash_sha256",
        "document_parses",
        "artifact_hash ~ '^[0-9a-f]{64}$'",
    )


def downgrade() -> None:
    """Remove lifecycle metadata only when no archived history would be lost."""
    op.execute(
        sa.text(
            "DO $$ BEGIN "
            "IF EXISTS (SELECT 1 FROM index_runs WHERE status = 'archived') THEN "
            "RAISE EXCEPTION 'cannot downgrade with archived index runs'; "
            "END IF; END $$"
        )
    )
    op.drop_constraint(
        "ck_document_parses_artifact_hash_sha256",
        "document_parses",
        type_="check",
    )
    op.drop_constraint(
        "ck_document_parses_chunk_count_nonnegative",
        "document_parses",
        type_="check",
    )
    op.drop_constraint(
        "ck_document_parses_section_count_positive",
        "document_parses",
        type_="check",
    )
    op.drop_column("document_parses", "artifact_hash")
    op.drop_column("document_parses", "chunk_count")
    op.drop_column("document_parses", "section_count")
    op.drop_index("uq_index_runs_one_previous_per_source", table_name="index_runs")
    op.drop_index("uq_index_runs_one_active_per_source", table_name="index_runs")
    op.drop_constraint(
        "ck_index_runs_lifecycle_activated_at",
        "index_runs",
        type_="check",
    )
    op.drop_constraint("ck_index_runs_status", "index_runs", type_="check")
    op.create_check_constraint(
        "ck_index_runs_status",
        "index_runs",
        "status IN ('building', 'ready', 'active', 'previous', 'failed')",
    )
    op.drop_column("index_runs", "activated_at")
