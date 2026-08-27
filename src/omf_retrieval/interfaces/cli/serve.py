"""Local FastAPI serving command."""

import typer
import uvicorn

from omf_retrieval.interfaces.api.runtime import build_runtime_app


def serve_command(
    host: str = typer.Option("127.0.0.1", help="Listening address."),
    port: int = typer.Option(8000, min=1, max=65535, help="Listening port."),
) -> None:
    """Run the authenticated local MVP service."""
    try:
        application = build_runtime_app()
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        typer.echo("Service startup failed", err=True)
        raise typer.Exit(code=4) from None
    uvicorn.run(application, host=host, port=port, log_config=None)


__all__ = ["serve_command"]
