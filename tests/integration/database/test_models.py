"""Integration tests for the SQLAlchemy database model contract."""

import warnings
from importlib.util import find_spec
from uuid import uuid4

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from database_test_utils import assert_safe_test_connection
from orm_parity import schema_catalog
from sqlalchemy import Connection, exc, text

from omf_retrieval.infrastructure.database import models
from omf_retrieval.infrastructure.database.base import Base

EXPECTED_TABLE_NAMES = {
    "api_clients",
    "chunk_embeddings",
    "chunks",
    "client_source_grants",
    "document_contents",
    "document_occurrences",
    "document_parses",
    "document_relations",
    "index_configs",
    "index_runs",
    "search_audit_events",
    "sections",
    "source_profiles",
}


def test_database_modules_exist() -> None:
    """Require the approved database mapping and session modules."""
    module_names = (
        "omf_retrieval.infrastructure.database.base",
        "omf_retrieval.infrastructure.database.models",
        "omf_retrieval.infrastructure.database.session",
    )

    missing_modules = [name for name in module_names if find_spec(name) is None]

    assert missing_modules == []


def test_model_metadata_has_exact_application_table_set() -> None:
    """Map every application table and no migration-only table."""
    assert models.Base is Base
    assert set(Base.metadata.tables) == EXPECTED_TABLE_NAMES


def test_mapper_registry_has_exact_model_class_set() -> None:
    """Register exactly one typed mapper for each application table."""
    expected_class_names = {
        "ApiClient",
        "Chunk",
        "ChunkEmbedding",
        "ClientSourceGrant",
        "DocumentContent",
        "DocumentOccurrence",
        "DocumentParse",
        "DocumentRelation",
        "IndexConfig",
        "IndexRun",
        "SearchAuditEvent",
        "Section",
        "SourceProfile",
    }

    assert {mapper.class_.__name__ for mapper in Base.registry.mappers} == (
        expected_class_names
    )


def test_uuid_identifier_columns_have_application_defaults() -> None:
    """Generate every standalone UUID identifier in the application on flush."""
    id_columns = {
        table_name: table.c.id
        for table_name, table in Base.metadata.tables.items()
        if "id" in table.c
    }

    assert set(id_columns) == EXPECTED_TABLE_NAMES - {"client_source_grants"}
    assert all(column.default is not None for column in id_columns.values())
    assert all(column.default.is_callable for column in id_columns.values())
    assert all(column.server_default is None for column in id_columns.values())


def test_orm_metadata_matches_migrated_schema(
    database_connection: Connection,
) -> None:
    """Keep ORM type, default, key, foreign key, and index metadata in parity."""
    migration_context = MigrationContext.configure(
        database_connection,
        opts={"compare_server_default": True, "compare_type": True},
    )

    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="Cannot correctly sort tables",
            category=exc.SAWarning,
        )
        differences = compare_metadata(migration_context, Base.metadata)

    assert differences == []


def test_orm_created_schema_catalog_matches_migration_catalog(
    database_connection: Connection,
) -> None:
    """Match every physical column, constraint, default, and explicit index."""
    schema_name = f"orm_parity_{uuid4().hex}"
    quoted_schema_name = database_connection.dialect.identifier_preparer.quote(
        schema_name
    )
    assert_safe_test_connection(database_connection)
    database_connection.execute(text(f"CREATE SCHEMA {quoted_schema_name}"))

    try:
        orm_connection = database_connection.execution_options(
            schema_translate_map={None: schema_name}
        )
        Base.metadata.create_all(orm_connection)

        assert schema_catalog(database_connection, schema_name) == schema_catalog(
            database_connection, "public"
        )
    finally:
        assert_safe_test_connection(database_connection)
        database_connection.execute(text(f"DROP SCHEMA {quoted_schema_name} CASCADE"))
