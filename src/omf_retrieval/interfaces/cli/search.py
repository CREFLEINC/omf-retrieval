"""HTTP-only search CLI that reads its Bearer token from settings."""

import json
import os

import httpx
import typer

from omf_retrieval.settings import Settings


def send_search_request(request: httpx.Request, **kwargs: object) -> httpx.Response:
    """Send one prepared request; a narrow seam keeps contract tests offline."""
    with httpx.Client(timeout=30.0) as client:
        return client.send(request, **kwargs)


def search_command(
    query: str = typer.Argument(..., help="Natural-language evidence query."),
    limit: int = typer.Option(5, min=1, max=20, help="Maximum evidence items."),
) -> None:
    """Call POST /v1/search using only the environment-provided token."""
    settings = Settings()
    token = settings.api_token
    if token is None:
        typer.echo("Authentication failed", err=True)
        raise typer.Exit(code=3)
    base_url = os.environ.get("OMF_RETRIEVAL_API_URL", "http://127.0.0.1:8000")
    request = httpx.Request(
        "POST",
        f"{base_url.rstrip('/')}/v1/search",
        headers={"Authorization": f"Bearer {token.get_secret_value()}"},
        json={"query": query, "limit": limit},
    )
    try:
        response = send_search_request(request)
        payload = response.json()
        if type(payload) is not dict:
            raise ValueError
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        typer.echo("Search service is unavailable", err=True)
        raise typer.Exit(code=4) from None
    if response.status_code != 200:
        typer.echo("Search request failed", err=True)
        raise typer.Exit(code=_exit_code(response.status_code))
    typer.echo(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def _exit_code(status_code: int) -> int:
    if status_code in {401, 403}:
        return 3
    if status_code == 422:
        return 2
    return 4


__all__ = ["search_command", "send_search_request"]
