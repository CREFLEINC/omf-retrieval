"""Compose snapshot acquisition and indexing inside caller-owned transactions."""

from __future__ import annotations

import re
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Protocol
from uuid import UUID

from omf_retrieval.application.indexing.ports import (
    SourceSnapshot,
    SourceSnapshotProvider,
)
from omf_retrieval.application.indexing.service import IndexResult
from omf_retrieval.domain.enums import IndexRunStatus


class IndexingAlreadyInProgressError(RuntimeError):
    """Raised when another transaction owns the source indexing lock."""


class _Transaction(Protocol):
    def begin_nested(self) -> AbstractContextManager[object]:
        """Open a savepoint inside the source-locked outer transaction."""


class _Transactions(Protocol):
    def begin(self) -> AbstractContextManager[_Transaction]:
        """Open one transaction and yield its repository session."""


class _LockingRepository(Protocol):
    def try_acquire_indexing_lock(self) -> bool:
        """Acquire the source-scoped transaction advisory lock if available."""

    def create_building_run(self, commit_sha: str) -> UUID:
        """Create a BUILDING run inside the current transaction."""

    def mark_failed(self, run_id: UUID, result: IndexResult) -> None:
        """Persist one fixed sanitized failure result."""


class _RepositoryFactory(Protocol):
    def __call__(self, transaction: object) -> _LockingRepository:
        """Bind a repository to the yielded transaction session."""


class _IndexService(Protocol):
    def index(self, snapshot: SourceSnapshot, *, run_id: UUID) -> IndexResult:
        """Build artifacts and mark the current run terminal."""


class _ServiceFactory(Protocol):
    def __call__(self, repository: _LockingRepository) -> _IndexService:
        """Bind the application service to one transaction repository."""


class _ReadyPipeline(Protocol):
    def index(self, commit_sha: str) -> IndexResult:
        """Build the requested immutable commit to a terminal run."""


class _Activation(Protocol):
    def activate(self, *, source_key: str, run_id: UUID, actor: str) -> object:
        """Atomically make one READY run active."""


class _ExpectedBuildFailure(Exception):
    def __init__(self, result: IndexResult) -> None:
        self.result = result


class TransactionalIndexPipeline:
    """Hold one source lock across snapshot acquisition and artifact creation."""

    def __init__(
        self,
        *,
        transactions: _Transactions,
        repository_factory: _RepositoryFactory,
        service_factory: _ServiceFactory,
        snapshot_provider: SourceSnapshotProvider,
        source_repo: Path,
    ) -> None:
        """Bind transaction, persistence, source, and processing collaborators."""
        if not isinstance(source_repo, Path):
            raise TypeError("source_repo must be a Path")
        self._transactions = transactions
        self._repository_factory = repository_factory
        self._service_factory = service_factory
        self._snapshot_provider = snapshot_provider
        self._source_repo = source_repo

    def index(self, commit_sha: str) -> IndexResult:
        """Build one commit atomically, retaining only a safe failed run on failure."""
        with self._transactions.begin() as transaction:
            repository = self._repository_factory(transaction)
            self._acquire_lock(repository)
            run_id = repository.create_building_run(commit_sha)
            snapshot = self._snapshot_provider.snapshot(
                self._source_repo,
                commit_sha,
            )
            if snapshot.commit_sha != commit_sha:
                raise ValueError("Source snapshot commit identity changed")
            try:
                with transaction.begin_nested():
                    result = self._service_factory(repository).index(
                        snapshot,
                        run_id=run_id,
                    )
                    if result.run_id != run_id:
                        raise ValueError("Index service result run_id changed")
                    if result.status is IndexRunStatus.FAILED:
                        raise _ExpectedBuildFailure(result)
                    if result.status is not IndexRunStatus.READY:
                        raise ValueError("Index service returned a non-terminal result")
            except _ExpectedBuildFailure as failure:
                repository.mark_failed(run_id, failure.result)
                return failure.result
            except Exception:
                failure = IndexResult(
                    run_id=run_id,
                    status=IndexRunStatus.FAILED,
                    occurrence_count=0,
                    unique_content_count=0,
                    excluded_file_count=snapshot.excluded_file_count,
                    invariant_failure_count=1,
                    failure_code="invariant_failure",
                    failure_detail=(
                        "Index artifacts violated their identity contract."
                    ),
                )
                repository.mark_failed(run_id, failure)
                return failure
            else:
                return result

    @staticmethod
    def _acquire_lock(repository: _LockingRepository) -> None:
        if not repository.try_acquire_indexing_lock():
            raise IndexingAlreadyInProgressError(
                "Indexing is already in progress for this source profile."
            )


class FixedCommitIndexWorkflow:
    """Build and immediately activate one configured immutable source revision."""

    def __init__(
        self,
        *,
        pipeline: _ReadyPipeline,
        activation: _Activation,
        source_key: str,
        commit_sha: str,
        actor: str,
    ) -> None:
        if type(source_key) is not str or not source_key.strip():
            raise ValueError("source_key must be a non-blank exact string")
        if (
            type(commit_sha) is not str
            or re.fullmatch(r"[0-9a-f]{40}", commit_sha) is None
        ):
            raise ValueError("commit_sha must be a lowercase full Git SHA")
        if type(actor) is not str or not actor.strip():
            raise ValueError("actor must be a non-blank exact string")
        self._pipeline = pipeline
        self._activation = activation
        self._source_key = source_key
        self._commit_sha = commit_sha
        self._actor = actor

    def index(self) -> IndexResult:
        """Build only the pinned commit and activate it only after READY commit."""
        result = self._pipeline.index(self._commit_sha)
        if type(result) is not IndexResult:
            raise TypeError("index pipeline must return an exact IndexResult")
        if result.status is IndexRunStatus.FAILED:
            return result
        if result.status is not IndexRunStatus.READY:
            raise ValueError("index pipeline returned a non-terminal result")
        self._activation.activate(
            source_key=self._source_key,
            run_id=result.run_id,
            actor=self._actor,
        )
        return result


__all__ = [
    "FixedCommitIndexWorkflow",
    "IndexingAlreadyInProgressError",
    "TransactionalIndexPipeline",
]
