"""Focused tests for Alembic environment URL configuration."""

import ast
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.config import Config

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_ENV_PATH = REPOSITORY_ROOT / "migrations" / "env.py"


def _load_alembic_environment(config: Config) -> list[str]:
    """Execute env.py offline with a controllable Alembic context proxy."""
    syntax_tree = ast.parse(
        ALEMBIC_ENV_PATH.read_text(), filename=str(ALEMBIC_ENV_PATH)
    )
    syntax_tree.body = [
        node
        for node in syntax_tree.body
        if not (isinstance(node, ast.ImportFrom) and node.module == "alembic")
    ]
    configured_urls: list[str] = []
    fake_context = SimpleNamespace(
        config=config,
        is_offline_mode=lambda: True,
        configure=lambda **kwargs: configured_urls.append(kwargs["url"]),
        begin_transaction=nullcontext,
        run_migrations=lambda: None,
    )

    exec(
        compile(syntax_tree, str(ALEMBIC_ENV_PATH), "exec"),
        {"context": fake_context, "__name__": "alembic_env_test"},
    )
    return configured_urls


@pytest.mark.parametrize("url_source", ["attribute", "environment"])
def test_percent_encoded_database_url_is_configured_without_interpolation_error(
    monkeypatch: pytest.MonkeyPatch,
    url_source: str,
) -> None:
    """Preserve percent-encoded credentials through Alembic ConfigParser."""
    encoded_url = (
        "postgresql+psycopg://omf_retrieval_test:p%40ss@"
        "127.0.0.1:55432/omf_retrieval_test"
    )
    config = Config()
    config.set_main_option("sqlalchemy.url", "sqlite://")
    if url_source == "attribute":
        config.attributes["database_url"] = encoded_url
        monkeypatch.delenv("OMF_RETRIEVAL_DATABASE_URL", raising=False)
    else:
        monkeypatch.setenv("OMF_RETRIEVAL_DATABASE_URL", encoded_url)
    interpolation_error: ValueError | None = None

    try:
        configured_urls = _load_alembic_environment(config)
    except ValueError as caught_error:
        interpolation_error = caught_error
        configured_urls = []

    assert interpolation_error is None
    assert configured_urls == [encoded_url]
    assert config.get_main_option("sqlalchemy.url") == encoded_url
