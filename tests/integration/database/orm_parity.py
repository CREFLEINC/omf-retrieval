"""PostgreSQL catalog snapshots used by ORM metadata parity tests."""

from typing import Any

from sqlalchemy import Connection, text


def schema_catalog(connection: Connection, schema_name: str) -> dict[str, Any]:
    """Return a structured physical schema map for semantic comparison."""
    table_names = set(
        connection.execute(
            text(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = :schema_name "
                "AND tablename <> 'alembic_version'"
            ),
            {"schema_name": schema_name},
        ).scalars()
    )
    columns = set(
        connection.execute(
            text(
                "SELECT rel.relname, att.attname, "
                "format_type(att.atttypid, att.atttypmod), att.attnotnull, "
                "pg_get_expr(def.adbin, def.adrelid) "
                "FROM pg_class rel "
                "JOIN pg_namespace ns ON ns.oid = rel.relnamespace "
                "JOIN pg_attribute att ON att.attrelid = rel.oid "
                "LEFT JOIN pg_attrdef def ON def.adrelid = rel.oid "
                "AND def.adnum = att.attnum "
                "WHERE ns.nspname = :schema_name AND rel.relkind = 'r' "
                "AND rel.relname <> 'alembic_version' "
                "AND att.attnum > 0 AND NOT att.attisdropped"
            ),
            {"schema_name": schema_name},
        ).tuples()
    )
    constraint_rows = connection.execute(
        text(
            "SELECT con.conname, rel.relname, con.contype, "
            "ARRAY(SELECT att.attname FROM unnest(con.conkey) "
            "WITH ORDINALITY AS key(attnum, ord) "
            "JOIN pg_attribute att ON att.attrelid = con.conrelid "
            "AND att.attnum = key.attnum ORDER BY key.ord), "
            "ref.relname, "
            "ARRAY(SELECT att.attname FROM unnest(con.confkey) "
            "WITH ORDINALITY AS key(attnum, ord) "
            "JOIN pg_attribute att ON att.attrelid = con.confrelid "
            "AND att.attnum = key.attnum ORDER BY key.ord), "
            "CASE WHEN con.contype = 'f' THEN con.confdeltype::text END, "
            "pg_get_constraintdef(con.oid, true) "
            "FROM pg_constraint con "
            "JOIN pg_class rel ON rel.oid = con.conrelid "
            "JOIN pg_namespace ns ON ns.oid = rel.relnamespace "
            "LEFT JOIN pg_class ref ON ref.oid = con.confrelid "
            "WHERE ns.nspname = :schema_name "
            "AND rel.relname <> 'alembic_version' "
            "AND con.contype <> 'n'"
        ),
        {"schema_name": schema_name},
    ).all()
    constraints = {
        (
            name,
            table_name,
            constraint_type,
            tuple(source_columns),
            referenced_table,
            tuple(referenced_columns),
            delete_action,
            definition if constraint_type == "c" else None,
        )
        for (
            name,
            table_name,
            constraint_type,
            source_columns,
            referenced_table,
            referenced_columns,
            delete_action,
            definition,
        ) in constraint_rows
    }
    index_rows = connection.execute(
        text(
            "SELECT idx.relname, rel.relname, am.amname, "
            "ARRAY(SELECT pg_get_indexdef(i.indexrelid, position, true) "
            "FROM generate_series(1, i.indnkeyatts) position), "
            "ARRAY(SELECT opc.opcname FROM unnest(i.indclass::oid[]) "
            "WITH ORDINALITY classes(opcoid, ord) "
            "JOIN pg_opclass opc ON opc.oid = classes.opcoid "
            "WHERE classes.ord <= i.indnkeyatts ORDER BY classes.ord) "
            "FROM pg_index i "
            "JOIN pg_class idx ON idx.oid = i.indexrelid "
            "JOIN pg_class rel ON rel.oid = i.indrelid "
            "JOIN pg_namespace ns ON ns.oid = rel.relnamespace "
            "JOIN pg_am am ON am.oid = idx.relam "
            "LEFT JOIN pg_constraint con ON con.conindid = i.indexrelid "
            "WHERE ns.nspname = :schema_name "
            "AND rel.relname <> 'alembic_version' "
            "AND con.oid IS NULL"
        ),
        {"schema_name": schema_name},
    ).all()
    indexes = {
        (name, table_name, method, tuple(columns), tuple(opclasses))
        for name, table_name, method, columns, opclasses in index_rows
    }

    return {
        "tables": table_names,
        "columns": columns,
        "constraints": constraints,
        "indexes": indexes,
    }
