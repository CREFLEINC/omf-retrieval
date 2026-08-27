"""Unit tests for atomic index activation and rollback orchestration."""

from __future__ import annotations

import ast
import importlib.util
from contextlib import AbstractContextManager
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from omf_retrieval.application.indexing.activation import (
    ActivationAuditEvent,
    ActivationError,
    ActivationService,
    PostCommitAuditError,
    RollbackCandidate,
    RollbackReadinessError,
    TransitionResult,
)
from omf_retrieval.application.indexing.artifact_identity import (
    parse_artifact_manifest,
)
from omf_retrieval.application.indexing.ports import ChunkDraft, ParsedSection
from omf_retrieval.domain.enums import IndexRunStatus
from omf_retrieval.infrastructure.database import (
    repository_activation as activation_repository_module,
)
from omf_retrieval.infrastructure.database.models import (
    DocumentParse,
    IndexRun,
    SourceProfile,
)
from omf_retrieval.infrastructure.database.repository_activation import (
    PostgresActivationRepository,
)
from omf_retrieval.infrastructure.database.repository_config import (
    full_index_config_hash,
)
from omf_retrieval.infrastructure.embedding.provider import EmbeddingConfigSnapshot


def test_activation_module_exists() -> None:
    """The approved activation boundary must be importable."""
    assert (
        importlib.util.find_spec("omf_retrieval.application.indexing.activation")
        is not None
    )


def test_activation_application_has_no_infrastructure_imports() -> None:
    """Application orchestration must depend on ports, not concrete adapters."""
    module_path = (
        Path(__file__).parents[3]
        / "src/omf_retrieval/application/indexing/activation.py"
    )
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert not any(
        module.startswith("omf_retrieval.infrastructure") for module in imported_modules
    )


def test_lifecycle_model_exposes_archived_timestamp_and_unique_slots() -> None:
    """The ORM must mirror the approved two-generation database contract."""
    statuses = {status.value for status in IndexRunStatus}
    activated_at = getattr(IndexRun, "activated_at", None)
    indexes = {index.name: index for index in IndexRun.__table__.indexes}

    assert "archived" in statuses
    assert activated_at is not None
    assert "uq_index_runs_one_active_per_source" in indexes
    assert "uq_index_runs_one_previous_per_source" in indexes
    assert indexes["uq_index_runs_one_active_per_source"].unique is True
    assert indexes["uq_index_runs_one_previous_per_source"].unique is True


def test_document_parse_model_exposes_exact_artifact_manifest_contract() -> None:
    constraints = {
        constraint.name for constraint in DocumentParse.__table__.constraints
    }

    assert DocumentParse.section_count.nullable is False
    assert DocumentParse.chunk_count.nullable is False
    assert DocumentParse.artifact_hash.nullable is False
    assert "ck_document_parses_section_count_positive" in constraints
    assert "ck_document_parses_chunk_count_nonnegative" in constraints
    assert "ck_document_parses_artifact_hash_sha256" in constraints


class _SourceLookupResult:
    def __init__(
        self,
        source: SourceProfile,
        observed_pointer: UUID | None,
    ) -> None:
        self.source = source
        self.observed_pointer = observed_pointer

    def one_or_none(self) -> tuple[UUID, UUID | None]:
        return self.source.id, self.observed_pointer


class _OrderingSession:
    def __init__(
        self,
        *,
        acquired: bool,
        observed_pointer: UUID | None = None,
    ) -> None:
        self.source = SourceProfile(id=uuid4(), source_key="omf")
        self.acquired = acquired
        self.observed_pointer = observed_pointer
        self.calls: list[str] = []

    def execute(self, statement: object) -> _SourceLookupResult:
        self.calls.append("lookup")
        return _SourceLookupResult(self.source, self.observed_pointer)

    def scalar(self, statement: object) -> object:
        sql = str(statement)
        if "pg_try_advisory_xact_lock" in sql:
            self.calls.append("advisory")
            return self.acquired
        if "FOR UPDATE" in sql:
            self.calls.append("row")
            return self.source
        raise AssertionError(sql)

    def delete(self, value: object) -> None:
        raise AssertionError("not used")

    def flush(self) -> None:
        raise AssertionError("not used")

    def get(self, model: object, identity: object) -> None:
        raise AssertionError("not used")

    def scalars(self, statement: object) -> tuple[object, ...]:
        raise AssertionError("not used")


def test_source_lock_order_is_lookup_then_advisory_then_row_lock() -> None:
    session = _OrderingSession(acquired=True)
    repository = PostgresActivationRepository(session)  # type: ignore[arg-type]

    assert repository._lock_source("omf") is session.source
    assert session.calls == ["lookup", "advisory", "row"]


def test_advisory_loser_never_waits_for_the_source_row() -> None:
    session = _OrderingSession(acquired=False)
    repository = PostgresActivationRepository(session)  # type: ignore[arg-type]

    with pytest.raises(ActivationError, match="in progress"):
        repository._lock_source("omf")

    assert session.calls == ["lookup", "advisory"]


def test_source_identity_is_revalidated_after_advisory_and_row_locks() -> None:
    session = _OrderingSession(acquired=True, observed_pointer=uuid4())
    repository = PostgresActivationRepository(session)  # type: ignore[arg-type]

    with pytest.raises(ActivationError, match="pointer changed"):
        repository._lock_source("omf")

    assert session.calls == ["lookup", "advisory", "row"]


class _MigrationOperations:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def execute(self, statement: object) -> None:
        self.calls.append(("execute", statement))

    def get_bind(self) -> object:
        class EmptyResult:
            @staticmethod
            def scalars() -> tuple[object, ...]:
                return ()

            @staticmethod
            def mappings() -> tuple[object, ...]:
                return ()

        class EmptyConnection:
            @staticmethod
            def execute(
                statement: object,
                parameters: object = None,
            ) -> EmptyResult:
                return EmptyResult()

        return EmptyConnection()

    def __getattr__(self, name: str) -> Any:
        def record(*args: object, **kwargs: object) -> None:
            self.calls.append((name, (args, kwargs)))

        return record


def test_activation_migration_preflights_every_legacy_lifecycle_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration_path = (
        Path(__file__).parents[3]
        / "migrations/versions/0002_index_run_activation_lifecycle.py"
    )
    spec = importlib.util.spec_from_file_location("task9_migration", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    operations = _MigrationOperations()
    monkeypatch.setattr(migration, "op", operations)

    migration.upgrade()

    assert operations.calls[0][0] == "execute"
    preflight = str(operations.calls[0][1]).lower()
    assert "invalid legacy index lifecycle" in preflight
    assert "active_count > 1" in preflight
    assert "previous_count > 1" in preflight
    assert "active_index_run_id is null and active_count" in preflight
    assert "active_index_run_id is not null and pointed_status <> 'active'" in preflight
    assert "active_index_run_id is null and previous_count" in preflight
    assert "active_index_run_id <> active_run_id" in preflight
    assert "pointed_source_id <> source_id" in preflight
    assert operations.calls[1][0] == "execute"
    parse_preflight = str(operations.calls[1][1]).lower()
    assert "parse without sections" in parse_preflight
    first_add_column = next(
        index for index, call in enumerate(operations.calls) if call[0] == "add_column"
    )
    assert first_add_column > 1


def test_artifact_stats_use_exact_python_unicode_whitespace_semantics() -> None:
    """Rollback classifies empty source exactly as Task 8's ``source.strip()``."""
    classify = getattr(activation_repository_module, "_classify_contents", None)
    first, second = uuid4(), uuid4()

    assert classify is not None
    result = classify(
        (
            (first, "\t\u2003\n"),
            (first, "\t\u2003\n"),
            (second, "nonempty"),
        )
    )
    assert result.occurrence_count == 3
    assert result.unique_content_count == 2
    assert result.empty_document_count == 2
    assert result.nonempty_content_ids == frozenset({second})


def _manifest_query_rows(
    body: str,
) -> tuple[tuple[tuple[object, ...], ...], tuple[tuple[object, ...], ...], UUID]:
    content_id, parse_id, section_id = uuid4(), uuid4(), uuid4()
    section = ParsedSection(0, None, 1, "H", ("H",), body, 1, 1, ())
    chunks = (
        ()
        if not body.strip()
        else (ChunkDraft(0, body, f"H\n{body}", 1, 1, 1, "a" * 64),)
    )
    manifest = parse_artifact_manifest(
        (section,),
        chunks,
        () if not chunks else (0,),
    )
    section_rows = (
        (
            content_id,
            parse_id,
            manifest.section_count,
            manifest.chunk_count,
            manifest.artifact_hash,
            section_id,
            None,
            0,
            None,
            1,
            "H",
            ["H"],
            body,
            1,
            1,
        ),
    )
    chunk_rows = (
        ()
        if not chunks
        else (
            (
                content_id,
                0,
                uuid4(),
                0,
                body,
                f"H\n{body}",
                1,
                1,
                1,
                "a" * 64,
                1,
            ),
        )
    )
    return section_rows, chunk_rows, content_id


@pytest.mark.parametrize("body", ["", " \t\n", "<!-- only -->\n", "body\n"])
def test_manifest_artifact_contract_matches_exact_chunker_zero_cases(
    body: str,
) -> None:
    """Heading/blank bodies are zero-chunk; comments and prose are searchable."""
    validate = getattr(activation_repository_module, "_validate_manifest_rows", None)
    section_rows, chunk_rows, content_id = _manifest_query_rows(body)

    assert validate is not None
    validate(section_rows, chunk_rows, frozenset({content_id}))
    validate((), (), frozenset())


@pytest.mark.parametrize("tamper", ["missing-chunk", "embedding", "section-body"])
def test_manifest_artifact_contract_rejects_incomplete_or_mutated_rows(
    tamper: str,
) -> None:
    validate = getattr(activation_repository_module, "_validate_manifest_rows", None)
    section_rows, chunk_rows, content_id = _manifest_query_rows("body\n")
    if tamper == "missing-chunk":
        chunk_rows = ()
    elif tamper == "embedding":
        chunk_rows = (chunk_rows[0][:-1] + (0,),)
    else:
        section_rows = (section_rows[0][:-3] + ("changed\n", 1, 1),)

    assert validate is not None
    with pytest.raises(ActivationError, match="artifacts are incomplete"):
        validate(section_rows, chunk_rows, frozenset({content_id}))


def test_manifest_artifact_contract_rejects_parse_for_empty_document() -> None:
    validate = getattr(activation_repository_module, "_validate_manifest_rows", None)
    section_rows, chunk_rows, _ = _manifest_query_rows(" \t\n")

    assert validate is not None
    with pytest.raises(ActivationError, match="artifacts are incomplete"):
        validate(section_rows, chunk_rows, frozenset())


class _LifecycleSession:
    def scalars(self, statement: object) -> tuple[object, ...]:
        return ()

    def flush(self) -> None:
        return None


class _PreviousOnlyActivationRepository(PostgresActivationRepository):
    def __init__(self) -> None:
        self._session = _LifecycleSession()  # type: ignore[assignment]
        self._prepared_candidate = None
        self.source = SourceProfile(id=uuid4(), source_key="omf")
        self.target = IndexRun(
            id=uuid4(),
            source_profile_id=self.source.id,
            index_config_id=uuid4(),
            commit_sha="a" * 40,
            status=IndexRunStatus.READY.value,
        )
        self.previous = IndexRun(
            id=uuid4(),
            source_profile_id=self.source.id,
            index_config_id=uuid4(),
            commit_sha="b" * 40,
            status=IndexRunStatus.PREVIOUS.value,
            activated_at=NOW,
        )

    def _lock_source(self, source_key: object) -> SourceProfile:
        return self.source

    def _locked_run(self, run_id: UUID) -> IndexRun | None:
        return self.target

    def _unique_status_run(
        self,
        source_id: UUID,
        status: IndexRunStatus,
    ) -> IndexRun | None:
        return self.previous if status is IndexRunStatus.PREVIOUS else None

    def _config_hash(self, config_id: UUID) -> str:
        return "c" * 64

    def _prune_archived_run(
        self,
        run_id: UUID,
        candidate_content_ids: tuple[UUID, ...],
    ) -> None:
        return None


def test_activation_rejects_previous_without_active_before_mutation() -> None:
    repository = _PreviousOnlyActivationRepository()

    with pytest.raises(ActivationError, match="inconsistent"):
        repository.activate("omf", repository.target.id, NOW)

    assert repository.previous.status == IndexRunStatus.PREVIOUS.value
    assert repository.target.status == IndexRunStatus.READY.value


NOW = datetime(2026, 8, 24, 3, 4, 5, tzinfo=UTC)
SOURCE_KEY = "omf"


def _snapshot(**changes: object) -> EmbeddingConfigSnapshot:
    values: dict[str, object] = {
        "provider": "sentence-transformers",
        "model_name": "test/model",
        "revision": "revision-1",
        "dimension": 3,
        "normalize_embeddings": True,
        "library_name": "sentence-transformers",
        "library_version": "5.7.0",
        "query_instruction": "Instruct: {query}",
    }
    values.update(changes)
    return EmbeddingConfigSnapshot(**values)  # type: ignore[arg-type]


def _candidate(snapshot: EmbeddingConfigSnapshot | None = None) -> RollbackCandidate:
    provider_snapshot = snapshot or _snapshot()
    parser = {"version": "parser-v1"}
    chunk = {"target_tokens": 400}
    tokenizer = {"revision": "revision-1"}
    embedding = provider_snapshot.as_config()
    rrf = {"k": 60}
    return RollbackCandidate(
        source_key=SOURCE_KEY,
        active_run_id=uuid4(),
        active_config_hash="a" * 64,
        target_run_id=uuid4(),
        target_config_hash=full_index_config_hash(
            parser_config=parser,
            chunk_config=chunk,
            tokenizer_config=tokenizer,
            embedding_config=embedding,
            rrf_config=rrf,
        ),
        parser_config=parser,
        chunk_config=chunk,
        tokenizer_config=tokenizer,
        embedding_config=embedding,
        rrf_config=rrf,
    )


class _Transaction(AbstractContextManager[dict[str, object]]):
    def __init__(self, owner: "_Transactions") -> None:
        self.owner = owner
        self.working = deepcopy(owner.state)

    def __enter__(self) -> dict[str, object]:
        self.owner.begin_calls += 1
        return self.working

    def __exit__(self, error_type: object, error: object, traceback: object) -> None:
        if error_type is None:
            self.owner.state = self.working
            self.owner.commit_calls += 1
        else:
            self.owner.rollback_calls += 1
        return None


class _Transactions:
    def __init__(self) -> None:
        self.state: dict[str, object] = {"transitions": 0}
        self.begin_calls = 0
        self.commit_calls = 0
        self.rollback_calls = 0

    def begin(self) -> _Transaction:
        return _Transaction(self)


class _Repository:
    def __init__(
        self,
        transaction: dict[str, object],
        candidate: RollbackCandidate,
    ) -> None:
        self.transaction = transaction
        self.candidate = candidate
        self.activate_error: Exception | None = None
        self.rollback_error: Exception | None = None
        self.rollback_calls = 0

    def activate(
        self, source_key: str, run_id: UUID, occurred_at: datetime
    ) -> TransitionResult:
        self.transaction["transitions"] = int(self.transaction["transitions"]) + 1
        if self.activate_error is not None:
            raise self.activate_error
        return TransitionResult(
            action="activate",
            occurred_at=occurred_at,
            source_key=source_key,
            from_run_id=None,
            to_run_id=run_id,
            from_config_hash=None,
            to_config_hash="b" * 64,
        )

    def prepare_rollback(self, source_key: str) -> RollbackCandidate:
        assert source_key == self.candidate.source_key
        return self.candidate

    def rollback(
        self, candidate: RollbackCandidate, occurred_at: datetime
    ) -> TransitionResult:
        self.rollback_calls += 1
        self.transaction["transitions"] = int(self.transaction["transitions"]) + 1
        if self.rollback_error is not None:
            raise self.rollback_error
        return TransitionResult(
            action="rollback",
            occurred_at=occurred_at,
            source_key=candidate.source_key,
            from_run_id=candidate.active_run_id,
            to_run_id=candidate.target_run_id,
            from_config_hash=candidate.active_config_hash,
            to_config_hash=candidate.target_config_hash,
        )


class _Provider:
    def __init__(
        self,
        snapshot: EmbeddingConfigSnapshot | None = None,
        *,
        ready: object = True,
    ) -> None:
        self.embedding_config_snapshot = snapshot or _snapshot()
        self.descriptor = self.embedding_config_snapshot.descriptor
        self.ready = ready
        self.readiness_calls = 0

    def is_ready(self) -> bool:
        self.readiness_calls += 1
        if isinstance(self.ready, BaseException):
            raise self.ready
        return self.ready  # type: ignore[return-value]

    def embed_query(self, query: str) -> tuple[float, ...]:
        raise AssertionError("not used by lifecycle transitions")

    def embed_documents(self, documents: object) -> tuple[tuple[float, ...], ...]:
        raise AssertionError("not used by lifecycle transitions")


class _Logger:
    def __init__(self, transactions: _Transactions, *, failure: bool = False) -> None:
        self.transactions = transactions
        self.failure = failure
        self.events: list[ActivationAuditEvent] = []
        self.commit_counts_at_write: list[int] = []

    def write(self, event: ActivationAuditEvent) -> None:
        self.commit_counts_at_write.append(self.transactions.commit_calls)
        if self.failure:
            raise RuntimeError("secret token /host/path")
        self.events.append(event)


def _service(
    *,
    candidate: RollbackCandidate | None = None,
    provider: _Provider | None = None,
    logger_failure: bool = False,
    clock: Any = lambda: NOW,
) -> tuple[ActivationService, _Transactions, _Repository, _Logger]:
    transactions = _Transactions()
    repository = _Repository(transactions.state, candidate or _candidate())

    def repository_factory(transaction: object) -> _Repository:
        repository.transaction = transaction  # type: ignore[assignment]
        return repository

    logger = _Logger(transactions, failure=logger_failure)
    return (
        ActivationService(
            transactions=transactions,
            repository_factory=repository_factory,
            embedding_provider=provider or _Provider(),
            audit_logger=logger,
            clock=clock,
        ),
        transactions,
        repository,
        logger,
    )


def test_activation_commits_before_writing_allowlisted_audit_event() -> None:
    service, transactions, _, logger = _service()
    target = uuid4()

    result = service.activate(source_key=SOURCE_KEY, run_id=target, actor="admin-1")

    assert result == TransitionResult(
        action="activate",
        occurred_at=NOW,
        source_key=SOURCE_KEY,
        from_run_id=None,
        to_run_id=target,
        from_config_hash=None,
        to_config_hash="b" * 64,
    )
    assert transactions.state["transitions"] == 1
    assert logger.commit_counts_at_write == [1]
    assert logger.events == [
        ActivationAuditEvent(
            actor="admin-1",
            action=result.action,
            occurred_at=result.occurred_at,
            source_key=result.source_key,
            from_run_id=result.from_run_id,
            to_run_id=result.to_run_id,
            from_config_hash=result.from_config_hash,
            to_config_hash=result.to_config_hash,
        )
    ]


def test_rollback_checks_exact_config_and_readiness_before_mutation() -> None:
    provider = _Provider()
    candidate = _candidate(provider.embedding_config_snapshot)
    service, transactions, repository, logger = _service(
        candidate=candidate,
        provider=provider,
    )

    result = service.rollback(source_key=SOURCE_KEY, actor="admin@example.com")

    assert result.action == "rollback"
    assert provider.readiness_calls == 1
    assert repository.rollback_calls == 1
    assert transactions.state["transitions"] == 1
    assert logger.commit_counts_at_write == [1]


@pytest.mark.parametrize(
    "provider",
    [
        _Provider(_snapshot(library_version="5.7.1")),
        _Provider(_snapshot(query_instruction="Changed: {query}")),
        _Provider(ready=False),
        _Provider(ready=1),
        _Provider(ready=RuntimeError("secret cache path")),
    ],
)
def test_rollback_readiness_failure_has_zero_mutation(provider: _Provider) -> None:
    service, transactions, repository, logger = _service(provider=provider)

    with pytest.raises(RollbackReadinessError):
        service.rollback(source_key=SOURCE_KEY, actor="admin")

    assert repository.rollback_calls == 0
    assert transactions.state["transitions"] == 0
    assert logger.events == []


def test_noncanonical_target_config_fails_before_mutation() -> None:
    candidate = replace(_candidate(), target_config_hash="f" * 64)
    service, transactions, repository, _ = _service(candidate=candidate)

    with pytest.raises(RollbackReadinessError):
        service.rollback(source_key=SOURCE_KEY, actor="admin")

    assert repository.rollback_calls == 0
    assert transactions.state["transitions"] == 0


@pytest.mark.parametrize("actor", ["", "  ", "admin/path", "한글", "a" * 129])
def test_actor_is_a_bounded_safe_identifier(actor: str) -> None:
    service, transactions, _, _ = _service()

    with pytest.raises(ActivationError):
        service.activate(source_key=SOURCE_KEY, run_id=uuid4(), actor=actor)

    assert transactions.begin_calls == 0


def test_source_key_cannot_put_a_path_in_the_audit_payload() -> None:
    service, transactions, _, _ = _service()

    with pytest.raises(ActivationError):
        service.activate(source_key="../host/path", run_id=uuid4(), actor="admin")

    assert transactions.begin_calls == 0


def test_naive_or_non_utc_clock_fails_before_transaction() -> None:
    for value in (
        datetime(2026, 8, 24, 3, 4, 5),
        datetime.fromisoformat("2026-08-24T12:04:05+09:00"),
    ):
        service, transactions, _, _ = _service(clock=lambda value=value: value)

        with pytest.raises(ActivationError):
            service.activate(source_key=SOURCE_KEY, run_id=uuid4(), actor="admin")

        assert transactions.begin_calls == 0


def test_database_failure_rolls_back_and_does_not_log() -> None:
    service, transactions, repository, logger = _service()
    repository.activate_error = ActivationError("transition failed")

    with pytest.raises(ActivationError, match="transition failed"):
        service.activate(source_key=SOURCE_KEY, run_id=uuid4(), actor="admin")

    assert transactions.state["transitions"] == 0
    assert transactions.rollback_calls == 1
    assert logger.events == []


def test_post_commit_logger_failure_exposes_only_safe_committed_result() -> None:
    service, transactions, _, _ = _service(logger_failure=True)
    target = uuid4()

    with pytest.raises(PostCommitAuditError) as captured:
        service.activate(source_key=SOURCE_KEY, run_id=target, actor="admin")

    assert captured.value.committed is True
    assert captured.value.result.to_run_id == target
    assert transactions.state["transitions"] == 1
    assert "secret" not in str(captured.value)
    assert "/host/path" not in str(captured.value)
