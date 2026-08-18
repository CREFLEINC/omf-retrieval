"""Integration tests for database-enforced retrieval invariants."""

from uuid import uuid4

import pytest
from sqlalchemy import Connection, text
from sqlalchemy.exc import IntegrityError

SHA256 = "a" * 64
OTHER_SHA256 = "b" * 64
COMMIT_SHA = "c" * 40


def _seed_valid_graph(connection: Connection) -> dict[str, object]:
    identifiers = {
        name: uuid4()
        for name in (
            "source_id",
            "config_id",
            "run_id",
            "content_id",
            "occurrence_id",
            "other_occurrence_id",
            "parse_id",
            "section_id",
            "chunk_id",
            "embedding_id",
            "relation_id",
            "client_id",
            "audit_id",
        )
    }
    connection.execute(
        text("INSERT INTO source_profiles (id, source_key) VALUES (:source_id, 'omf')"),
        identifiers,
    )
    connection.execute(
        text(
            "INSERT INTO index_configs "
            "(id, config_hash, parser_config, chunk_config, tokenizer_config, "
            "embedding_config, rrf_config) VALUES "
            "(:config_id, :config_hash, '{}', '{}', '{}', '{}', '{}')"
        ),
        {**identifiers, "config_hash": SHA256},
    )
    connection.execute(
        text(
            "INSERT INTO index_runs "
            "(id, source_profile_id, index_config_id, commit_sha) VALUES "
            "(:run_id, :source_id, :config_id, :commit_sha)"
        ),
        {**identifiers, "commit_sha": COMMIT_SHA},
    )
    connection.execute(
        text(
            "INSERT INTO document_contents "
            "(id, content_hash, content, byte_size) VALUES "
            "(:content_id, :content_hash, 'body', 4)"
        ),
        {**identifiers, "content_hash": SHA256},
    )
    connection.execute(
        text(
            "INSERT INTO document_occurrences "
            "(id, run_id, content_id, source_path, version_scope, decision_state, "
            "owner_domain) VALUES "
            "(:occurrence_id, :run_id, :content_id, 'docs/a.md', 'current', "
            "'confirmed', 'docs'), "
            "(:other_occurrence_id, :run_id, :content_id, 'docs/b.md', 'historical', "
            "'draft', 'docs')"
        ),
        identifiers,
    )
    connection.execute(
        text(
            "INSERT INTO document_parses "
            "(id, content_id, parser_version, chunk_config_hash) VALUES "
            "(:parse_id, :content_id, 'markdown-it-1', :chunk_config_hash)"
        ),
        {**identifiers, "chunk_config_hash": SHA256},
    )
    connection.execute(
        text(
            "INSERT INTO sections "
            "(id, parse_id, ordinal, level, heading, heading_path, body, "
            "line_start, line_end) VALUES "
            "(:section_id, :parse_id, 0, 0, NULL, ARRAY[]::text[], 'body', 1, 1)"
        ),
        identifiers,
    )
    connection.execute(
        text(
            "INSERT INTO chunks "
            "(id, section_id, ordinal, raw_text, search_text, token_count, "
            "line_start, line_end, chunk_hash) VALUES "
            "(:chunk_id, :section_id, 0, 'body', 'body', 1, 1, 1, :chunk_hash)"
        ),
        {**identifiers, "chunk_hash": SHA256},
    )
    connection.execute(
        text(
            "INSERT INTO chunk_embeddings "
            "(id, chunk_id, embedding_config_hash, model_name, model_revision, "
            "dimension, embedding) VALUES "
            "(:embedding_id, :chunk_id, :embedding_config_hash, 'model', 'revision', "
            "3, '[0.1,0.2,0.3]')"
        ),
        {**identifiers, "embedding_config_hash": SHA256},
    )
    connection.execute(
        text(
            "INSERT INTO document_relations "
            "(id, run_id, from_occurrence_id, to_occurrence_id, relation_type, "
            "evidence_source_path, evidence_line_start, evidence_line_end) VALUES "
            "(:relation_id, :run_id, :occurrence_id, :other_occurrence_id, "
            "'supersedes', 'docs/a.md', 1, 1)"
        ),
        identifiers,
    )
    connection.execute(
        text(
            "INSERT INTO api_clients (id, name, key_id, token_hash) VALUES "
            "(:client_id, 'agent', '1234567890abcdef', :token_hash)"
        ),
        {**identifiers, "token_hash": b"t" * 32},
    )
    connection.execute(
        text(
            "INSERT INTO search_audit_events "
            "(id, request_id, client_id, source_profile_id, query_hmac, commit_sha, "
            "status, result_count, embedding_ms, keyword_ms, vector_ms, rrf_ms, "
            "total_ms) VALUES "
            "(:audit_id, 'request-1', :client_id, :source_id, :query_hmac, "
            ":commit_sha, 'ok', 1, 1, 1, 1, 1, 5)"
        ),
        {**identifiers, "query_hmac": b"h" * 32, "commit_sha": COMMIT_SHA},
    )
    return identifiers


INVALID_VALUE_UPDATES = (
    pytest.param("source_profiles", "source_key = '   '", "source_id", id="source-key"),
    pytest.param(
        "source_profiles",
        "include_patterns = '{}'::jsonb",
        "source_id",
        id="include-json-type",
    ),
    pytest.param(
        "source_profiles",
        "exclude_patterns = '{}'::jsonb",
        "source_id",
        id="exclude-json-type",
    ),
    pytest.param("index_configs", "config_hash = 'xyz'", "config_id", id="config-hash"),
    *(
        pytest.param(
            "index_configs",
            f"{column} = '[]'::jsonb",
            "config_id",
            id=f"{column}-json-type",
        )
        for column in (
            "parser_config",
            "chunk_config",
            "tokenizer_config",
            "embedding_config",
            "rrf_config",
        )
    ),
    pytest.param("index_runs", "commit_sha = 'xyz'", "run_id", id="run-commit-sha"),
    pytest.param("index_runs", "status = 'done'", "run_id", id="run-status"),
    pytest.param("index_runs", "stats = '[]'::jsonb", "run_id", id="stats-json-type"),
    pytest.param(
        "document_contents", "content_hash = 'xyz'", "content_id", id="content-hash"
    ),
    pytest.param(
        "document_contents", "byte_size = -1", "content_id", id="negative-size"
    ),
    pytest.param("document_contents", "byte_size = 3", "content_id", id="wrong-size"),
    pytest.param(
        "document_occurrences", "source_path = '  '", "occurrence_id", id="source-path"
    ),
    pytest.param(
        "document_occurrences",
        "version_scope = 'all'",
        "occurrence_id",
        id="version-scope",
    ),
    pytest.param(
        "document_occurrences",
        "decision_state = 'final'",
        "occurrence_id",
        id="decision-state",
    ),
    pytest.param(
        "document_occurrences",
        "owner_domain = 'other'",
        "occurrence_id",
        id="owner-domain",
    ),
    pytest.param(
        "document_parses", "parser_version = '  '", "parse_id", id="parser-version"
    ),
    pytest.param(
        "document_parses",
        "chunk_config_hash = 'xyz'",
        "parse_id",
        id="parse-config-hash",
    ),
    pytest.param("sections", "ordinal = -1", "section_id", id="section-ordinal"),
    pytest.param("sections", "level = 7", "section_id", id="section-level"),
    pytest.param("sections", "line_start = 0", "section_id", id="section-zero-line"),
    pytest.param(
        "sections",
        "line_start = 2, line_end = 1",
        "section_id",
        id="section-reversed-line",
    ),
    pytest.param("chunks", "ordinal = -1", "chunk_id", id="chunk-ordinal"),
    pytest.param("chunks", "token_count = -1", "chunk_id", id="token-count"),
    pytest.param("chunks", "line_start = 0", "chunk_id", id="chunk-zero-line"),
    pytest.param(
        "chunks", "line_start = 2, line_end = 1", "chunk_id", id="chunk-reversed-line"
    ),
    pytest.param("chunks", "chunk_hash = 'xyz'", "chunk_id", id="chunk-hash"),
    pytest.param(
        "chunk_embeddings",
        "embedding_config_hash = 'xyz'",
        "embedding_id",
        id="embedding-config-hash",
    ),
    pytest.param(
        "chunk_embeddings", "model_name = '  '", "embedding_id", id="model-name"
    ),
    pytest.param(
        "chunk_embeddings", "model_revision = '  '", "embedding_id", id="model-revision"
    ),
    pytest.param("chunk_embeddings", "dimension = 0", "embedding_id", id="dimension"),
    pytest.param(
        "chunk_embeddings", "status = 'failed'", "embedding_id", id="embedding-status"
    ),
    pytest.param(
        "document_relations",
        "relation_type = 'conflict'",
        "relation_id",
        id="relation-type",
    ),
    pytest.param(
        "document_relations",
        "evidence_source_path = '  '",
        "relation_id",
        id="evidence-path",
    ),
    pytest.param(
        "document_relations",
        "evidence_line_start = 0",
        "relation_id",
        id="evidence-zero-line",
    ),
    pytest.param(
        "document_relations",
        "evidence_line_start = 2, evidence_line_end = 1",
        "relation_id",
        id="evidence-reversed-line",
    ),
    pytest.param("api_clients", "name = '  '", "client_id", id="client-name"),
    pytest.param("api_clients", "key_id = 'short'", "client_id", id="key-id"),
    pytest.param("api_clients", "token_hash = '\\x00'", "client_id", id="token-hash"),
    pytest.param("api_clients", "status = 'inactive'", "client_id", id="client-status"),
    pytest.param(
        "search_audit_events", "request_id = '  '", "audit_id", id="request-id"
    ),
    pytest.param(
        "search_audit_events", "query_hmac = '\\x00'", "audit_id", id="query-hmac"
    ),
    pytest.param(
        "search_audit_events",
        "filters = '[]'::jsonb",
        "audit_id",
        id="filters-json-type",
    ),
    pytest.param(
        "search_audit_events", "commit_sha = 'xyz'", "audit_id", id="audit-commit-sha"
    ),
    pytest.param(
        "search_audit_events", "status = 'failed'", "audit_id", id="audit-status"
    ),
    pytest.param(
        "search_audit_events", "result_count = -1", "audit_id", id="result-count"
    ),
    *(
        pytest.param(
            "search_audit_events",
            f"{column} = -1",
            "audit_id",
            id=column,
        )
        for column in ("embedding_ms", "keyword_ms", "vector_ms", "rrf_ms", "total_ms")
    ),
)

NON_BLANK_COLUMNS = (
    pytest.param(
        "source_profiles",
        "source_key",
        "source_id",
        "ck_source_profiles_source_key_non_blank",
        id="source-key",
    ),
    pytest.param(
        "document_occurrences",
        "source_path",
        "occurrence_id",
        "ck_document_occurrences_source_path_non_blank",
        id="source-path",
    ),
    pytest.param(
        "document_parses",
        "parser_version",
        "parse_id",
        "ck_document_parses_parser_version_non_blank",
        id="parser-version",
    ),
    pytest.param(
        "chunk_embeddings",
        "model_name",
        "embedding_id",
        "ck_chunk_embeddings_model_name_non_blank",
        id="model-name",
    ),
    pytest.param(
        "chunk_embeddings",
        "model_revision",
        "embedding_id",
        "ck_chunk_embeddings_model_revision_non_blank",
        id="model-revision",
    ),
    pytest.param(
        "document_relations",
        "evidence_source_path",
        "relation_id",
        "ck_document_relations_evidence_source_path_non_blank",
        id="evidence-source-path",
    ),
    pytest.param(
        "api_clients",
        "name",
        "client_id",
        "ck_api_clients_name_non_blank",
        id="client-name",
    ),
    pytest.param(
        "search_audit_events",
        "request_id",
        "audit_id",
        "ck_search_audit_events_request_id_non_blank",
        id="request-id",
    ),
)


@pytest.mark.parametrize(
    ("whitespace_name", "whitespace"),
    (("space", " "), ("tab", "\t"), ("newline", "\n")),
)
@pytest.mark.parametrize(
    ("table_name", "column_name", "id_name", "constraint_name"),
    NON_BLANK_COLUMNS,
)
def test_non_blank_values_reject_all_whitespace(
    database_connection: Connection,
    table_name: str,
    column_name: str,
    id_name: str,
    constraint_name: str,
    whitespace_name: str,
    whitespace: str,
) -> None:
    """Every non-blank field rejects space, tab, and newline-only values."""
    identifiers = _seed_valid_graph(database_connection)

    with pytest.raises(IntegrityError) as error, database_connection.begin_nested():
        database_connection.execute(
            text(f"UPDATE {table_name} SET {column_name} = :value WHERE id = :row_id"),
            {"row_id": identifiers[id_name], "value": whitespace},
        )

    assert error.value.orig.diag.constraint_name == constraint_name


@pytest.mark.parametrize(("table_name", "assignment", "id_name"), INVALID_VALUE_UPDATES)
def test_invalid_values_are_rejected(
    database_connection: Connection,
    table_name: str,
    assignment: str,
    id_name: str,
) -> None:
    """Every approved value invariant rejects a violating update."""
    identifiers = _seed_valid_graph(database_connection)
    id_column = "id"

    with pytest.raises(IntegrityError), database_connection.begin_nested():
        database_connection.execute(
            text(f"UPDATE {table_name} SET {assignment} WHERE {id_column} = :row_id"),
            {"row_id": identifiers[id_name]},
        )


@pytest.mark.parametrize(
    ("statement", "parameters"),
    (
        pytest.param(
            "INSERT INTO source_profiles (id, source_key) VALUES (:new_id, 'omf')",
            {},
            id="source-key",
        ),
        pytest.param(
            "INSERT INTO index_configs "
            "(id, config_hash, parser_config, chunk_config, tokenizer_config, "
            "embedding_config, rrf_config) VALUES "
            "(:new_id, :sha, '{}', '{}', '{}', '{}', '{}')",
            {"sha": SHA256},
            id="config-hash",
        ),
        pytest.param(
            "INSERT INTO document_contents "
            "(id, content_hash, content, byte_size) VALUES "
            "(:new_id, :sha, 'body', 4)",
            {"sha": SHA256},
            id="content-hash",
        ),
        pytest.param(
            "INSERT INTO document_occurrences "
            "(id, run_id, content_id, source_path, version_scope, decision_state, "
            "owner_domain) VALUES "
            "(:new_id, :run_id, :content_id, 'docs/a.md', 'current', 'unknown', 'docs')",
            {},
            id="run-source-path",
        ),
        pytest.param(
            "INSERT INTO document_parses "
            "(id, content_id, parser_version, chunk_config_hash) VALUES "
            "(:new_id, :content_id, 'markdown-it-1', :sha)",
            {"sha": SHA256},
            id="parse-identity",
        ),
        pytest.param(
            "INSERT INTO chunks "
            "(id, section_id, ordinal, raw_text, search_text, token_count, "
            "line_start, line_end, chunk_hash) VALUES "
            "(:new_id, :section_id, 0, 'other', 'other', 1, 1, 1, :sha)",
            {"sha": OTHER_SHA256},
            id="section-ordinal",
        ),
        pytest.param(
            "INSERT INTO chunk_embeddings "
            "(id, chunk_id, embedding_config_hash, model_name, model_revision, "
            "dimension, embedding) VALUES "
            "(:new_id, :chunk_id, :sha, 'other', 'other', 3, '[0,0,0]')",
            {"sha": SHA256},
            id="chunk-embedding-config",
        ),
        pytest.param(
            "INSERT INTO api_clients (id, name, key_id, token_hash) VALUES "
            "(:new_id, 'other', '1234567890abcdef', :token_hash)",
            {"token_hash": b"x" * 32},
            id="key-id",
        ),
    ),
)
def test_duplicate_unique_values_are_rejected(
    database_connection: Connection,
    statement: str,
    parameters: dict[str, object],
) -> None:
    """Approved natural and composite identities reject duplicates."""
    identifiers = _seed_valid_graph(database_connection)

    with pytest.raises(IntegrityError), database_connection.begin_nested():
        database_connection.execute(
            text(statement),
            {**identifiers, **parameters, "new_id": uuid4()},
        )


def test_same_client_name_with_distinct_keys_is_allowed(
    database_connection: Connection,
) -> None:
    """Client display names are not credential identities."""
    identifiers = _seed_valid_graph(database_connection)

    database_connection.execute(
        text(
            "INSERT INTO api_clients (id, name, key_id, token_hash) VALUES "
            "(:id, 'agent', 'fedcba0987654321', :token_hash)"
        ),
        {"id": uuid4(), "token_hash": b"z" * 32},
    )
    count = database_connection.execute(
        text("SELECT count(*) FROM api_clients WHERE name = 'agent'")
    ).scalar_one()

    assert identifiers["client_id"] is not None
    assert count == 2


def test_parser_versions_can_coexist_for_same_content_and_config(
    database_connection: Connection,
) -> None:
    """Parser version participates in parse identity."""
    identifiers = _seed_valid_graph(database_connection)

    database_connection.execute(
        text(
            "INSERT INTO document_parses "
            "(id, content_id, parser_version, chunk_config_hash) VALUES "
            "(:id, :content_id, 'markdown-it-2', :sha)"
        ),
        {"id": uuid4(), "content_id": identifiers["content_id"], "sha": SHA256},
    )
    count = database_connection.execute(
        text("SELECT count(*) FROM document_parses WHERE content_id = :content_id"),
        {"content_id": identifiers["content_id"]},
    ).scalar_one()

    assert count == 2


def _add_second_run(
    connection: Connection,
    identifiers: dict[str, object],
) -> dict[str, object]:
    second = {
        "second_source_id": uuid4(),
        "second_run_id": uuid4(),
        "second_occurrence_id": uuid4(),
    }
    parameters = {**identifiers, **second, "commit_sha": "d" * 40}
    connection.execute(
        text(
            "INSERT INTO source_profiles (id, source_key) VALUES "
            "(:second_source_id, 'other')"
        ),
        parameters,
    )
    connection.execute(
        text(
            "INSERT INTO index_runs "
            "(id, source_profile_id, index_config_id, commit_sha) VALUES "
            "(:second_run_id, :second_source_id, :config_id, :commit_sha)"
        ),
        parameters,
    )
    connection.execute(
        text(
            "INSERT INTO document_occurrences "
            "(id, run_id, content_id, source_path, version_scope, decision_state, "
            "owner_domain) VALUES "
            "(:second_occurrence_id, :second_run_id, :content_id, 'docs/other.md', "
            "'current', 'unknown', 'docs')"
        ),
        parameters,
    )
    return second


def test_cross_source_active_pointer_is_rejected(
    database_connection: Connection,
) -> None:
    """A profile cannot activate another profile's run."""
    identifiers = _seed_valid_graph(database_connection)
    second = _add_second_run(database_connection, identifiers)

    with pytest.raises(IntegrityError), database_connection.begin_nested():
        database_connection.execute(
            text(
                "UPDATE source_profiles SET active_index_run_id = :run_id "
                "WHERE id = :source_id"
            ),
            {
                "run_id": identifiers["run_id"],
                "source_id": second["second_source_id"],
            },
        )


@pytest.mark.parametrize(
    ("cross_snapshot_target", "constraint_name"),
    (
        ("from", "fk_document_relations_from_occurrence"),
        ("to", "fk_document_relations_to_occurrence"),
        ("evidence", "fk_document_relations_evidence_occurrence"),
    ),
)
def test_cross_run_relations_are_rejected(
    database_connection: Connection,
    cross_snapshot_target: str,
    constraint_name: str,
) -> None:
    """Relation occurrences and evidence must belong to the declared run."""
    identifiers = _seed_valid_graph(database_connection)
    second = _add_second_run(database_connection, identifiers)
    from_occurrence_id = identifiers["occurrence_id"]
    to_occurrence_id = identifiers["other_occurrence_id"]
    evidence_source_path = "docs/a.md"
    if cross_snapshot_target == "from":
        from_occurrence_id = second["second_occurrence_id"]
    elif cross_snapshot_target == "to":
        to_occurrence_id = second["second_occurrence_id"]
    else:
        evidence_source_path = "docs/other.md"

    with pytest.raises(IntegrityError) as error, database_connection.begin_nested():
        database_connection.execute(
            text(
                "INSERT INTO document_relations "
                "(id, run_id, from_occurrence_id, to_occurrence_id, relation_type, "
                "evidence_source_path, evidence_line_start, evidence_line_end) VALUES "
                "(:id, :run_id, :from_id, :to_id, 'potential_conflict', "
                ":evidence_path, 1, 1)"
            ),
            {
                "id": uuid4(),
                "run_id": identifiers["run_id"],
                "from_id": from_occurrence_id,
                "to_id": to_occurrence_id,
                "evidence_path": evidence_source_path,
            },
        )

    assert error.value.orig.diag.constraint_name == constraint_name


def test_same_run_potential_conflict_relation_is_allowed(
    database_connection: Connection,
) -> None:
    """A potential-conflict relation succeeds when all targets share its run."""
    identifiers = _seed_valid_graph(database_connection)

    relation_id = uuid4()
    database_connection.execute(
        text(
            "INSERT INTO document_relations "
            "(id, run_id, from_occurrence_id, to_occurrence_id, relation_type, "
            "evidence_source_path, evidence_line_start, evidence_line_end) VALUES "
            "(:id, :run_id, :from_id, :to_id, 'potential_conflict', "
            "'docs/a.md', 1, 1)"
        ),
        {
            "id": relation_id,
            "run_id": identifiers["run_id"],
            "from_id": identifiers["occurrence_id"],
            "to_id": identifiers["other_occurrence_id"],
        },
    )
    relation_type = database_connection.execute(
        text("SELECT relation_type FROM document_relations WHERE id = :id"),
        {"id": relation_id},
    ).scalar_one()

    assert relation_type == "potential_conflict"


def test_cross_parse_section_parent_is_rejected(
    database_connection: Connection,
) -> None:
    """A section parent must belong to the same parse."""
    identifiers = _seed_valid_graph(database_connection)
    other_parse_id = uuid4()
    database_connection.execute(
        text(
            "INSERT INTO document_parses "
            "(id, content_id, parser_version, chunk_config_hash) VALUES "
            "(:id, :content_id, 'markdown-it-2', :sha)"
        ),
        {"id": other_parse_id, "content_id": identifiers["content_id"], "sha": SHA256},
    )

    with pytest.raises(IntegrityError), database_connection.begin_nested():
        database_connection.execute(
            text(
                "INSERT INTO sections "
                "(id, parse_id, parent_section_id, ordinal, level, heading_path, "
                "body, line_start, line_end) VALUES "
                "(:id, :parse_id, :parent_id, 0, 1, ARRAY['child'], 'child', 1, 1)"
            ),
            {
                "id": uuid4(),
                "parse_id": other_parse_id,
                "parent_id": identifiers["section_id"],
            },
        )


def test_vector_dimension_mismatch_is_rejected(
    database_connection: Connection,
) -> None:
    """Stored dimension metadata must match the unbounded vector value."""
    identifiers = _seed_valid_graph(database_connection)

    with pytest.raises(IntegrityError), database_connection.begin_nested():
        database_connection.execute(
            text("UPDATE chunk_embeddings SET dimension = 4 WHERE id = :id"),
            {"id": identifiers["embedding_id"]},
        )


def test_active_run_delete_is_restricted(database_connection: Connection) -> None:
    """An active run remains protected by the profile pointer."""
    identifiers = _seed_valid_graph(database_connection)
    second = _add_second_run(database_connection, identifiers)
    database_connection.execute(
        text(
            "UPDATE source_profiles SET active_index_run_id = :run_id "
            "WHERE id = :source_id"
        ),
        {
            "run_id": second["second_run_id"],
            "source_id": second["second_source_id"],
        },
    )
    database_connection.execute(
        text("DELETE FROM document_occurrences WHERE run_id = :run_id"),
        {"run_id": second["second_run_id"]},
    )

    with pytest.raises(IntegrityError), database_connection.begin_nested():
        database_connection.execute(
            text("DELETE FROM index_runs WHERE id = :run_id"),
            {"run_id": second["second_run_id"]},
        )


def test_three_and_1024_dimension_vectors_are_allowed(
    database_connection: Connection,
) -> None:
    """Unbounded vector storage accepts each matching row dimension."""
    identifiers = _seed_valid_graph(database_connection)
    section_id = uuid4()
    chunk_id = uuid4()
    database_connection.execute(
        text(
            "INSERT INTO sections "
            "(id, parse_id, ordinal, level, heading_path, body, line_start, line_end) "
            "VALUES (:id, :parse_id, 1, 1, ARRAY['large'], 'large', 2, 2)"
        ),
        {"id": section_id, "parse_id": identifiers["parse_id"]},
    )
    database_connection.execute(
        text(
            "INSERT INTO chunks "
            "(id, section_id, ordinal, raw_text, search_text, token_count, "
            "line_start, line_end, chunk_hash) VALUES "
            "(:id, :section_id, 0, 'large', 'large', 1, 2, 2, :sha)"
        ),
        {"id": chunk_id, "section_id": section_id, "sha": OTHER_SHA256},
    )
    vector = "[" + ",".join("0" for _ in range(1024)) + "]"

    database_connection.execute(
        text(
            "INSERT INTO chunk_embeddings "
            "(id, chunk_id, embedding_config_hash, model_name, model_revision, "
            "dimension, embedding) VALUES "
            "(:id, :chunk_id, :sha, 'model', 'revision', 1024, :embedding)"
        ),
        {"id": uuid4(), "chunk_id": chunk_id, "sha": SHA256, "embedding": vector},
    )
    dimensions = database_connection.execute(
        text("SELECT array_agg(dimension ORDER BY dimension) FROM chunk_embeddings")
    ).scalar_one()

    assert dimensions == [3, 1024]


def test_parse_delete_cascades_descendants_but_shared_content_is_restricted(
    database_connection: Connection,
) -> None:
    """Parse descendants cascade while an occurrence protects shared content."""
    identifiers = _seed_valid_graph(database_connection)

    with pytest.raises(IntegrityError), database_connection.begin_nested():
        database_connection.execute(
            text("DELETE FROM document_contents WHERE id = :id"),
            {"id": identifiers["content_id"]},
        )

    database_connection.execute(
        text("DELETE FROM document_parses WHERE id = :id"),
        {"id": identifiers["parse_id"]},
    )
    counts = database_connection.execute(
        text(
            "SELECT "
            "(SELECT count(*) FROM sections), "
            "(SELECT count(*) FROM chunks), "
            "(SELECT count(*) FROM chunk_embeddings)"
        )
    ).one()

    assert counts == (0, 0, 0)


def test_occurrence_delete_cascades_relations_but_preserves_run(
    database_connection: Connection,
) -> None:
    """Deleting a relation endpoint removes relations without deleting its run."""
    identifiers = _seed_valid_graph(database_connection)

    database_connection.execute(
        text("DELETE FROM document_occurrences WHERE id = :id"),
        {"id": identifiers["occurrence_id"]},
    )
    relation_count = database_connection.execute(
        text("SELECT count(*) FROM document_relations")
    ).scalar_one()
    run_count = database_connection.execute(
        text("SELECT count(*) FROM index_runs WHERE id = :id"),
        {"id": identifiers["run_id"]},
    ).scalar_one()

    assert relation_count == 0
    assert run_count == 1
