"""Shared fixtures for PostgreSQL integration tests."""

from collections.abc import Iterator

import pytest
from database_test_utils import create_test_engine
from sqlalchemy import Connection


@pytest.fixture
def database_connection() -> Iterator[Connection]:
    """Yield a live connection to the isolated integration-test database."""
    engine = create_test_engine()
    with engine.connect() as connection:
        yield connection
    engine.dispose()
