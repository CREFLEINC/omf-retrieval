"""Side-effect bounded startup resolution for immutable search policies."""

from contextlib import contextmanager
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from omf_retrieval.application.admin.tokens import AuthenticatedClient, AuthorizedSource
from omf_retrieval.application.search import SearchPolicyManifest
from omf_retrieval.interfaces.api import runtime
from omf_retrieval.settings import Settings


class Transactions:
    @contextmanager
    def begin(self) -> object:
        yield object()


class Access:
    def execute_authorized(
        self, _token: str, _source: str, operation: object
    ) -> object:
        authorized = AuthorizedSource(
            AuthenticatedClient(UUID(int=9), "agent", "0123456789abcdef"), "omf"
        )
        return operation(authorized)  # type: ignore[operator]


def test_runtime_registers_then_resolves_the_configured_policy_once_per_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = getattr(runtime, "resolve_runtime_search_policy", None)
    assert callable(resolver), "runtime policy resolution is not implemented"
    settings = Settings(environment="test")
    snapshot = settings.search_policy_snapshot()
    manifest = SearchPolicyManifest(UUID(int=1), snapshot.config_hash, snapshot)
    operations: list[tuple[str, object]] = []

    class Policies:
        def __init__(self, _session: object) -> None:
            pass

        def register(self, value: object) -> SearchPolicyManifest:
            operations.append(("register", value))
            return manifest

        def resolve(self, value: object) -> SearchPolicyManifest:
            operations.append(("resolve", value))
            return manifest

    monkeypatch.setattr(runtime, "PostgresSearchPolicyRepository", Policies)

    first = resolver(Transactions(), settings)
    second = resolver(Transactions(), settings)

    assert first == second == manifest
    assert operations == [
        ("register", snapshot),
        ("resolve", snapshot.config_hash),
        ("register", snapshot),
        ("resolve", snapshot.config_hash),
    ]


@pytest.mark.parametrize("failure", [RuntimeError("postgres://private"), None])
def test_runtime_policy_storage_failure_or_manifest_drift_is_safe(
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception | None,
) -> None:
    settings = Settings(environment="test")
    changed = settings.model_copy(update={"vector_similarity_floor": 0.9})
    changed_snapshot = changed.search_policy_snapshot()
    manifest = SearchPolicyManifest(
        UUID(int=2), changed_snapshot.config_hash, changed_snapshot
    )

    class Policies:
        def __init__(self, _session: object) -> None:
            pass

        def register(self, _value: object) -> SearchPolicyManifest:
            if failure is not None:
                raise failure
            return manifest

        def resolve(self, _value: object) -> SearchPolicyManifest:
            return manifest

    monkeypatch.setattr(runtime, "PostgresSearchPolicyRepository", Policies)

    with pytest.raises(RuntimeError, match="^Search policy is unavailable$") as error:
        runtime.resolve_runtime_search_policy(Transactions(), settings)

    assert "private" not in str(error.value)


def test_policy_startup_failure_keeps_live_but_search_and_ready_safe_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = object()
    monkeypatch.setattr(runtime, "Settings", lambda: Settings(environment="test"))
    monkeypatch.setattr(runtime, "database_url_from_environment", lambda: "db")
    monkeypatch.setattr(runtime, "create_database_engine", lambda _url: engine)
    monkeypatch.setattr(
        runtime, "create_session_factory", lambda _engine: Transactions()
    )
    monkeypatch.setattr(runtime, "PostgresClientRepository", lambda _sessions: object())
    monkeypatch.setattr(runtime, "ClientAccessService", lambda _clients: Access())
    monkeypatch.setattr(
        runtime,
        "SentenceTransformerEmbeddingProvider",
        lambda _settings: (_ for _ in ()).throw(AssertionError("model must not build")),
    )
    monkeypatch.setattr(
        runtime,
        "resolve_runtime_search_policy",
        lambda _sessions, _settings: (_ for _ in ()).throw(RuntimeError("private")),
    )

    try:
        application = runtime.build_runtime_app()
    except RuntimeError:
        application = None

    assert application is not None, "policy failure must create an unavailable app"
    client = TestClient(application)
    headers = {"Authorization": "Bearer token"}
    assert client.get("/health/live").status_code == 200
    responses = (
        client.get("/health/ready", headers=headers),
        client.post("/v1/search", headers=headers, json={"query": "질문"}),
    )
    for response in responses:
        assert response.status_code == 503
        assert response.json()["code"] == "service_unavailable"
        assert "private" not in response.text
