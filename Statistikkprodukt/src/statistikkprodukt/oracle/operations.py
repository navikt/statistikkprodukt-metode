from __future__ import annotations

import logging
import math
from typing import Any

import oracledb
import pandas as pd
from statistikkprodukt.config import (
    DatabaseConnection,
    Aggregate,
    validate_identifier,
)
from statistikkprodukt.app.models import TableMetadata
from statistikkprodukt.oracle.aggregate_table import comment_statements
from statistikkprodukt.oracle.utils import execute_sql, executemany_sql

LOGGER = logging.getLogger(__name__)

_STRING_TYPE_CODES = (
    oracledb.DB_TYPE_CHAR,
    oracledb.DB_TYPE_NCHAR,
    oracledb.DB_TYPE_NVARCHAR,
    oracledb.DB_TYPE_VARCHAR,
)


def connect(configuration: DatabaseConnection) -> oracledb.Connection:
    """Kobler til konfigurert Oracle-database og velger skjema."""
    host, port, service = configuration.oracle_parameters()
    LOGGER.info(
        "Kobler til Oracle: host=%s, port=%s, tjeneste=%s, bruker=%s, skjema=%s, tidsfrist=%s.",
        host, port, service, configuration.user, configuration.schema_name, configuration.timeout,
    )
    connection = oracledb.connect(
        user=configuration.user,
        password=configuration.password.get_secret_value(),
        dsn=oracledb.makedsn(host, port, service_name=service),
        tcp_connect_timeout=configuration.timeout,
    )

    with connection.cursor() as cursor:
        execute_sql(
            cursor, f"ALTER SESSION SET CURRENT_SCHEMA = {configuration.schema_name}"
        )
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
        aggregate: Aggregate,
        *,
        periods_to_load: list[int | str] | None = None,
) -> pd.DataFrame:
    """Kjører aggregatspørringen og returnerer resultatet."""
    sql = sql.strip().removesuffix(";").rstrip()
    parameters = []
    if periods_to_load is not None:
        period = validate_identifier(aggregate.periodisering.kolonne)
        parameters = [_db_value(value) for value in periods_to_load]
        predicate = _generate_in_predicate(period, parameters)
        sql = f"SELECT * FROM (\n{sql}\n) WHERE {predicate}"
        LOGGER.debug("Begrenser spørringen for %s til %d perioder.", aggregate.tabell, len(parameters))

    with connection.cursor() as cursor:
        try:
            execute_sql(cursor, sql, parameters)
        except oracledb.DatabaseError as exc:
            _raise_friendly_source_sql_error(exc)
            raise

        description = cursor.description or []
        _validate_query_string_columns(description, aggregate)
        columns = [column[0].lower() for column in description]

        rows = cursor.fetchall()

    frame = pd.DataFrame(rows, columns=columns)
    LOGGER.debug("Spørringen for %s ga %d rader med kolonnene %s.", aggregate.tabell, len(frame), columns)
    return frame


def read_periods(
        connection: oracledb.Connection,
        sql: str,
        period: str,
        *,
        source_query: bool = True,
) -> set[int | str]:
    """Henter bare unike, ikke-tomme perioder fra en kildespørring."""
    period = validate_identifier(period)
    sql = sql.strip().removesuffix(";").rstrip()
    with connection.cursor() as cursor:
        try:
            execute_sql(
                cursor,
                f"SELECT DISTINCT {period} FROM (\n{sql}\n) "
                f"WHERE {period} IS NOT NULL",
            )
        except oracledb.DatabaseError as exc:
            if source_query:
                _raise_friendly_source_sql_error(exc)
            raise
        _validate_string_type(cursor.description, period)
        periods = {row[0] for row in cursor.fetchall()}
    LOGGER.debug("Fant %d perioder i %s.", len(periods), "kildespørringen" if source_query else "måltabellen")
    return periods


def read_target_periods(
        connection: oracledb.Connection, schema: str, aggregate: Aggregate
) -> set[int | str]:
    """Henter periodene som allerede finnes i måltabellen."""
    table = f"{validate_identifier(schema)}.{validate_identifier(aggregate.prepub_tabell)}"
    period = validate_identifier(aggregate.periodisering.kolonne)
    return read_periods(
        connection, f"SELECT {period} FROM {table}", period, source_query=False
    )


def _raise_friendly_source_sql_error(exc: oracledb.DatabaseError) -> None:
    """Gir forståelige meldinger for kjente feil i aggregatets kildespørring."""
    error = exc.args[0] if exc.args else None
    code = getattr(error, "code", getattr(exc, "code", None))
    if code == 942:
        raise ValueError(
            "En tabell eller visning i aggregatets SQL-spørring finnes ikke, "
            "eller databasebrukeren mangler tilgang. Kontroller navn og "
            "tilganger i SQL-filen. Du kan se hele spørringen som kjørte ved å bruke --log-level=DEBUG."
        ) from exc
    if code == 32034:
        raise ValueError(
            "Oracle kan ikke bruke aggregatets SQL-spørring som en underspørring. "
            "Dette kan skyldes en WITH-spørring; omskriv SQL-filen slik at "
            "spørringen kan brukes som underspørring.  Du kan se hele spørringen som kjørte "
            "ved å bruke --log-level=DEBUG."
        ) from exc


def table_exists(
        connection: oracledb.Connection, schema: str, table_name: str
) -> bool:
    """Sjekker om en tabell finnes i det angitte Oracle-skjemaet."""
    with connection.cursor() as cursor:
        execute_sql(
            cursor,
            "SELECT 1 FROM all_tables WHERE owner = :owner AND table_name = :table_name",
            owner=schema.upper(),
            table_name=table_name.upper(),
        )
        exists = cursor.fetchone() is not None
    LOGGER.debug("Tabellen %s.%s finnes: %s.", schema, table_name, exists)
    return exists


def validate_string_columns(
        connection: oracledb.Connection,
        schema: str,
        table_name: str,
        columns: list[str],
) -> None:
    """Kontrollerer at periode- og variabelkolonnene i en tabell er tekstkolonner."""
    with connection.cursor() as cursor:
        execute_sql(
            cursor,
            "SELECT column_name, data_type FROM all_tab_columns "
            "WHERE owner = :owner AND table_name = :table_name",
            owner=schema.upper(),
            table_name=table_name.upper(),
        )
        column_types = {
            column_name.lower(): data_type.upper()
            for column_name, data_type in cursor.fetchall()
        }

    for column in columns:
        data_type = column_types.get(column.lower())
        if data_type not in {"CHAR", "VARCHAR2", "NCHAR", "NVARCHAR2"}:
            actual_type = data_type or "mangler"
            raise ValueError(
                f"Kolonnen {column} i tabellen {schema}.{table_name} må være en "
                f"tekstkolonne (CHAR, VARCHAR2, NCHAR eller NVARCHAR2), "
                f"men er {actual_type}."
            )


def _validate_query_string_columns(description: Any, aggregate: Aggregate) -> None:
    columns = [
        aggregate.periodisering.kolonne,
        *(variable.kolonne for variable in aggregate.variabler),
    ]
    description_by_name = {
        column[0].lower(): column for column in (description or [])
    }
    for name in columns:
        column = description_by_name.get(name.lower())
        if column is None:
            raise ValueError(f"Kolonnen {name} mangler i resultatet fra SQL-spørringen.")
        _validate_string_type([column], name)


def _validate_string_type(description: Any, name: str) -> None:
    column = description[0] if description else None
    type_code = column[1] if column and len(column) > 1 else None
    if type_code not in _STRING_TYPE_CODES:
        actual_type = getattr(type_code, "name", str(type_code))
        raise ValueError(
            f"Kolonnen {name} i resultatet fra SQL-spørringen må være en tekstkolonne, "
            f"men er {actual_type}."
        )


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
            execute_sql(cursor, statement)


def write_result(
        connection: oracledb.Connection,
        schema: str,
        aggregate: Aggregate,
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

    period_column = aggregate.periodisering.kolonne.lower()
    if period_column not in frame.columns:
        raise ValueError(
            f"Periodekolonnen {aggregate.periodisering.kolonne} mangler i resultatet."
        )

    try:
        with connection.cursor() as cursor:
            LOGGER.debug("Skriver %d rader til %s (periodekolonne=%s).", len(frame), qualified_table_name, period_column)
            _delete_incremental_if_overwrite(cursor, qualified_table_name, frame, period_column)
            _insert_rows(cursor, qualified_table_name, frame)
        connection.commit()
        LOGGER.debug("Committet skriving til %s.", qualified_table_name)
    except Exception:
        LOGGER.debug("Feil ved skriving til %s; ruller tilbake.", qualified_table_name)
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
        LOGGER.debug("Sletter eksisterende rader i %s for perioder %s.", table, available_periods)
        predicate = _generate_in_predicate(period_column, available_periods)
        execute_sql(
            cursor, f"DELETE FROM {table} WHERE {predicate}", available_periods
        )


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
    executemany_sql(
        cursor, f"INSERT INTO {table} ({names}) VALUES ({binds})", rows
    )


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
