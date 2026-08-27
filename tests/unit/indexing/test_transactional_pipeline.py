"""Unit tests for transaction-safe indexing pipeline composition."""

from __future__ import annotations

from contextlib import AbstractContextManager
from importlib import import_module
from importlib.util import find_spec
from pathlib import Path
from types import TracebackType
from uuid import UUID

import pytest

from omf_retrieval.application.indexing.ports import SourceSnapshot
from omf_retrieval.application.indexing.service import IndexResult
from omf_retrieval.domain.enums import IndexRunStatus


def test_transactional_pipeline_module_exists() -> None:
    """Task 8C needs one explicit transaction owner around IndexService."""
    assert find_spec("omf_retrieval.application.indexing.pipeline") is not None


def test_transactional_pipeline_contract_exists() -> None:
    """The composition boundary and stable lock error are public contracts."""
    module = import_module("omf_retrieval.application.indexing.pipeline")

    assert callable(getattr(module, "TransactionalIndexPipeline", None))
    assert callable(getattr(module, "FixedCommitIndexWorkflow", None))
    assert issubclass(
        getattr(module, "IndexingAlreadyInProgressError", None),
        RuntimeError,
    )


class _Transaction(AbstractContextManager[object]):
    def __init__(self, events: list[str], ordinal: int) -> None:
        self.events = events
        self.ordinal = ordinal

    def __enter__(self) -> object:
        self.events.append(f"begin:{self.ordinal}")
        return self

    def begin_nested(self) -> _Savepoint:
        return _Savepoint(self.events, self.ordinal)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        del exc_value, traceback
        terminal = "commit" if exc_type is None else "rollback"
        self.events.append(f"{terminal}:{self.ordinal}")
        return False


class _Savepoint(AbstractContextManager[object]):
    def __init__(self, events: list[str], ordinal: int) -> None:
        self.events = events
        self.ordinal = ordinal

    def __enter__(self) -> object:
        self.events.append(f"savepoint-begin:{self.ordinal}")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        del exc_value, traceback
        terminal = "savepoint-commit" if exc_type is None else "savepoint-rollback"
        self.events.append(f"{terminal}:{self.ordinal}")
        return False


class _Transactions:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.count = 0

    def begin(self) -> _Transaction:
        self.count += 1
        return _Transaction(self.events, self.count)


class _Repository:
    def __init__(self, events: list[str], ordinal: int, *, lock: bool = True) -> None:
        self.events = events
        self.ordinal = ordinal
        self.lock = lock
        self.failed_results: list[IndexResult] = []

    def try_acquire_indexing_lock(self) -> bool:
        self.events.append(f"lock:{self.ordinal}")
        return self.lock

    def create_building_run(self, commit_sha: str) -> UUID:
        self.events.append(f"building:{self.ordinal}:{commit_sha}")
        return UUID(int=self.ordinal)

    def mark_failed(self, run_id: UUID, result: IndexResult) -> None:
        assert result.run_id == run_id
        self.events.append(f"failed:{self.ordinal}:{run_id.int}")
        self.failed_results.append(result)


class _SnapshotProvider:
    def __init__(self, events: list[str], snapshot: SourceSnapshot) -> None:
        self.events = events
        self.value = snapshot

    def snapshot(self, repo: Path, commit_sha: str) -> SourceSnapshot:
        self.events.append(f"snapshot:{repo.name}:{commit_sha}")
        return self.value


class _Service:
    def __init__(
        self,
        events: list[str],
        repository: _Repository,
        result: IndexResult | Exception,
    ) -> None:
        self.events = events
        self.repository = repository
        self.result = result

    def index(self, snapshot: SourceSnapshot, *, run_id: UUID) -> IndexResult:
        self.events.append(
            f"service:{self.repository.ordinal}:{run_id.int}:{snapshot.commit_sha}"
        )
        if isinstance(self.result, Exception):
            raise self.result
        if self.result.status is IndexRunStatus.FAILED:
            self.repository.mark_failed(run_id, self.result)
        return self.result


class _Activation:
    def __init__(
        self,
        events: list[str],
        active_run_id: UUID | None = None,
    ) -> None:
        self.events = events
        self.active_run_id = active_run_id

    def activate(self, *, source_key: str, run_id: UUID, actor: str) -> object:
        self.events.append(f"activate:{source_key}:{run_id.int}:{actor}")
        self.active_run_id = run_id
        return object()


def _snapshot() -> SourceSnapshot:
    return SourceSnapshot("a" * 40, (), 2)


def _result(status: IndexRunStatus) -> IndexResult:
    return IndexResult(
        run_id=UUID(int=1),
        status=status,
        occurrence_count=0,
        unique_content_count=0,
        excluded_file_count=2,
        failure_code=("parse_failure" if status is IndexRunStatus.FAILED else None),
        failure_detail=(
            "A source document could not be parsed."
            if status is IndexRunStatus.FAILED
            else None
        ),
        parse_failure_count=int(status is IndexRunStatus.FAILED),
    )


def _pipeline(
    events: list[str],
    result: IndexResult | Exception,
    *,
    first_lock: bool = True,
) -> tuple[object, list[_Repository]]:
    module = import_module("omf_retrieval.application.indexing.pipeline")
    transactions = _Transactions(events)
    repositories: list[_Repository] = []

    def repository_factory(transaction: object) -> _Repository:
        assert isinstance(transaction, _Transaction)
        repository = _Repository(
            events,
            transaction.ordinal,
            lock=first_lock if transaction.ordinal == 1 else True,
        )
        repositories.append(repository)
        return repository

    return (
        module.TransactionalIndexPipeline(
            transactions=transactions,
            repository_factory=repository_factory,
            service_factory=lambda repository: _Service(
                events,
                repository,
                result,
            ),
            snapshot_provider=_SnapshotProvider(events, _snapshot()),
            source_repo=Path("/source/omf"),
        ),
        repositories,
    )


def test_success_holds_lock_while_snapshot_and_service_commit() -> None:
    """Snapshot acquisition and READY publication share one lock transaction."""
    events: list[str] = []
    expected = _result(IndexRunStatus.READY)
    pipeline, repositories = _pipeline(events, expected)

    actual = pipeline.index("a" * 40)  # type: ignore[attr-defined]

    assert actual == expected
    assert actual.run_id == UUID(int=1)
    assert len(repositories) == 1
    assert events == [
        "begin:1",
        "lock:1",
        f"building:1:{'a' * 40}",
        f"snapshot:omf:{'a' * 40}",
        "savepoint-begin:1",
        f"service:1:1:{'a' * 40}",
        "savepoint-commit:1",
        "commit:1",
    ]


def test_fixed_commit_workflow_activates_the_new_ready_run_after_commit() -> None:
    """The MVP index operation pins its commit and immediately activates READY."""
    events: list[str] = []
    expected = _result(IndexRunStatus.READY)
    pipeline, _repositories = _pipeline(events, expected)
    previous_active_run_id = UUID(int=99)
    activation = _Activation(events, previous_active_run_id)
    module = import_module("omf_retrieval.application.indexing.pipeline")
    workflow = module.FixedCommitIndexWorkflow(
        pipeline=pipeline,
        activation=activation,
        source_key="omf",
        commit_sha="a" * 40,
        actor="index",
    )

    actual = workflow.index()

    assert actual == expected
    assert activation.active_run_id == expected.run_id
    assert events[-2:] == ["commit:1", "activate:omf:1:index"]


def test_fixed_commit_workflow_leaves_active_pointer_unchanged_on_failed_build() -> (
    None
):
    """Parse or embedding failure cannot invoke the active-pointer transition."""
    events: list[str] = []
    expected = _result(IndexRunStatus.FAILED)
    pipeline, _repositories = _pipeline(events, expected)
    previous_active_run_id = UUID(int=99)
    activation = _Activation(events, previous_active_run_id)
    module = import_module("omf_retrieval.application.indexing.pipeline")
    workflow = module.FixedCommitIndexWorkflow(
        pipeline=pipeline,
        activation=activation,
        source_key="omf",
        commit_sha="a" * 40,
        actor="index",
    )

    actual = workflow.index()

    assert actual == expected
    assert activation.active_run_id == previous_active_run_id
    assert not any(event.startswith("activate:") for event in events)


def test_expected_document_failure_rolls_back_then_records_safe_failed_run() -> None:
    """Partial artifacts roll back before a fresh boundary stores safe failure."""
    events: list[str] = []
    expected = _result(IndexRunStatus.FAILED)
    pipeline, repositories = _pipeline(events, expected)

    actual = pipeline.index("a" * 40)  # type: ignore[attr-defined]

    assert actual == expected
    assert actual.run_id == UUID(int=1)
    assert len(repositories) == 1
    assert repositories[0].failed_results == [expected, expected]
    assert events == [
        "begin:1",
        "lock:1",
        f"building:1:{'a' * 40}",
        f"snapshot:omf:{'a' * 40}",
        "savepoint-begin:1",
        f"service:1:1:{'a' * 40}",
        "failed:1:1",
        "savepoint-rollback:1",
        "failed:1:1",
        "commit:1",
    ]


def test_same_source_lock_rejection_happens_before_snapshot_or_run() -> None:
    """A competing build cannot observe or mutate source artifacts."""
    events: list[str] = []
    pipeline, repositories = _pipeline(
        events,
        _result(IndexRunStatus.READY),
        first_lock=False,
    )
    module = import_module("omf_retrieval.application.indexing.pipeline")

    with pytest.raises(module.IndexingAlreadyInProgressError):
        pipeline.index("a" * 40)  # type: ignore[attr-defined]

    assert len(repositories) == 1
    assert events == ["begin:1", "lock:1", "rollback:1"]


def test_unexpected_persistence_exception_records_failed_run_without_artifacts() -> (
    None
):
    """A storage failure keeps a safe FAILED run and no partial artifacts."""
    events: list[str] = []
    pipeline, repositories = _pipeline(events, RuntimeError("database unavailable"))

    actual = pipeline.index("a" * 40)  # type: ignore[attr-defined]

    assert actual.status is IndexRunStatus.FAILED
    assert actual.failure_code == "invariant_failure"
    assert actual.failure_detail == "Index artifacts violated their identity contract."
    assert len(repositories) == 1
    assert repositories[0].failed_results == [actual]
    assert events[-3:] == ["savepoint-rollback:1", "failed:1:1", "commit:1"]
