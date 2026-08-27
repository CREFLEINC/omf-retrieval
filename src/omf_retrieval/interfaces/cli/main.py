"""Provide the command-line interface application."""

import typer

from omf_retrieval.interfaces.cli.client import client_app
from omf_retrieval.interfaces.cli.indexing import index_command
from omf_retrieval.interfaces.cli.model import model_app
from omf_retrieval.interfaces.cli.search import search_command
from omf_retrieval.interfaces.cli.serve import serve_command

app = typer.Typer(help="OMF retrieval service operations.")
app.add_typer(model_app, name="model")
app.add_typer(client_app, name="client")
app.command("index")(index_command)
app.command("serve")(serve_command)
app.command("search")(search_command)
