"""FastAPI composition for the three-endpoint MVP public surface."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.responses import Response

from omf_retrieval.application.admin.tokens import (
    AuthenticationError,
    AuthorizedSource,
    SourceAccessError,
)
from omf_retrieval.application.search import (
    NoActiveIndexError,
    SearchResult,
    SearchUnavailableError,
)
from omf_retrieval.interfaces.api.schemas import SearchRequest, SearchResponse


class _AccessService(Protocol):
    def execute_authorized(
        self,
        token: str,
        source_key: str,
        operation: object,
    ) -> object: ...


class _SearchService(Protocol):
    def search(
        self,
        authorized: AuthorizedSource,
        query: str,
        *,
        limit: int,
        relevance_level: str = "default",
    ) -> SearchResult: ...

    def is_ready(self, authorized: AuthorizedSource) -> bool: ...


def create_app(
    *,
    access_service: _AccessService | None = None,
    search_service: _SearchService | None = None,
    web_dist: Path | None = None,
) -> FastAPI:
    """Create a side-effect-free app whose runtime services are injectable."""
    resolved_web_dist = (web_dist or Path.cwd() / "web" / "dist").resolve()
    application = FastAPI(
        title="OMF Retrieval",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @application.middleware("http")
    async def request_identity(request: Request, call_next: object) -> object:
        request.state.request_id = str(uuid4())
        web_response = _web_response(request, resolved_web_dist)
        if web_response is not None:
            return web_response
        return await call_next(request)  # type: ignore[operator]

    @application.exception_handler(RequestValidationError)
    async def validation_error(
        request: Request, _error: RequestValidationError
    ) -> JSONResponse:
        return _error_response(
            request,
            status_code=422,
            code="invalid_request",
            message="Request validation failed.",
        )

    @application.post("/v1/search", response_model=SearchResponse)
    async def search(request: Request, payload: SearchRequest) -> JSONResponse:
        try:
            token = _bearer_token(request)
            if access_service is None or search_service is None:
                raise SearchUnavailableError
            result = access_service.execute_authorized(
                token,
                "omf",
                lambda authorized: search_service.search(
                    authorized,
                    payload.query,
                    limit=payload.limit,
                    relevance_level=payload.relevance_level,
                ),
            )
            if type(result) is not SearchResult:
                raise SearchUnavailableError
            return JSONResponse(_success_body(request, result))
        except AuthenticationError:
            return _known_error(request, AuthenticationError())
        except SourceAccessError:
            return _known_error(request, SourceAccessError())
        except NoActiveIndexError:
            return _known_error(request, NoActiveIndexError())
        except SearchUnavailableError:
            return _known_error(request, SearchUnavailableError())
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            return _known_error(request, SearchUnavailableError())

    @application.get("/health/live")
    async def live(request: Request) -> dict[str, str]:
        return {"request_id": request.state.request_id, "status": "live"}

    @application.get("/health/ready")
    async def ready(request: Request) -> JSONResponse:
        try:
            token = _bearer_token(request)
            if access_service is None or search_service is None:
                raise SearchUnavailableError
            available = access_service.execute_authorized(
                token,
                "omf",
                lambda authorized: search_service.is_ready(authorized),
            )
            if available is not True:
                raise SearchUnavailableError
            return JSONResponse(
                {"request_id": request.state.request_id, "status": "ready"}
            )
        except AuthenticationError:
            return _known_error(request, AuthenticationError())
        except SourceAccessError:
            return _known_error(request, SourceAccessError())
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            return _known_error(request, SearchUnavailableError())

    return application


def _web_response(request: Request, web_dist: Path) -> Response | None:
    """Return a built web file without intercepting API and health namespaces."""
    if request.method not in {"GET", "HEAD"} or _is_service_path(request.url.path):
        return None

    index_file = web_dist / "index.html"
    if not index_file.is_file():
        return None

    requested_path = request.url.path.lstrip("/")
    if requested_path:
        requested_file = (web_dist / requested_path).resolve()
        if requested_file.is_relative_to(web_dist) and requested_file.is_file():
            return FileResponse(requested_file)
        if requested_path.startswith("assets/"):
            return None

    return FileResponse(index_file)


def _is_service_path(path: str) -> bool:
    return path in {"/v1", "/health"} or path.startswith(("/v1/", "/health/"))


def _bearer_token(request: Request) -> str:
    authorization = request.headers.get("Authorization")
    if type(authorization) is not str:
        raise AuthenticationError
    scheme, separator, token = authorization.partition(" ")
    if (
        separator != " "
        or scheme.lower() != "bearer"
        or not token
        or token.strip() != token
        or any(character.isspace() for character in token)
    ):
        raise AuthenticationError
    return token


def _success_body(request: Request, result: SearchResult) -> dict[str, object]:
    return {
        "request_id": request.state.request_id,
        "status": result.status,
        "index": {
            "run_id": str(result.index.run_id),
            "commit_sha": result.index.commit_sha,
        },
        "search_policy": {
            "policy_id": str(result.search_policy.policy_id),
            "config_hash": result.search_policy.config_hash,
        },
        "evidence_items": [
            {
                "rank": item.rank,
                "heading_path": list(item.heading_path),
                "matches": [
                    {
                        "excerpt": match.excerpt,
                        "line_start": match.line_start,
                        "line_end": match.line_end,
                        "keyword_rank": match.keyword_rank,
                        "vector_rank": match.vector_rank,
                        "rrf_score": match.rrf_score,
                    }
                    for match in item.matches
                ],
                "origins": [
                    {
                        "source_path": origin.source_path,
                        "content_hash": origin.content_hash,
                    }
                    for origin in item.origins
                ],
            }
            for item in result.evidence_items
        ],
    }


def _known_error(request: Request, error: object) -> JSONResponse:
    return _error_response(
        request,
        status_code=error.status_code,  # type: ignore[attr-defined]
        code=error.code,  # type: ignore[attr-defined]
        message=str(error),
    )


def _error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "request_id": request.state.request_id,
            "code": code,
            "message": message,
        },
        headers=({"WWW-Authenticate": "Bearer"} if status_code == 401 else None),
    )


app = create_app()

__all__ = ["app", "create_app"]
