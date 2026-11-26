from __future__ import annotations

import logging
from pathlib import Path

import oracledb
from statistikkprodukt import oracle
from statistikkprodukt.app.last import _connect, _read_sql, _select_aggregates
from statistikkprodukt.config import Aggregate, Configuration, validate_identifier
from statistikkprodukt.oracle.utils import execute_sql

LOGGER = logging.getLogger(__name__)


def create_tables(
        configuration: Configuration,
        config_directory: Path,
        *,
        selected_tables: list[str] | None = None,
        create_in_database: bool = False,
) -> None:
    """Viser DDL for tomme småaggregattabeller eller oppretter dem i Oracle."""
    aggregates = _select_aggregates(configuration, selected_tables)
    LOGGER.debug("Oppretter skjema for %s (i database: %s).", [a.tabell for a in aggregates], create_in_database)
    connection = _connect(configuration)
    try:
        schema = configuration.connection.schema_name
        _create_log_and_meta_tables(
            connection, schema, aggregates, create_in_database=create_in_database
        )
        for aggregate in aggregates:
            tables = [
                (aggregate.prepub_tabell, False),
                (aggregate.kontroll_tabell, True),
            ]
            missing = []
            for table_name, is_kontroll_tabell in tables:
                if oracle.table_exists(connection, schema, table_name):
                    LOGGER.info(
                        "Tabellen %s.%s finnes allerede; hopper over.",
                        schema,
                        table_name,
                    )
                else:
                    missing.append((table_name, is_kontroll_tabell))
            if not missing:
                continue

            LOGGER.debug("Mangler tabeller for %s: %s.", aggregate.tabell, [name for name, _ in missing])
            metadata = oracle.read_metadata(connection, schema, aggregate)
            sql = _read_sql(aggregate, config_directory)
            statements = []
            for table_name, is_kontroll_tabell in missing:
                if is_kontroll_tabell:
                    statements.append(
                        oracle.create_kontroll_table_statement(
                            schema, table_name, sql, aggregate
                        )
                    )
                    table = f"{validate_identifier(schema)}.{validate_identifier(table_name)}"
                    statements.append(
                        f"ALTER TABLE {table} ADD ("
                        "dvh_lastet_tid DATE DEFAULT SYSDATE NOT NULL)"
                    )
                    statements.extend(
                        oracle.kontroll_comment_statements(
                            schema, table_name, aggregate, metadata
                        )
                    )
                else:
                    statements.append(
                        oracle.create_table_statement(schema, table_name, sql)
                    )
                    table = f"{validate_identifier(schema)}.{validate_identifier(table_name)}"
                    statements.append(
                        f"ALTER TABLE {table} ADD ("
                        "dvh_lastet_tid DATE DEFAULT SYSDATE NOT NULL)"
                    )
                    statements.extend(
                        oracle.comment_statements(schema, table_name, metadata)
                    )

            if create_in_database:
                with connection.cursor() as cursor:
                    for statement in statements:
                        execute_sql(cursor, statement)

                # fail if any column that's supposed to be a string is not a string
                for table_name, is_kontroll_tabell in missing:
                    string_columns = [
                        aggregate.periodisering.kolonne,
                        *(variable.kolonne for variable in aggregate.variabler),
                    ]
                    if is_kontroll_tabell:
                        string_columns.append("tellepunkt")
                    oracle.validate_string_columns(
                        connection, schema, table_name, string_columns
                    )
                LOGGER.info(
                    "Opprettet tabellene %s.",
                    ", ".join(f"{schema}.{name}" for name, _ in missing),
                )
            else:
                print(";\n".join(statements) + ";")
    finally:
        LOGGER.debug("Lukker databasetilkoblingen.")
        connection.close()


def _create_log_and_meta_tables(
        connection: oracledb.Connection,
        schema: str,
        aggregates: list[Aggregate],
        *,
        create_in_database: bool,
) -> None:
    """Sjekker dictionary før DDL for én loggtabell og aggregatenes metadatatabeller."""
    statements = {
        oracle.LOG_TABLE: (
            oracle.create_log_table_statement(schema),
            *oracle.log_comment_statements(schema),
        ),
        **{
            aggregate.meta_tabell: (
                oracle.create_meta_table_statement(schema, aggregate.meta_tabell),
                *oracle.meta_comment_statements(schema, aggregate.meta_tabell),
            )
            for aggregate in aggregates
        },
    }
    with connection.cursor() as cursor:
        execute_sql(
            cursor,
            "SELECT table_name FROM all_tables WHERE owner = :owner",
            owner=schema.upper(),
        )
        existing = {row[0].lower() for row in cursor.fetchall()}
        for table_name, table_statements in statements.items():
            if table_name in existing:
                LOGGER.info("Tabellen %s.%s finnes allerede; hopper over.", schema, table_name)
            elif create_in_database:
                for statement in table_statements:
                    execute_sql(cursor, statement)
                LOGGER.info("Opprettet tabellen %s.%s.", schema, table_name)
            else:
                print(";\n".join(table_statements) + ";")
