"""Shared fixtures for PostgreSQL integration tests."""

from collections.abc import Iterator

import pytest
from database_test_utils import assert_safe_test_connection, create_test_engine
from sqlalchemy import Connection


@pytest.fixture
def database_connection() -> Iterator[Connection]:
    """Yield a live connection to the isolated integration-test database."""
    engine = create_test_engine()
    try:
        with engine.connect() as connection:
            assert_safe_test_connection(connection)
            yield connection
    finally:
        engine.dispose()
