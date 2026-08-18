"""Connection utilities shared by PostgreSQL integration tests."""

import os

from sqlalchemy import URL, Connection, Engine, create_engine, make_url, text

DEFAULT_TEST_DATABASE_URL = (
    "postgresql+psycopg://omf_retrieval_test:omf_retrieval_test@"
    "127.0.0.1:55432/omf_retrieval_test"
)
TEST_DATABASE_URL_ENV = "OMF_RETRIEVAL_TEST_DATABASE_URL"
GENERIC_DATABASE_URL_ENV = "OMF_RETRIEVAL_DATABASE_URL"
SAFE_TEST_DATABASE_HOST = "127.0.0.1"
SAFE_TEST_DATABASE_PORT = 55432
SAFE_TEST_DATABASE_USER = "omf_retrieval_test"
SAFE_TEST_DATABASE_PREFIX = "omf_retrieval_test"
ALLOWED_TEST_DATABASE_QUERY_KEYS = frozenset({"application_name"})


def _database_name_is_safe(database_name: object) -> bool:
    return isinstance(database_name, str) and (
        database_name == SAFE_TEST_DATABASE_PREFIX
        or database_name.startswith(f"{SAFE_TEST_DATABASE_PREFIX}_")
    )


def _validate_effective_connect_arguments(parsed_url: URL) -> None:
    dialect = parsed_url.get_dialect()()
    positional_arguments, keyword_arguments = dialect.create_connect_args(parsed_url)
    database_name = keyword_arguments.get(
        "dbname",
        keyword_arguments.get("database"),
    )
    if positional_arguments or (
        keyword_arguments.get("host") != SAFE_TEST_DATABASE_HOST
        or keyword_arguments.get("port") != SAFE_TEST_DATABASE_PORT
        or keyword_arguments.get("user") != SAFE_TEST_DATABASE_USER
        or not _database_name_is_safe(database_name)
    ):
        raise ValueError("Unsafe effective test database target")


def validate_test_database_url(database_url: str | URL) -> URL:
    """Validate that a URL can only target the isolated local test database."""
    parsed_url = make_url(database_url)
    unsupported_query_keys = set(parsed_url.query) - ALLOWED_TEST_DATABASE_QUERY_KEYS
    if unsupported_query_keys:
        raise ValueError("Unsafe test database URL query option")
    if (
        parsed_url.host != SAFE_TEST_DATABASE_HOST
        or parsed_url.port != SAFE_TEST_DATABASE_PORT
        or parsed_url.username != SAFE_TEST_DATABASE_USER
        or not _database_name_is_safe(parsed_url.database)
    ):
        raise ValueError("Unsafe test database URL: isolated test identity required")
    _validate_effective_connect_arguments(parsed_url)
    return parsed_url


def test_database_url() -> str:
    """Return only the validated test-specific URL override or safe default."""
    database_url = os.getenv(TEST_DATABASE_URL_ENV)
    if database_url is None and os.getenv(GENERIC_DATABASE_URL_ENV) is not None:
        raise ValueError(
            "Generic database URL is set without an explicit test database URL"
        )
    if database_url is None:
        database_url = DEFAULT_TEST_DATABASE_URL
    validate_test_database_url(database_url)
    return database_url


def create_test_engine() -> Engine:
    """Create an engine for the resolved integration-test database URL."""
    return create_engine(test_database_url())


def assert_safe_test_connection(connection: Connection) -> None:
    """Revalidate the live database and role before destructive test operations."""
    database_name, database_user = connection.execute(
        text("SELECT current_database(), current_user")
    ).one()
    if database_user != SAFE_TEST_DATABASE_USER or not _database_name_is_safe(
        database_name
    ):
        raise ValueError("Unsafe live test database identity: destructive SQL blocked")
