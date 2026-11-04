from __future__ import annotations

import logging
import math
from typing import Any

import oracledb
import pandas as pd
from statistikkprodukt.config import (
    DatabaseConnection,
    SmallAggregate,
    validate_identifier,
)
from statistikkprodukt.oracle.metadata import TableMetadata, comment_statements

LOGGER = logging.getLogger(__name__)


def connect(configuration: DatabaseConnection) -> oracledb.Connection:
    """Kobler til konfigurert Oracle-database og velger skjema."""
    host, port, service = configuration.oracle_parameters()
    connection = oracledb.connect(
        user=configuration.user,
        password=configuration.password.get_secret_value(),
        dsn=oracledb.makedsn(host, port, service_name=service),
        tcp_connect_timeout=configuration.timeout,
    )

    with connection.cursor() as cursor:
        cursor.execute(f"ALTER SESSION SET CURRENT_SCHEMA = {configuration.schema_name}")
    return connection


def _generate_in_predicate(column_name: str, elements: list[Any]) -> str:
    binds = [f":{index}" for index in range(1, len(elements) + 1)]
    # Oracle-versjoner før 23 begrenser IN-lister til 1000 verdier.
    clauses = [
        f"{column_name} IN ({', '.join(binds[start:start + 1000])})"
        for start in range(0, len(binds), 1000)
    ]
    return " OR ".join(clauses) if clauses else "1 = 0"


def read_query(
        connection: oracledb.Connection,
        sql: str,
        aggregate: SmallAggregate,
        *,
        periods_to_load: list[int | str] | None = None,
) -> pd.DataFrame:
    """Kjører aggregatspørringen og returnerer resultatet."""
    sql = sql.strip().removesuffix(";").rstrip()
    parameters = []
    if periods_to_load is not None:
        period = validate_identifier(aggregate.periodisering)
        parameters = [_db_value(value) for value in periods_to_load]
        predicate = _generate_in_predicate(period, parameters)
        sql = f"SELECT * FROM (\n{sql}\n) WHERE {predicate}"

    with connection.cursor() as cursor:
        cursor.execute(sql, parameters)

        description = cursor.description or []
        columns = [column[0].lower() for column in description]

        rows = cursor.fetchall()

    frame = pd.DataFrame(rows, columns=columns)
    return frame


def read_periods(
        connection: oracledb.Connection, sql: str, period: str
) -> set[int | str]:
    """Henter bare unike, ikke-tomme perioder fra en kildespørring."""
    period = validate_identifier(period)
    sql = sql.strip().removesuffix(";").rstrip()
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT DISTINCT {period} FROM (\n{sql}\n) WHERE {period} IS NOT NULL"
        )
        return {row[0] for row in cursor.fetchall()}


def read_target_periods(
        connection: oracledb.Connection, schema: str, aggregate: SmallAggregate
) -> set[int | str]:
    """Henter periodene som allerede finnes i måltabellen."""
    table = f"{validate_identifier(schema)}.{validate_identifier(aggregate.prepub_tabell)}"
    period = validate_identifier(aggregate.periodisering)
    return read_periods(connection, f"SELECT {period} FROM {table}", period)


def table_exists(
        connection: oracledb.Connection, schema: str, table_name: str
) -> bool:
    """Sjekker om en tabell finnes i det angitte Oracle-skjemaet."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM all_tables WHERE owner = :owner AND table_name = :table_name",
            owner=schema.upper(),
            table_name=table_name.upper(),
        )
        return cursor.fetchone() is not None


def create_table_statement(
        schema: str, table_name: str, sql: str, *, with_markers: bool = False
) -> str:
    """Lager DDL for en tom tabell basert på SQL-spørringens kolonner.

    Kontrolltabellen har i tillegg markørkolonnene primary og suppressed.
    """
    table = f"{validate_identifier(schema)}.{validate_identifier(table_name)}"
    source_sql = sql.strip().removesuffix(";").rstrip()
    markers = (
        ", CAST(NULL AS NUMBER(1)) AS primary, CAST(NULL AS NUMBER(1)) AS suppressed"
        if with_markers
        else ""
    )
    return (
        f"CREATE TABLE {table} AS\n"
        f"SELECT source_data.*{markers}\n"
        f"FROM (\n{source_sql}\n) source_data\n"
        "WHERE 1 = 0"
    )


def write_comments(
        connection: oracledb.Connection,
        schema: str,
        table_name: str,
        metadata: TableMetadata,
) -> None:
    """Oppretter eller oppdaterer tabellens og kolonnenes Oracle-kommentarer."""
    with connection.cursor() as cursor:
        for statement in comment_statements(schema, table_name, metadata):
            cursor.execute(statement)


def write_result(
        connection: oracledb.Connection,
        schema: str,
        aggregate: SmallAggregate,
        table_name: str,
        frame: pd.DataFrame,
        *,
        dry_run: bool = False,
) -> None:
    """Skriver aggregatresultatet til tabellen table_name transaksjonelt."""
    if frame.empty:
        LOGGER.info("Ingen rader for %s; databasen endres ikke.", table_name)
        return

    if dry_run:
        LOGGER.info(
            "Ville skrevet %d rader til %s.%s.",
            len(frame),
            schema,
            table_name,
        )
        return

    qualified_table_name = (
        f"{validate_identifier(schema)}."
        f"{validate_identifier(table_name)}"
    )

    period_column = aggregate.periodisering.lower()
    if period_column not in frame.columns:
        raise ValueError(f"Periodekolonnen {aggregate.periodisering} mangler i resultatet.")

    try:
        with connection.cursor() as cursor:
            _delete_incremental_if_overwrite(cursor, qualified_table_name, frame, period_column)
            _insert_rows(cursor, qualified_table_name, frame)
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def _delete_incremental_if_overwrite(cursor: Any, table: str,
                                     frame: pd.DataFrame,
                                     period_column: str) -> None:
    """Sletter eksisterende rader i tabellen hvis inkrementell modus er satt til overskriv.

    Antar at table / period identifikatorer er gyldige Oracle identifier.
    """
    # frame is already only containing those periods that should be inserted, prefiltered for incremental mode
    available_periods = frame[period_column].dropna().unique().tolist()
    if available_periods:
        predicate = _generate_in_predicate(period_column, available_periods)
        cursor.execute(f"DELETE FROM {table} WHERE {predicate}", available_periods)


def _insert_rows(cursor: Any, table: str, frame: pd.DataFrame) -> None:
    """Setter inn alle rader i tabellen med parameteriserte verdier."""
    if frame.empty:
        return

    columns = list(frame.columns)
    names = ", ".join(validate_identifier(name) for name in columns)
    binds = ", ".join(f":{index + 1}" for index in range(len(columns)))
    rows = [
        tuple(_db_value(value) for value in row)
        for row in frame.itertuples(index=False, name=None)
    ]
    cursor.executemany(f"INSERT INTO {table} ({names}) VALUES ({binds})", rows)


def _db_value(value: Any) -> Any:
    """Konverterer pandas- og numpy-verdier til Oracle-kompatible verdier."""
    if value is None:
        return None
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, bool):
        return int(value)
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, float) and math.isnan(value):
        return None
    return value
