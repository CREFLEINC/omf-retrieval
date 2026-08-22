"""Embedding-model lifecycle commands."""

import json

import typer

from omf_retrieval.application.indexing.hashing import canonical_json
from omf_retrieval.infrastructure.embedding.prepare import prepare_embedding_model
from omf_retrieval.settings import Settings

model_app = typer.Typer(help="Prepare the fixed embedding model cache.")


@model_app.command("prepare")
def prepare() -> None:
    """Download and publish the pinned embedding model manifest."""
    try:
        output = prepare_embedding_model(Settings())
        if type(output) is not bytes:
            raise ValueError("Invalid model preparation output")
        parsed = json.loads(
            output.decode("utf-8"),
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError("Invalid model preparation output")
            ),
        )
        if canonical_json(parsed) != output:
            raise ValueError("Invalid model preparation output")
        rendered = output.decode("utf-8")
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        typer.echo("Embedding model preparation failed", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(rendered)
