"""Minimal API-client administration CLI."""

import json

import typer

from omf_retrieval.application.admin.service import ClientAdminService
from omf_retrieval.infrastructure.database.repository_auth import (
    PostgresClientRepository,
)
from omf_retrieval.infrastructure.database.session import (
    create_database_engine,
    create_session_factory,
)
from omf_retrieval.interfaces.api.runtime import database_url_from_environment

client_app = typer.Typer(help="Create an authenticated OMF search client.")


def create_client(name: str) -> dict[str, str]:
    """Create one client and return its one-time credential response."""
    engine = create_database_engine(database_url_from_environment())
    try:
        service = ClientAdminService(
            PostgresClientRepository(create_session_factory(engine))
        )
        issued = service.create_client(name, {"omf"})
        return {
            "client_id": str(issued.client_id),
            "key_id": issued.key_id,
            "name": issued.name,
            "token": issued.token,
        }
    finally:
        engine.dispose()


@client_app.command("create")
def create(name: str = typer.Argument(..., help="Non-blank client name.")) -> None:
    """Issue a token once and persist only its digest with the OMF grant."""
    try:
        result = create_client(name)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        typer.echo("Client creation failed", err=True)
        raise typer.Exit(code=4) from None
    typer.echo(json.dumps(result, sort_keys=True, separators=(",", ":")))


__all__ = ["client_app", "create_client"]
