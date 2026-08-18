"""Shared fixtures for PostgreSQL integration tests."""

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import Connection, create_engine

TEST_DATABASE_URL = (
    "postgresql+psycopg://omf_retrieval_test:omf_retrieval_test@"
    "127.0.0.1:55432/omf_retrieval_test"
)


@pytest.fixture
def database_connection() -> Iterator[Connection]:
    """Yield a live connection to the isolated integration-test database."""
    database_url = os.getenv("OMF_RETRIEVAL_DATABASE_URL", TEST_DATABASE_URL)
    engine = create_engine(database_url)
    with engine.connect() as connection:
        yield connection
    engine.dispose()
