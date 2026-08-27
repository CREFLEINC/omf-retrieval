"""Atomically activate and roll back complete index generations."""

from __future__ import annotations

import re
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from omf_retrieval.application.indexing.config_identity import (
    document_embedding_config_hash,
    full_index_config_hash,
)
from omf_retrieval.domain.errors import DomainError
from omf_retrieval.domain.models import EmbeddingDescriptor

_SAFE_ACTOR = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@-]{0,127}")


class ActivationError(DomainError):
    """Raised when a requested lifecycle transition is invalid."""


class RollbackReadinessError(ActivationError):
    """Raised before mutation when the previous generation cannot be served."""


@dataclass(frozen=True, slots=True)
class RollbackCandidate:
    """Locked current/previous identities and the target's immutable config."""

    source_key: str
    active_run_id: UUID
    active_config_hash: str
    target_run_id: UUID
    target_config_hash: str
    parser_config: dict[str, object]
    chunk_config: dict[str, object]
    tokenizer_config: dict[str, object]
    embedding_config: dict[str, object]
    rrf_config: dict[str, object]


@dataclass(frozen=True, slots=True)
class TransitionResult:
    """Safe committed lifecycle transition identity."""

    action: str
    occurred_at: datetime
    source_key: str
    from_run_id: UUID | None
    to_run_id: UUID
    from_config_hash: str | None
    to_config_hash: str


@dataclass(frozen=True, slots=True)
class ActivationAuditEvent:
    """Allowlisted post-commit lifecycle audit payload."""

    actor: str
    occurred_at: datetime
    source_key: str
    from_run_id: UUID | None
    to_run_id: UUID
    from_config_hash: str | None
    to_config_hash: str
    action: str


class PostCommitAuditError(RuntimeError):
    """Report audit sink failure without concealing the committed transition."""

    def __init__(self, result: TransitionResult) -> None:
        super().__init__("Index transition committed but audit logging failed.")
        self.committed = True
        self.result = result


class _ActivationRepository(Protocol):
    def activate(
        self, source_key: str, run_id: UUID, occurred_at: datetime
    ) -> TransitionResult:
        """Lock, validate, activate, and prune one ready generation."""

    def prepare_rollback(self, source_key: str) -> RollbackCandidate:
        """Lock and inspect the unique active/previous pair without mutation."""

    def rollback(
        self, candidate: RollbackCandidate, occurred_at: datetime
    ) -> TransitionResult:
        """Exchange the already locked active and previous generations."""


class _Transactions(Protocol):
    def begin(self) -> AbstractContextManager[object]:
        """Open one transaction and yield its repository session."""


class _AuditLogger(Protocol):
    def write(self, event: ActivationAuditEvent) -> None:
        """Write one allowlisted structured event after commit."""


class _EmbeddingConfigSnapshot(Protocol):
    @property
    def descriptor(self) -> EmbeddingDescriptor:
        """Return model identity derived from the immutable full snapshot."""

    def as_config(self) -> dict[str, object]:
        """Return a fresh canonical JSON-compatible configuration."""


class _EmbeddingReadiness(Protocol):
    @property
    def descriptor(self) -> EmbeddingDescriptor:
        """Return the provider's immutable public model identity."""

    @property
    def embedding_config_snapshot(self) -> _EmbeddingConfigSnapshot:
        """Return every document and query behavior identity field."""

    def is_ready(self) -> bool:
        """Return exact cache/provider readiness without mutation."""


class ActivationService:
    """Coordinate transactional lifecycle mutation and post-commit audit."""

    def __init__(
        self,
        *,
        transactions: _Transactions,
        repository_factory: Callable[[object], _ActivationRepository],
        embedding_provider: _EmbeddingReadiness,
        audit_logger: _AuditLogger,
        clock: Callable[[], datetime],
    ) -> None:
        self._transactions = transactions
        self._repository_factory = repository_factory
        self._embedding_provider = embedding_provider
        self._audit_logger = audit_logger
        self._clock = clock

    def activate(
        self, *, source_key: str, run_id: UUID, actor: str
    ) -> TransitionResult:
        """Activate one READY generation and audit only after its commit."""
        occurred_at = _validated_request(source_key, actor, self._clock)
        if type(run_id) is not UUID:
            raise ActivationError("run_id must be an exact UUID")
        with self._transactions.begin() as transaction:
            result = self._repository_factory(transaction).activate(
                source_key,
                run_id,
                occurred_at,
            )
        self._write_audit(actor, result)
        return result

    def rollback(self, *, source_key: str, actor: str) -> TransitionResult:
        """Roll back to the unique previous generation after readiness checks."""
        occurred_at = _validated_request(source_key, actor, self._clock)
        with self._transactions.begin() as transaction:
            repository = self._repository_factory(transaction)
            candidate = repository.prepare_rollback(source_key)
            self._assert_rollback_readiness(candidate, source_key)
            result = repository.rollback(candidate, occurred_at)
        self._write_audit(actor, result)
        return result

    def _assert_rollback_readiness(
        self,
        candidate: RollbackCandidate,
        source_key: str,
    ) -> None:
        try:
            if type(candidate) is not RollbackCandidate:
                raise ValueError
            if candidate.source_key != source_key:
                raise ValueError
            canonical_hash = full_index_config_hash(
                parser_config=candidate.parser_config,
                chunk_config=candidate.chunk_config,
                tokenizer_config=candidate.tokenizer_config,
                embedding_config=candidate.embedding_config,
                rrf_config=candidate.rrf_config,
            )
            if canonical_hash != candidate.target_config_hash:
                raise ValueError
            target_document_hash = document_embedding_config_hash(
                candidate.embedding_config
            )
            snapshot = self._embedding_provider.embedding_config_snapshot
            if type(snapshot.descriptor) is not EmbeddingDescriptor:
                raise ValueError
            if snapshot.descriptor != self._embedding_provider.descriptor:
                raise ValueError
            provider_config = snapshot.as_config()
            if provider_config != candidate.embedding_config:
                raise ValueError
            if document_embedding_config_hash(provider_config) != target_document_hash:
                raise ValueError
            if self._embedding_provider.is_ready() is not True:
                raise ValueError
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            raise RollbackReadinessError(
                "Rollback target embedding configuration is not ready."
            ) from None

    def _write_audit(self, actor: str, result: TransitionResult) -> None:
        event = ActivationAuditEvent(
            actor=actor,
            occurred_at=result.occurred_at,
            source_key=result.source_key,
            from_run_id=result.from_run_id,
            to_run_id=result.to_run_id,
            from_config_hash=result.from_config_hash,
            to_config_hash=result.to_config_hash,
            action=result.action,
        )
        try:
            self._audit_logger.write(event)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            raise PostCommitAuditError(result) from None


def _validated_request(
    source_key: object,
    actor: object,
    clock: Callable[[], datetime],
) -> datetime:
    if type(source_key) is not str or _SAFE_ACTOR.fullmatch(source_key) is None:
        raise ActivationError("source_key must be a bounded safe identifier")
    if type(actor) is not str or _SAFE_ACTOR.fullmatch(actor) is None:
        raise ActivationError("actor must be a bounded safe identifier")
    occurred_at = clock()
    if type(occurred_at) is not datetime or occurred_at.tzinfo is not UTC:
        raise ActivationError("event time must be an aware UTC datetime")
    return occurred_at


__all__ = [
    "ActivationAuditEvent",
    "ActivationError",
    "ActivationService",
    "PostCommitAuditError",
    "RollbackCandidate",
    "RollbackReadinessError",
    "TransitionResult",
]
