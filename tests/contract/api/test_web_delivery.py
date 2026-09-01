"""Same-origin web application delivery contracts."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from omf_retrieval.interfaces.api.app import create_app


def _web_dist(tmp_path: Path) -> Path:
    web_dist = tmp_path / "dist"
    assets = web_dist / "assets"
    assets.mkdir(parents=True)
    (web_dist / "index.html").write_text(
        "<!doctype html><title>OMF 정보 조회</title>", encoding="utf-8"
    )
    (assets / "application.js").write_text(
        "globalThis.omfRetrieval = true;", encoding="utf-8"
    )
    return web_dist


def test_root_returns_injected_web_index(tmp_path: Path) -> None:
    client = TestClient(create_app(web_dist=_web_dist(tmp_path)))

    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "OMF 정보 조회" in response.text


def test_static_asset_returns_file_with_correct_content_type(tmp_path: Path) -> None:
    client = TestClient(create_app(web_dist=_web_dist(tmp_path)))

    response = client.get("/assets/application.js")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/javascript")
    assert response.text == "globalThis.omfRetrieval = true;"


@pytest.mark.parametrize(
    "requested_path",
    [
        "/../outside-static-sentinel.txt",
        "/%2e%2e/outside-static-sentinel.txt",
        "/%252e%252e/outside-static-sentinel.txt",
    ],
)
def test_static_path_traversal_does_not_expose_files_outside_web_dist(
    tmp_path: Path,
    requested_path: str,
) -> None:
    sentinel = "PATH_TRAVERSAL_SENTINEL_DO_NOT_EXPOSE"
    (tmp_path / "outside-static-sentinel.txt").write_text(sentinel, encoding="utf-8")
    client = TestClient(create_app(web_dist=_web_dist(tmp_path)))

    response = client.get(requested_path)

    assert response.status_code == 200
    assert "OMF 정보 조회" in response.text
    assert sentinel not in response.text


def test_api_and_health_paths_are_not_hidden_by_web_fallback(tmp_path: Path) -> None:
    client = TestClient(create_app(web_dist=_web_dist(tmp_path)))

    search_response = client.post("/v1/search", json={"query": "질문"})

    assert search_response.status_code == 401
    assert search_response.json()["code"] == "invalid_token"
    assert client.get("/health/live").json()["status"] == "live"
    assert client.get("/health/ready").status_code == 401
    assert client.get("/v1/unknown").status_code == 404
    assert client.get("/health/unknown").status_code == 404


def test_missing_web_dist_keeps_create_app_available(tmp_path: Path) -> None:
    client = TestClient(create_app(web_dist=tmp_path / "missing"))

    response = client.get("/")

    assert response.status_code == 404
