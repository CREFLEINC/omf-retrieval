"""Connection utilities shared by PostgreSQL integration tests."""

import os

from sqlalchemy import Engine, create_engine

DEFAULT_TEST_DATABASE_URL = (
    "postgresql+psycopg://omf_retrieval_test:omf_retrieval_test@"
    "127.0.0.1:55432/omf_retrieval_test"
)


def test_database_url() -> str:
    """Return the Alembic-compatible test database URL override or default."""
    return os.getenv("OMF_RETRIEVAL_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)


def create_test_engine() -> Engine:
    """Create an engine for the resolved integration-test database URL."""
    return create_engine(test_database_url())
