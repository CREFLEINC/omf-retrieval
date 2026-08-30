"""Append-only PostgreSQL storage for immutable search policy manifests."""

from __future__ import annotations

import re
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from omf_retrieval.application.search.policy import (
    SearchPolicyManifest,
    SearchPolicySnapshot,
    SearchPolicyValidationError,
    validated_search_policy_snapshot,
)
from omf_retrieval.infrastructure.database.models import (
    SearchPolicyManifest as SearchPolicyManifestRow,
)
from omf_retrieval.infrastructure.database.repository_errors import (
    RepositoryInvariantError,
)

_SHA256 = re.compile(r"[0-9a-f]{64}")
_SAFE_ERROR = "Search policy manifest is inconsistent"


class PostgresSearchPolicyRepository:
    """Register and resolve policies without update or delete operations."""

    def __init__(self, session: Session) -> None:
        if not isinstance(session, Session) and not hasattr(session, "scalar"):
            raise TypeError("session must provide the SQLAlchemy Session contract")
        self._session = session

    def register(self, snapshot: SearchPolicySnapshot) -> SearchPolicyManifest:
        """Insert once by canonical hash and resolve concurrent duplicates."""
        if type(snapshot) is not SearchPolicySnapshot:
            raise RepositoryInvariantError(_SAFE_ERROR)
        digest = snapshot.config_hash
        statement = (
            insert(SearchPolicyManifestRow)
            .values(
                id=uuid4(),
                config_hash=digest,
                snapshot=snapshot.as_config(),
            )
            .on_conflict_do_nothing(
                index_elements=[SearchPolicyManifestRow.config_hash]
            )
            .returning(SearchPolicyManifestRow.id)
        )
        policy_id = self._session.scalar(statement)
        if policy_id is not None:
            try:
                return SearchPolicyManifest(policy_id, digest, snapshot)
            except SearchPolicyValidationError as error:
                raise RepositoryInvariantError(_SAFE_ERROR) from error
        return self.resolve(digest)

    def resolve(self, config_hash: str) -> SearchPolicyManifest:
        """Resolve a manifest only when its persisted content matches its hash."""
        if type(config_hash) is not str or _SHA256.fullmatch(config_hash) is None:
            raise RepositoryInvariantError(_SAFE_ERROR)
        stored = self._session.scalar(
            select(SearchPolicyManifestRow).where(
                SearchPolicyManifestRow.config_hash == config_hash
            )
        )
        if stored is None:
            raise RepositoryInvariantError(_SAFE_ERROR)
        try:
            snapshot = validated_search_policy_snapshot(stored.snapshot)
            return SearchPolicyManifest(stored.id, stored.config_hash, snapshot)
        except (AttributeError, SearchPolicyValidationError, TypeError) as error:
            raise RepositoryInvariantError(_SAFE_ERROR) from error


__all__ = ["PostgresSearchPolicyRepository"]
