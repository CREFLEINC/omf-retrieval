"""Unit contracts for the PostgreSQL API-client repository."""

from uuid import uuid4

from sqlalchemy.dialects import postgresql

from omf_retrieval.infrastructure.database.repository_auth import (
    has_source_grant_in_session,
    source_grant_statement,
)


def test_source_grant_predicate_is_exact_parameterized_and_transaction_reusable() -> (
    None
):
    """Task 11 can reuse one minimal predicate without interpolating identities."""
    client_id = uuid4()
    source_key = "omf-sensitive-source"

    statement = source_grant_statement(client_id, source_key)
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)

    assert "client_source_grants" in sql
    assert "source_profiles" in sql
    assert "api_clients" in sql
    assert "api_clients.status" in sql
    assert "api_clients.expires_at" in sql
    assert "now()" in sql
    assert "FOR SHARE OF api_clients" in sql
    assert source_key not in sql
    assert client_id in compiled.params.values()
    assert source_key in compiled.params.values()
    assert "active" in compiled.params.values()


class _ScalarSession:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls = 0

    def scalar(self, _statement: object) -> object:
        self.calls += 1
        return self.result


def test_source_grant_check_allows_only_exact_true() -> None:
    """Truthy or integer adapter results cannot cross the authorization boundary."""
    client_id = uuid4()
    for result in (False, None, 0, 1, "true", object()):
        database_session = _ScalarSession(result)
        assert (
            has_source_grant_in_session(  # type: ignore[arg-type]
                database_session, client_id, "omf"
            )
            is False
        )
    exact = _ScalarSession(True)
    assert (
        has_source_grant_in_session(exact, client_id, "omf")  # type: ignore[arg-type]
        is True
    )


def test_invalid_grant_identity_fails_closed_without_query() -> None:
    """Invalid identity types do not reach PostgreSQL or coerce into a grant."""
    database_session = _ScalarSession(True)

    assert (
        has_source_grant_in_session(  # type: ignore[arg-type]
            database_session, str(uuid4()), "omf"
        )
        is False
    )
    assert database_session.calls == 0
