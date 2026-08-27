"""MVP CLI public inventory and HTTP search contract."""

import inspect
import json
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from typer.testing import CliRunner

from omf_retrieval.interfaces.cli import indexing as indexing_cli
from omf_retrieval.interfaces.cli import search as search_cli
from omf_retrieval.interfaces.cli.main import app


def test_public_command_inventory_is_exactly_the_mvp_surface() -> None:
    assert {command.name for command in app.registered_commands} == {
        "index",
        "serve",
        "search",
    }
    assert {group.name for group in app.registered_groups} == {"model", "client"}
    client_group = next(
        group for group in app.registered_groups if group.name == "client"
    )
    assert {
        command.name for command in client_group.typer_instance.registered_commands
    } == {"create"}


def test_search_reads_token_from_environment_and_calls_http_surface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[httpx.Request] = []

    def send(request: httpx.Request, **_kwargs: object) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            request=request,
            json={
                "request_id": "r",
                "status": "no_evidence",
                "index": {
                    "run_id": "00000000-0000-0000-0000-000000000001",
                    "commit_sha": "a" * 40,
                },
                "evidence_items": [],
            },
        )

    monkeypatch.setenv("OMF_RETRIEVAL_ENVIRONMENT", "test")
    monkeypatch.setenv("OMF_RETRIEVAL_API_TOKEN", "environment-secret")
    monkeypatch.setattr(search_cli, "send_search_request", send)

    result = CliRunner().invoke(app, ["search", "정책", "--limit", "1"])

    assert result.exit_code == 0
    assert json.loads(result.stdout)["status"] == "no_evidence"
    assert len(seen) == 1
    assert seen[0].headers["Authorization"] == "Bearer environment-secret"
    assert json.loads(seen[0].content) == {"query": "정책", "limit": 1}
    assert "environment-secret" not in result.stdout


def test_search_has_no_token_argument_and_missing_env_fails_safely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OMF_RETRIEVAL_API_TOKEN", raising=False)
    result = CliRunner().invoke(app, ["search", "질문", "--token", "forbidden"])
    assert result.exit_code == 2
    assert "forbidden" not in result.stdout
    missing = CliRunner().invoke(app, ["search", "질문"])
    assert missing.exit_code == 3
    assert missing.stderr == "Authentication failed\n"


@pytest.mark.parametrize("source_repo", [None, "", " \t "])
def test_index_requires_nonblank_source_repo_before_runtime_initialization(
    monkeypatch: pytest.MonkeyPatch,
    source_repo: str | None,
) -> None:
    settings_calls: list[None] = []

    def settings() -> object:
        settings_calls.append(None)
        raise AssertionError("Settings must not be initialized")

    if source_repo is None:
        monkeypatch.delenv("OMF_RETRIEVAL_SOURCE_REPO", raising=False)
    else:
        monkeypatch.setenv("OMF_RETRIEVAL_SOURCE_REPO", source_repo)
    monkeypatch.setattr(indexing_cli, "Settings", settings)

    result = CliRunner().invoke(app, ["index"])

    assert result.exit_code == 4
    assert result.stdout == ""
    assert result.stderr == "Indexing failed\n"
    assert settings_calls == []


def test_index_forwards_configured_source_repo_to_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    @dataclass(frozen=True)
    class TokenizerIdentity:
        model_name: str = "tokenizer"
        revision: str = "revision"

    class Engine:
        def __init__(self) -> None:
            self.disposed = False

        def dispose(self) -> None:
            self.disposed = True

    class Transactions:
        def begin(self) -> object:
            return nullcontext(object())

    class Configurations:
        def __init__(self, _session: object) -> None:
            pass

        def ensure(self, **_kwargs: object) -> object:
            return SimpleNamespace(
                source_profile_id="source",
                index_config_id="config",
                embedding_config_hash="embedding-hash",
            )

    class Embeddings:
        descriptor = SimpleNamespace(dimension=1024)
        embedding_config_snapshot = SimpleNamespace(
            as_config=lambda: {
                "document": {
                    "provider": "provider",
                    "normalize_embeddings": True,
                    "library_name": "library",
                    "library_version": "version",
                }
            }
        )

    class PipelineObserved(Exception):
        pass

    captured: dict[str, object] = {}
    engine = Engine()
    source_repo = tmp_path / "source repository"
    monkeypatch.setenv("OMF_RETRIEVAL_SOURCE_REPO", str(source_repo))
    monkeypatch.setattr(
        indexing_cli,
        "Settings",
        lambda: SimpleNamespace(parent_context_max_tokens=1200),
    )
    monkeypatch.setattr(
        indexing_cli,
        "omf_profile",
        lambda: SimpleNamespace(commit_sha="a" * 40),
    )
    monkeypatch.setattr(indexing_cli, "database_url_from_environment", lambda: "db")
    monkeypatch.setattr(indexing_cli, "create_database_engine", lambda _url: engine)
    monkeypatch.setattr(
        indexing_cli,
        "create_session_factory",
        lambda _engine: Transactions(),
    )
    monkeypatch.setattr(
        indexing_cli,
        "SentenceTransformerEmbeddingProvider",
        lambda _settings: Embeddings(),
    )
    monkeypatch.setattr(
        indexing_cli,
        "SentenceTransformerTokenCounter",
        lambda _settings: SimpleNamespace(descriptor=TokenizerIdentity()),
    )
    monkeypatch.setattr(
        indexing_cli,
        "chunk_config_identity_hash",
        lambda _config, _descriptor: "chunk-hash",
    )
    monkeypatch.setattr(indexing_cli, "ParentChildChunker", lambda *_args: object())
    monkeypatch.setattr(indexing_cli, "retrieval_config_snapshot", lambda _settings: {})
    monkeypatch.setattr(
        indexing_cli, "PostgresIndexConfigurationRepository", Configurations
    )
    monkeypatch.setattr(
        indexing_cli, "GitArchiveSnapshotProvider", lambda _profile: object()
    )

    def observe_pipeline(**kwargs: object) -> object:
        captured.update(kwargs)
        raise PipelineObserved

    monkeypatch.setattr(indexing_cli, "TransactionalIndexPipeline", observe_pipeline)

    with pytest.raises(PipelineObserved):
        indexing_cli.run_fixed_index()

    assert captured["source_repo"] == source_repo
    assert isinstance(captured["source_repo"], Path)
    assert engine.disposed is True


def test_indexing_source_has_no_host_specific_repository_default() -> None:
    source = inspect.getsource(indexing_cli)

    assert "_DEFAULT_" + "OMF_REPOSITORY" not in source
    assert "/Users/" + "rangkim" not in source
