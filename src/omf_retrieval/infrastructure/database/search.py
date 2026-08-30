"""PostgreSQL pg_trgm and exact pgvector candidate retrieval."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql.elements import TextClause

from omf_retrieval.application.admin.tokens import (
    AuthorizedSource,
    SourceAccessError,
)
from omf_retrieval.application.indexing.config_identity import (
    IndexConfigValidationError,
    validated_retrieval_config,
)
from omf_retrieval.application.search.ports import (
    ActiveIndex,
    Candidate,
    CandidateBatch,
    NoActiveIndexError,
    Origin,
    ScoredCandidate,
    SearchUnavailableError,
)
from omf_retrieval.domain.models import EmbeddingDescriptor
from omf_retrieval.infrastructure.database.repository_auth import (
    has_source_grant_in_session,
)
from omf_retrieval.settings import (
    MVP_KEYWORD_WEIGHT,
    MVP_RRF_K,
    MVP_VECTOR_WEIGHT,
)

_SHA256 = re.compile(r"[0-9a-f]{64}")

_AUTHORIZED_SCOPE = """
authorized_scope AS MATERIALIZED (
    SELECT
        sp.id AS source_profile_id,
        sp.active_index_run_id AS run_id,
        ir.commit_sha,
        ic.parser_config ->> 'version' AS parser_version,
        ic.chunk_config ->> 'hash' AS chunk_config_hash
    FROM api_clients AS ac
    JOIN client_source_grants AS csg ON csg.client_id = ac.id
    JOIN source_profiles AS sp ON sp.id = csg.source_profile_id
    JOIN index_runs AS ir ON ir.id = sp.active_index_run_id
        AND ir.source_profile_id = sp.id
        AND ir.status = 'active'
    JOIN index_configs AS ic ON ic.id = ir.index_config_id
    WHERE ac.id = :client_id
      AND ac.status = 'active'
      AND (ac.expires_at IS NULL OR ac.expires_at > now())
      AND sp.source_key = :source_key
),
authorized_unique_chunks AS MATERIALIZED (
    SELECT
        scope.run_id,
        scope.commit_sha,
        c.id AS chunk_id,
        s.id AS parent_id,
        s.heading_path,
        c.raw_text AS excerpt,
        c.search_text,
        c.line_start,
        c.line_end,
        jsonb_agg(
            DISTINCT jsonb_build_object(
                'source_path', occurrence.source_path,
                'content_hash', content.content_hash
            )
        ) AS origins
    FROM authorized_scope AS scope
    JOIN document_occurrences AS occurrence ON occurrence.run_id = scope.run_id
    JOIN document_contents AS content ON content.id = occurrence.content_id
    JOIN document_parses AS parse ON parse.content_id = content.id
        AND parse.parser_version = scope.parser_version
        AND parse.chunk_config_hash = scope.chunk_config_hash
    JOIN sections AS s ON s.parse_id = parse.id
    JOIN chunks AS c ON c.section_id = s.id
    GROUP BY
        scope.run_id,
        scope.commit_sha,
        c.id,
        s.id,
        s.heading_path,
        c.raw_text,
        c.search_text,
        c.line_start,
        c.line_end
)
"""

_ACTIVE_INDEX_SQL = text(
    """
    SELECT
        sp.active_index_run_id AS run_id,
        ir.commit_sha,
        ic.embedding_config -> 'document' ->> 'model_name' AS model_name,
        ic.embedding_config -> 'document' ->> 'revision' AS model_revision,
        (ic.embedding_config -> 'document' ->> 'dimension')::integer AS dimension,
        ic.embedding_config -> 'document' ->> 'provider' AS provider,
        (ic.embedding_config -> 'document' ->> 'normalize_embeddings')::boolean
            AS normalize_embeddings
    FROM source_profiles AS sp
    JOIN index_runs AS ir ON ir.id = sp.active_index_run_id
        AND ir.source_profile_id = sp.id
        AND ir.status = 'active'
    JOIN index_configs AS ic ON ic.id = ir.index_config_id
    JOIN client_source_grants AS csg ON csg.source_profile_id = sp.id
    JOIN api_clients AS ac ON ac.id = csg.client_id
    WHERE ac.id = :client_id
      AND ac.status = 'active'
      AND (ac.expires_at IS NULL OR ac.expires_at > now())
      AND sp.source_key = :source_key
    FOR SHARE OF sp, ir
    """
)

_KEYWORD_SQL = text(
    f"""
    WITH {_AUTHORIZED_SCOPE},
    keyword_scored AS MATERIALIZED (
        SELECT
            candidate.*,
            similarity(candidate.search_text, :query) AS raw_score
        FROM authorized_unique_chunks AS candidate
    )
    SELECT
        run_id,
        commit_sha,
        chunk_id,
        parent_id,
        heading_path,
        excerpt,
        line_start,
        line_end,
        origins,
        raw_score
    FROM keyword_scored
    WHERE raw_score >= :keyword_similarity_floor
    ORDER BY raw_score DESC, chunk_id
    LIMIT :keyword_candidate_limit
    """
)

_VECTOR_SQL = text(
    f"""
    WITH {_AUTHORIZED_SCOPE},
    vector_scored AS MATERIALIZED (
        SELECT
            candidate.*,
            1.0 - (
                embedding.embedding <=> CAST(:query_vector AS vector)
            ) AS raw_score
        FROM authorized_unique_chunks AS candidate
        JOIN chunk_embeddings AS embedding ON embedding.chunk_id = candidate.chunk_id
          AND embedding.embedding_config_hash = :embedding_config_hash
          AND embedding.model_name = :model_name
          AND embedding.model_revision = :model_revision
          AND embedding.dimension = :dimension
          AND embedding.status = 'ready'
    )
    SELECT
        run_id,
        commit_sha,
        chunk_id,
        parent_id,
        heading_path,
        excerpt,
        line_start,
        line_end,
        origins,
        raw_score
    FROM vector_scored
    WHERE raw_score >= :vector_similarity_floor
    ORDER BY raw_score DESC, chunk_id
    LIMIT :vector_candidate_limit
    """
)


def keyword_candidate_statement(
    client_id: UUID,
    source_key: str,
    query: str,
    *,
    limit: int,
    minimum_score: float = 0.0,
) -> TextClause:
    """Bind identities and the fixed top-N policy without interpolation."""
    _validate_identity(client_id, source_key, limit)
    _validate_minimum_score(minimum_score)
    if type(query) is not str or not query.strip():
        raise ValueError("Invalid keyword query")
    return _KEYWORD_SQL.bindparams(
        bindparam("client_id", value=client_id),
        bindparam("source_key", value=source_key),
        bindparam("query", value=query),
        bindparam("keyword_similarity_floor", value=minimum_score),
        bindparam("keyword_candidate_limit", value=limit),
    )


def vector_candidate_statement(
    client_id: UUID,
    source_key: str,
    query_vector: tuple[float, ...],
    descriptor: EmbeddingDescriptor,
    embedding_config_hash: str,
    *,
    limit: int,
    minimum_score: float = 0.0,
) -> TextClause:
    """Bind an exact cosine query and active embedding identity."""
    _validate_identity(client_id, source_key, limit)
    _validate_minimum_score(minimum_score)
    if (
        type(query_vector) is not tuple
        or type(descriptor) is not EmbeddingDescriptor
        or len(query_vector) != descriptor.dimension
        or any(type(value) is not float for value in query_vector)
        or type(embedding_config_hash) is not str
        or _SHA256.fullmatch(embedding_config_hash) is None
    ):
        raise ValueError("Invalid vector query")
    vector_literal = "[" + ",".join(repr(value) for value in query_vector) + "]"
    return _VECTOR_SQL.bindparams(
        bindparam("client_id", value=client_id),
        bindparam("source_key", value=source_key),
        bindparam("query_vector", value=vector_literal),
        bindparam("embedding_config_hash", value=embedding_config_hash),
        bindparam("model_name", value=descriptor.model_name),
        bindparam("model_revision", value=descriptor.revision),
        bindparam("dimension", value=descriptor.dimension),
        bindparam("vector_similarity_floor", value=minimum_score),
        bindparam("vector_candidate_limit", value=limit),
    )


class PostgresHybridSearchRepository:
    """Execute both candidate lanes inside one authorized transaction."""

    def __init__(
        self,
        transactions: sessionmaker[Session],
        *,
        embedding_config_hash: str,
        embedding_provider: str,
    ) -> None:
        if (
            type(embedding_config_hash) is not str
            or _SHA256.fullmatch(embedding_config_hash) is None
        ):
            raise ValueError("Invalid embedding config hash")
        self._transactions = transactions
        self._embedding_config_hash = embedding_config_hash
        if type(embedding_provider) is not str or not embedding_provider.strip():
            raise ValueError("Invalid embedding provider")
        self._embedding_provider = embedding_provider

    def active_index(
        self,
        authorized: AuthorizedSource,
        descriptor: EmbeddingDescriptor,
        *,
        normalize_embeddings: bool,
    ) -> ActiveIndex:
        """Resolve the locked active coordinate before model inference."""
        _validate_authorized(authorized)
        try:
            with self._transactions.begin() as database_session:
                self._require_grant(database_session, authorized)
                return self._active_index(
                    database_session,
                    authorized,
                    descriptor,
                    normalize_embeddings=normalize_embeddings,
                )
        except (SourceAccessError, NoActiveIndexError, SearchUnavailableError):
            raise
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            raise SearchUnavailableError from None

    def retrieve(
        self,
        authorized: AuthorizedSource,
        query: str,
        query_vector: tuple[float, ...],
        descriptor: EmbeddingDescriptor,
        *,
        keyword_limit: int,
        vector_limit: int,
        normalize_embeddings: bool,
        keyword_similarity_floor: float,
        vector_similarity_floor: float,
    ) -> CandidateBatch:
        """Load active candidates after the SQL-level grant recheck."""
        _validate_authorized(authorized)
        try:
            with self._transactions.begin() as database_session:
                self._require_grant(database_session, authorized)
                active = self._active_index(
                    database_session,
                    authorized,
                    descriptor,
                    normalize_embeddings=normalize_embeddings,
                )
                keyword_rows = database_session.execute(
                    keyword_candidate_statement(
                        authorized.client.client_id,
                        authorized.source_key,
                        query,
                        limit=keyword_limit,
                        minimum_score=keyword_similarity_floor,
                    )
                ).mappings()
                vector_rows = database_session.execute(
                    vector_candidate_statement(
                        authorized.client.client_id,
                        authorized.source_key,
                        query_vector,
                        descriptor,
                        self._embedding_config_hash,
                        limit=vector_limit,
                        minimum_score=vector_similarity_floor,
                    )
                ).mappings()
                keyword = _candidate_rows(keyword_rows, active)
                vector = _candidate_rows(vector_rows, active)
        except (SourceAccessError, NoActiveIndexError, SearchUnavailableError):
            raise
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            raise SearchUnavailableError from None
        return CandidateBatch(active, keyword, vector)

    def retrieve_for_calibration(
        self,
        authorized: AuthorizedSource,
        query: str,
        query_vector: tuple[float, ...],
        descriptor: EmbeddingDescriptor,
        *,
        keyword_limit: int,
        vector_limit: int,
        normalize_embeddings: bool = True,
    ) -> CandidateBatch:
        """Return nonnegative raw-score candidates without enabling public search."""
        _validate_authorized(authorized)
        try:
            with self._transactions.begin() as database_session:
                self._require_grant(database_session, authorized)
                active = self._active_index(
                    database_session,
                    authorized,
                    descriptor,
                    normalize_embeddings=normalize_embeddings,
                )
                keyword_rows = database_session.execute(
                    keyword_candidate_statement(
                        authorized.client.client_id,
                        authorized.source_key,
                        query,
                        limit=keyword_limit,
                        minimum_score=0.0,
                    )
                ).mappings()
                vector_rows = database_session.execute(
                    vector_candidate_statement(
                        authorized.client.client_id,
                        authorized.source_key,
                        query_vector,
                        descriptor,
                        self._embedding_config_hash,
                        limit=vector_limit,
                        minimum_score=0.0,
                    )
                ).mappings()
                keyword = _candidate_rows(keyword_rows, active)
                vector = _candidate_rows(vector_rows, active)
        except (SourceAccessError, NoActiveIndexError, SearchUnavailableError):
            raise
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            raise SearchUnavailableError from None
        return CandidateBatch(active, keyword, vector)

    def is_ready(
        self,
        authorized: AuthorizedSource,
        descriptor: EmbeddingDescriptor,
        *,
        normalize_embeddings: bool,
    ) -> bool:
        """Check DB, transactional grant, active run, and model identity."""
        try:
            _validate_authorized(authorized)
            with self._transactions.begin() as database_session:
                self._require_grant(database_session, authorized)
                self._active_index(
                    database_session,
                    authorized,
                    descriptor,
                    normalize_embeddings=normalize_embeddings,
                )
            return True
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            return False

    @staticmethod
    def _require_grant(database_session: Session, authorized: AuthorizedSource) -> None:
        if (
            has_source_grant_in_session(
                database_session,
                authorized.client.client_id,
                authorized.source_key,
            )
            is not True
        ):
            raise SourceAccessError

    def _active_index(
        self,
        database_session: Session,
        authorized: AuthorizedSource,
        descriptor: EmbeddingDescriptor,
        *,
        normalize_embeddings: bool,
    ) -> ActiveIndex:
        row = (
            database_session.execute(
                _ACTIVE_INDEX_SQL.bindparams(
                    bindparam("client_id", value=authorized.client.client_id),
                    bindparam("source_key", value=authorized.source_key),
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise NoActiveIndexError
        if (
            row["provider"] != self._embedding_provider
            or row["model_name"] != descriptor.model_name
            or row["model_revision"] != descriptor.revision
            or row["dimension"] != descriptor.dimension
            or type(normalize_embeddings) is not bool
            or row["normalize_embeddings"] is not normalize_embeddings
        ):
            raise SearchUnavailableError
        try:
            return ActiveIndex(row["run_id"], row["commit_sha"])
        except (TypeError, ValueError):
            raise SearchUnavailableError from None


def validate_persisted_retrieval_config(
    value: object,
    *,
    expected: object,
    require_calibrated: bool = True,
) -> None:
    """Require the active run's exact persisted RRF behavior snapshot."""
    try:
        stored = validated_retrieval_config(value)
        configured = validated_retrieval_config(expected)
    except (IndexConfigValidationError, TypeError, ValueError):
        raise SearchUnavailableError from None
    if (
        stored != configured
        or configured["k"] != MVP_RRF_K
        or configured["keyword_weight"] != MVP_KEYWORD_WEIGHT
        or configured["vector_weight"] != MVP_VECTOR_WEIGHT
        or type(require_calibrated) is not bool
        or (require_calibrated and configured["evidence_floor_status"] != "calibrated")
    ):
        raise SearchUnavailableError


def _candidate_rows(
    rows: Sequence[Mapping[str, Any]], active: ActiveIndex
) -> tuple[ScoredCandidate, ...]:
    candidates: list[ScoredCandidate] = []
    for row in rows:
        if row["run_id"] != active.run_id or row["commit_sha"] != active.commit_sha:
            raise SearchUnavailableError
        raw_origins = row["origins"]
        if type(raw_origins) is str:
            raw_origins = json.loads(raw_origins)
        if not isinstance(raw_origins, list) or not all(
            isinstance(origin, Mapping) for origin in raw_origins
        ):
            raise SearchUnavailableError
        try:
            origins = tuple(
                sorted(
                    {
                        Origin(origin["source_path"], origin["content_hash"])
                        for origin in raw_origins
                    }
                )
            )
            candidate = ScoredCandidate(
                candidate=Candidate(
                    chunk_id=row["chunk_id"],
                    parent_id=row["parent_id"],
                    heading_path=tuple(row["heading_path"]),
                    excerpt=row["excerpt"],
                    line_start=row["line_start"],
                    line_end=row["line_end"],
                    origins=origins,
                ),
                raw_score=row["raw_score"],
            )
        except (KeyError, TypeError, ValueError):
            raise SearchUnavailableError from None
        candidates.append(candidate)
    return tuple(candidates)


def _validate_authorized(authorized: object) -> None:
    if type(authorized) is not AuthorizedSource or authorized.source_key != "omf":
        raise SourceAccessError


def _validate_identity(client_id: object, source_key: object, limit: object) -> None:
    if (
        type(client_id) is not UUID
        or type(source_key) is not str
        or source_key != "omf"
        or type(limit) is not int
        or limit <= 0
    ):
        raise ValueError("Invalid candidate policy")


def _validate_minimum_score(value: object) -> None:
    if type(value) is not float or not 0.0 <= value <= 1.0:
        raise ValueError("Invalid evidence floor")


__all__ = [
    "PostgresHybridSearchRepository",
    "keyword_candidate_statement",
    "validate_persisted_retrieval_config",
    "vector_candidate_statement",
]
