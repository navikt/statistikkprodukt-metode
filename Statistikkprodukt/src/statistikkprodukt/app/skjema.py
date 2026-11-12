from __future__ import annotations

import logging
from pathlib import Path

import oracledb
from statistikkprodukt import oracle
from statistikkprodukt.app.last import _connect, _read_sql, _select_aggregates
from statistikkprodukt.config import Aggregate, Configuration
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
            for table_name, with_markers in tables:
                if oracle.table_exists(connection, schema, table_name):
                    LOGGER.info(
                        "Tabellen %s.%s finnes allerede; hopper over.",
                        schema,
                        table_name,
                    )
                else:
                    missing.append((table_name, with_markers))
            if not missing:
                continue

            metadata = oracle.read_metadata(connection, schema, aggregate)
            sql = _read_sql(aggregate, config_directory)
            statements = []
            for table_name, with_markers in missing:
                statements.append(
                    oracle.create_table_statement(
                        schema, table_name, sql, with_markers=with_markers
                    )
                )
                statements.extend(oracle.comment_statements(schema, table_name, metadata))

            if create_in_database:
                with connection.cursor() as cursor:
                    for statement in statements:
                        execute_sql(cursor, statement)
                string_columns = [
                    aggregate.periodisering.kolonne,
                    *(variable.kolonne for variable in aggregate.variabler),
                ]
                for table_name, _ in missing:
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
        oracle.LOG_TABLE: oracle.create_log_table_statement(schema),
        **{
            aggregate.meta_tabell: oracle.create_meta_table_statement(schema, aggregate.meta_tabell)
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
        for table_name, statement in statements.items():
            if table_name in existing:
                LOGGER.info("Tabellen %s.%s finnes allerede; hopper over.", schema, table_name)
            elif create_in_database:
                execute_sql(cursor, statement)
                LOGGER.info("Opprettet tabellen %s.%s.", schema, table_name)
            else:
                print(statement + ";")
