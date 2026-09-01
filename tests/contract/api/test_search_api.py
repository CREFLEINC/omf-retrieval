"""FastAPI MVP search and health surface contract."""

from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from omf_retrieval.application.admin.tokens import (
    AuthenticatedClient,
    AuthenticationError,
    AuthorizedSource,
    SourceAccessError,
)
from omf_retrieval.application.search import (
    ActiveIndex,
    EvidenceItem,
    EvidenceMatch,
    NoActiveIndexError,
    Origin,
    SearchPolicyManifest,
    SearchResult,
    SearchUnavailableError,
)
from omf_retrieval.interfaces.api.app import create_app
from omf_retrieval.settings import Settings

AUTHORIZED = AuthorizedSource(
    AuthenticatedClient(UUID(int=1), "client", "0123456789abcdef"), "omf"
)
POLICY_SNAPSHOT = Settings(environment="test").search_policy_snapshot()
POLICY = SearchPolicyManifest(UUID(int=4), POLICY_SNAPSHOT.config_hash, POLICY_SNAPSHOT)
RESULT = SearchResult(
    status="ok",
    index=ActiveIndex(UUID(int=2), "a" * 40),
    search_policy=POLICY,
    evidence_items=(
        EvidenceItem(
            rank=1,
            parent_id=UUID(int=3),
            heading_path=("확정", "정책"),
            score=0.0325,
            matches=(EvidenceMatch("원문", 10, 18, 2, 1, 0.0325),),
            origins=(Origin("design/wiki/policy.md", "b" * 64),),
        ),
    ),
)


class FakeAccess:
    def __init__(self, failure: Exception | None = None) -> None:
        self.failure = failure
        self.tokens: list[str] = []

    def execute_authorized(self, token: str, source: str, operation: object) -> object:
        self.tokens.append(token)
        assert source == "omf"
        if self.failure is not None:
            raise self.failure
        return operation(AUTHORIZED)  # type: ignore[operator]


class FakeSearch:
    def __init__(
        self, result: SearchResult | Exception = RESULT, ready: bool = True
    ) -> None:
        self.result = result
        self.ready = ready
        self.calls: list[tuple[str, int]] = []
        self.relevance_levels: list[str] = []

    def search(
        self,
        authorized: AuthorizedSource,
        query: str,
        limit: int,
        relevance_level: str = "default",
    ) -> SearchResult:
        assert authorized == AUTHORIZED
        self.calls.append((query, limit))
        self.relevance_levels.append(relevance_level)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def is_ready(self, authorized: AuthorizedSource) -> bool:
        assert authorized == AUTHORIZED
        return self.ready


def _client(
    access: FakeAccess | None = None, search: FakeSearch | None = None
) -> TestClient:
    return TestClient(
        create_app(
            access_service=access or FakeAccess(), search_service=search or FakeSearch()
        )
    )


def _auth(token: str = "omfr_token") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_search_success_has_complete_evidence_contract_and_default_limit() -> None:
    search = FakeSearch()
    response = _client(search=search).post(
        "/v1/search", headers=_auth(), json={"query": " 정책 "}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["index"] == {"run_id": str(UUID(int=2)), "commit_sha": "a" * 40}
    assert body["search_policy"] == {
        "policy_id": str(UUID(int=4)),
        "config_hash": POLICY.config_hash,
    }
    assert body["evidence_items"][0]["matches"][0]["keyword_rank"] == 2
    assert body["evidence_items"][0]["origins"] == [
        {"source_path": "design/wiki/policy.md", "content_hash": "b" * 64}
    ]
    assert set(body) == {
        "request_id",
        "status",
        "index",
        "search_policy",
        "evidence_items",
    }
    assert search.calls == [("정책", 5)]
    assert search.relevance_levels == ["default"]


def test_search_accepts_strict_relevance_level() -> None:
    search = FakeSearch()

    response = _client(search=search).post(
        "/v1/search",
        headers=_auth(),
        json={"query": "질문", "relevance_level": "strict"},
    )

    assert response.status_code == 200
    assert search.calls == [("질문", 5)]
    assert search.relevance_levels == ["strict"]


@pytest.mark.parametrize("limit", [1, 20])
def test_search_accepts_limit_boundaries(limit: int) -> None:
    search = FakeSearch()
    response = _client(search=search).post(
        "/v1/search", headers=_auth(), json={"query": "질문", "limit": limit}
    )
    assert response.status_code == 200
    assert search.calls == [("질문", limit)]


@pytest.mark.parametrize(
    "payload",
    [
        {"query": ""},
        {"query": "   "},
        {"query": "질문", "limit": 0},
        {"query": "질문", "limit": 21},
        {"query": "질문", "relevance_level": "unknown"},
        {"query": "질문", "source": "omf"},
    ],
)
def test_invalid_requests_use_safe_422_body(payload: dict[str, object]) -> None:
    response = _client().post("/v1/search", headers=_auth(), json=payload)
    assert response.status_code == 422
    assert set(response.json()) == {"request_id", "code", "message"}
    assert response.json()["code"] == "invalid_request"
    assert "질문" not in response.text


@pytest.mark.parametrize("header", [None, "Basic x", "Bearer", "Bearer  x"])
def test_missing_or_malformed_bearer_is_401(header: str | None) -> None:
    headers = {} if header is None else {"Authorization": header}
    response = _client().post("/v1/search", headers=headers, json={"query": "q"})
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"


@pytest.mark.parametrize(
    ("failure", "status", "code"),
    [
        (AuthenticationError(), 401, "invalid_token"),
        (SourceAccessError(), 403, "source_access_denied"),
        (NoActiveIndexError(), 409, "no_active_index"),
        (SearchUnavailableError(), 503, "service_unavailable"),
        (RuntimeError("postgres://secret-host/private"), 503, "service_unavailable"),
    ],
)
def test_failures_have_stable_safe_error_bodies(
    failure: Exception, status: int, code: str
) -> None:
    access = FakeAccess(failure) if status in {401, 403} else FakeAccess()
    search = FakeSearch(failure) if status not in {401, 403} else FakeSearch()
    response = _client(access, search).post(
        "/v1/search", headers=_auth("secret-token"), json={"query": "secret-query"}
    )
    assert response.status_code == status
    assert set(response.json()) == {"request_id", "code", "message"}
    assert response.json()["code"] == code
    assert all(
        value not in response.text
        for value in ("secret-token", "secret-query", "secret-host", "private")
    )


def test_no_evidence_is_http_200_with_empty_items() -> None:
    result = SearchResult("no_evidence", RESULT.index, POLICY, ())
    response = _client(search=FakeSearch(result)).post(
        "/v1/search", headers=_auth(), json={"query": "없는 질문"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "no_evidence"
    assert response.json()["search_policy"]["config_hash"] == POLICY.config_hash
    assert response.json()["evidence_items"] == []


def test_live_is_public_ready_is_authenticated_and_dependency_aware() -> None:
    client = _client()
    assert client.get("/health/live").json()["status"] == "live"
    assert client.get("/health/ready").status_code == 401
    assert client.get("/health/ready", headers=_auth()).json()["status"] == "ready"
    unavailable = _client(search=FakeSearch(ready=False)).get(
        "/health/ready", headers=_auth()
    )
    assert unavailable.status_code == 503
    assert unavailable.json()["code"] == "service_unavailable"


def test_only_three_mvp_routes_are_public() -> None:
    app = create_app(access_service=FakeAccess(), search_service=FakeSearch())
    assert {
        (route.path, tuple(sorted(route.methods or ()))) for route in app.routes
    } == {
        ("/v1/search", ("POST",)),
        ("/health/live", ("GET",)),
        ("/health/ready", ("GET",)),
    }
