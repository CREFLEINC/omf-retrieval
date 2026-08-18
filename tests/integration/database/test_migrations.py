"""Integration tests for the PostgreSQL migration lifecycle."""

import os
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, create_engine, make_url, text

TEST_DATABASE_URL = (
    "postgresql+psycopg://omf_retrieval_test:omf_retrieval_test@"
    "127.0.0.1:55432/omf_retrieval_test"
)
REQUIRED_EXTENSIONS = {"pg_trgm", "vector"}
OVERRIDE_DATABASE_URL = f"{TEST_DATABASE_URL}?application_name=override-fixture"


def _installed_extensions(connection: Connection) -> set[str]:
    return set(
        connection.execute(
            text(
                "SELECT extname FROM pg_extension "
                "WHERE extname IN ('vector', 'pg_trgm')"
            )
        ).scalars()
    )


@pytest.fixture
def database_connection() -> Iterator[Connection]:
    """Yield a live connection to the isolated integration-test database."""
    database_url = os.getenv("OMF_RETRIEVAL_DATABASE_URL", TEST_DATABASE_URL)
    engine = create_engine(database_url)
    with engine.connect() as connection:
        yield connection
    engine.dispose()


@pytest.fixture
def alembic_config() -> Config:
    """Return the repository Alembic configuration."""
    return Config("alembic.ini")


def test_database_connection_prefers_environment_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The integration fixture follows the Alembic database URL override."""
    monkeypatch.setenv("OMF_RETRIEVAL_DATABASE_URL", OVERRIDE_DATABASE_URL)
    connection_iterator = database_connection.__wrapped__()
    connection = next(connection_iterator)

    try:
        assert connection.engine.url == make_url(OVERRIDE_DATABASE_URL)
    finally:
        connection_iterator.close()


def test_required_extensions_are_installed(database_connection: Connection) -> None:
    """The initial migration installs the approved PostgreSQL extensions."""

    assert _installed_extensions(database_connection) == REQUIRED_EXTENSIONS


def test_alembic_revision_is_at_head(
    database_connection: Connection,
    alembic_config: Config,
) -> None:
    """The database revision matches the migration script head."""
    applied_revision = database_connection.execute(
        text("SELECT version_num FROM alembic_version")
    ).scalar_one()
    expected_revision = ScriptDirectory.from_config(alembic_config).get_current_head()

    assert applied_revision == expected_revision


def test_extensions_survive_downgrade_and_reupgrade(
    database_connection: Connection,
    alembic_config: Config,
) -> None:
    """Downgrade preserves extensions and the revision upgrades again."""
    command.downgrade(alembic_config, "base")

    assert _installed_extensions(database_connection) == REQUIRED_EXTENSIONS

    command.upgrade(alembic_config, "head")
    applied_revision = database_connection.execute(
        text("SELECT version_num FROM alembic_version")
    ).scalar_one()
    expected_revision = ScriptDirectory.from_config(alembic_config).get_current_head()

    assert applied_revision == expected_revision
