"""Provide the command-line interface application."""

import typer

from omf_retrieval.interfaces.cli.model import model_app

app = typer.Typer(help="OMF retrieval service operations.")
app.add_typer(model_app, name="model")
