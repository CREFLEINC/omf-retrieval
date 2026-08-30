"""PostgreSQL integration contracts for immutable search policy manifests."""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import database_test_utils as database_test_support
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from omf_retrieval.application.search.policy import (
    validated_search_policy_snapshot,
)
from omf_retrieval.infrastructure.database.models import SearchPolicyManifest
from omf_retrieval.infrastructure.database.repository_errors import (
    RepositoryInvariantError,
)
from omf_retrieval.infrastructure.database.repository_policy import (
    PostgresSearchPolicyRepository,
)
from omf_retrieval.infrastructure.database.session import (
    create_database_engine,
    create_session_factory,
)


def _config(**changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "query_embedding_model_name": "Qwen/Qwen3-Embedding-0.6B",
        "query_embedding_revision": "revision-1",
        "query_embedding_dimension": 1024,
        "query_embedding_normalize_embeddings": True,
        "query_instruction": "Instruct: {query}",
        "keyword_candidate_limit": 50,
        "vector_candidate_limit": 50,
        "rrf_k": 60,
        "keyword_weight": 1.0,
        "vector_weight": 1.0,
        "keyword_similarity_floor": 0.03658536400000001,
        "vector_similarity_floor": 0.48344050397156374,
        "calibration_status": "calibrated",
    }
    value.update(changes)
    return value


@pytest.fixture
def policy_sessions(request: pytest.FixtureRequest) -> Iterator[sessionmaker[Session]]:
    engine = create_database_engine(database_test_support.test_database_url())
    request.addfinalizer(engine.dispose)
    with engine.begin() as connection:
        database_test_support.assert_safe_test_connection(connection)
        connection.execute(text("TRUNCATE TABLE search_policy_manifests"))
    try:
        yield create_session_factory(engine)
    finally:
        with engine.begin() as connection:
            database_test_support.assert_safe_test_connection(connection)
            connection.execute(text("TRUNCATE TABLE search_policy_manifests"))


def test_concurrent_and_repeated_registration_resolves_one_row(
    policy_sessions: sessionmaker[Session],
) -> None:
    snapshot = validated_search_policy_snapshot(_config())
    barrier = Barrier(2)

    def register() -> object:
        with policy_sessions.begin() as session:
            barrier.wait()
            return PostgresSearchPolicyRepository(session).register(snapshot)

    with ThreadPoolExecutor(max_workers=2) as executor:
        manifests = tuple(executor.map(lambda _ordinal: register(), range(2)))
    with policy_sessions.begin() as session:
        repeated = PostgresSearchPolicyRepository(session).register(snapshot)
        row_count = session.scalar(
            select(func.count()).select_from(SearchPolicyManifest)
        )

    assert manifests[0] == manifests[1] == repeated
    assert row_count == 1


def test_floor_change_adds_policy_only_and_preserves_index_artifact_counts(
    policy_sessions: sessionmaker[Session],
) -> None:
    tracked_tables = ("index_runs", "chunks", "chunk_embeddings")
    with policy_sessions.begin() as session:
        before = {
            table: session.scalar(text(f"SELECT count(*) FROM {table}"))
            for table in tracked_tables
        }
        repository = PostgresSearchPolicyRepository(session)
        first = repository.register(validated_search_policy_snapshot(_config()))
        second = repository.register(
            validated_search_policy_snapshot(_config(vector_similarity_floor=0.6))
        )
        after = {
            table: session.scalar(text(f"SELECT count(*) FROM {table}"))
            for table in tracked_tables
        }

    assert first.policy_id != second.policy_id
    assert before == after


def test_signed_zero_registration_survives_jsonb_round_trip(
    policy_sessions: sessionmaker[Session],
) -> None:
    """JSONB positive-zero normalization preserves the canonical identity."""
    signed_zero = validated_search_policy_snapshot(
        _config(keyword_similarity_floor=-0.0, vector_similarity_floor=-0.0)
    )
    positive_zero = validated_search_policy_snapshot(
        _config(keyword_similarity_floor=0.0, vector_similarity_floor=0.0)
    )
    with policy_sessions.begin() as session:
        first = PostgresSearchPolicyRepository(session).register(signed_zero)
    with policy_sessions.begin() as session:
        repository = PostgresSearchPolicyRepository(session)
        second = repository.register(positive_zero)
        resolved = repository.resolve(signed_zero.config_hash)

    assert first == second == resolved
    assert signed_zero == positive_zero


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_database_trigger_rejects_update_and_delete(
    policy_sessions: sessionmaker[Session],
    operation: str,
) -> None:
    snapshot = validated_search_policy_snapshot(_config())
    with policy_sessions.begin() as session:
        manifest = PostgresSearchPolicyRepository(session).register(snapshot)

    with pytest.raises(DBAPIError, match="append-only"):
        with policy_sessions.begin() as session:
            if operation == "update":
                session.execute(
                    text(
                        "UPDATE search_policy_manifests SET config_hash = :hash "
                        "WHERE id = :id"
                    ),
                    {"hash": "f" * 64, "id": manifest.policy_id},
                )
            else:
                session.execute(
                    text("DELETE FROM search_policy_manifests WHERE id = :id"),
                    {"id": manifest.policy_id},
                )


def test_repository_rejects_raw_hash_content_mismatch_safely(
    policy_sessions: sessionmaker[Session],
) -> None:
    requested = validated_search_policy_snapshot(_config())
    changed = validated_search_policy_snapshot(_config(vector_similarity_floor=0.6))
    with policy_sessions.begin() as session:
        session.execute(
            text(
                "INSERT INTO search_policy_manifests (id, config_hash, snapshot) "
                "VALUES (:id, :hash, CAST(:snapshot AS jsonb))"
            ),
            {
                "id": uuid4(),
                "hash": requested.config_hash,
                "snapshot": changed.canonical_json.decode("utf-8"),
            },
        )

    with policy_sessions.begin() as session:
        with pytest.raises(
            RepositoryInvariantError,
            match="^Search policy manifest is inconsistent$",
        ) as error:
            PostgresSearchPolicyRepository(session).resolve(requested.config_hash)

    assert requested.config_hash not in str(error.value)
