from __future__ import annotations

import logging
import re
from datetime import datetime

import oracledb
from oracledb import Cursor
from statistikkprodukt.app.models import TableMetadata
from statistikkprodukt.config import Aggregate, Metadata, validate_identifier
from statistikkprodukt.oracle.utils import execute_sql, table_exists

LOGGER = logging.getLogger(__name__)


def _format_comment(metadata: Metadata) -> str:
    comment = " ".join(
        f"#{tag.upper()} {getattr(metadata, tag)}" for tag in Metadata.model_fields
    )
    return comment.replace("'", "''")


def comment_statements(
    schema: str, table_name: str, metadata: TableMetadata
) -> list[str]:
    """Lager Oracle-setninger for kommentarer på tabellen og kolonnene."""
    table = f"{validate_identifier(schema)}.{validate_identifier(table_name)}"
    statements = [f"COMMENT ON TABLE {table} IS '{_format_comment(metadata.table)}'"]
    for column, column_metadata in metadata.columns.items():
        qualified_column = f"{table}.{validate_identifier(column)}"
        statements.append(
            f"COMMENT ON COLUMN {qualified_column} IS '{_format_comment(column_metadata)}'"
        )
    return statements


def kontroll_comment_statements(
    schema: str,
    table_name: str,
    aggregate: Aggregate,
    metadata: TableMetadata,
) -> list[str]:
    """Lager kommentarer for kontrolltabellen med dimensjoner samt tellepunkt og verdi."""
    columns = {
        column: metadata.columns[column]
        for column in [
            aggregate.periodisering.kolonne,
            *(variable.kolonne for variable in aggregate.variabler),
        ]
    }
    columns["tellepunkt"] = Metadata(
        navn="Tellepunkt",
        navn_en="Count point",
        innhold="Navnet på tellepunktkolonnen som verdien gjelder for.",
        innhold_en="Name of the count point column the value belongs to.",
    )
    columns["verdi"] = Metadata(
        navn="Verdi",
        navn_en="Value",
        innhold="Verdien for tellepunktet.",
        innhold_en="The value for the count point.",
    )
    columns["primary"] = Metadata(
        navn="Primær prikket flagg",
        navn_en="Primary suppressed flag",
        innhold="Binær verdi som sier om en verdi ble primærprikket eller ikke",
        innhold_en="Binary value that tells what values were primary suppressed",
    )
    columns["suppressed"] = Metadata(
        navn="Prikket flagg",
        navn_en="Suppressed flag",
        innhold="Binær verdi som sier om en verdi ble prikket eller ikke",
        innhold_en="Binary value that tells what values were suppressed",
    )
    columns["dvh_lastet_tid"] = Metadata(
        navn="DVH lastet tidspunkt",
        navn_en="DWH loaded timestamp",
        innhold="Lastetidspunkt for når raden ble skrevet til databasen",
        innhold_en="Timestamp for when the row was written to the database",
    )
    return comment_statements(
        schema,
        table_name,
        TableMetadata(table=metadata.table, columns=columns),
    )


def read_metadata(
    connection: oracledb.Connection,
    schema: str,
    aggregate: Aggregate,
) -> TableMetadata:
    """Fyller manglende aggregatmetadata fra Oracle-kommentarer.

    Data fra konfigurasjon prioriteres over databasekommentarer.
    """
    table_parts = aggregate.referanse_tabell.split(".")
    owner, table_name = (table_parts[0], table_parts[1]) if len(table_parts) == 2 else (
        schema,
        table_parts[0],
    )
    with connection.cursor() as cursor:
        LOGGER.debug("Leser metadata for %s fra %s.%s.", aggregate.tabell, owner, table_name)
        db_metadata = _fetch_table_metadata(owner, table_name, cursor)
        config_metadata = Metadata.model_validate(
            aggregate.model_dump(include=set(Metadata.model_fields))
        )
        table_metadata = _merge_metadata(table_name, config_metadata, db_metadata)

        columns_metadata = {}
        for variable in [
            aggregate.periodisering,
            *aggregate.variabler,
            *aggregate.tellepunkter,
        ]:
            column = variable.kolonne
            db_metadata = _fetch_column_metadata(owner, table_name, column, cursor)
            config_metadata = Metadata.model_validate(
                variable.model_dump(include=set(Metadata.model_fields))
            )
            columns_metadata[column] = _merge_metadata(
                column, config_metadata, db_metadata
            )

    return TableMetadata(table=table_metadata, columns=dict(columns_metadata))


def _merge_metadata(column, config_metadata: Metadata, db_metadata: Metadata):
    """Kombinerer metadata fra konfigurasjon og databasekommentarer, prioritert etter konfigurasjon."""
    result = Metadata()
    comments_from_db, comments_from_yaml = 0, 0
    for tag in Metadata.model_fields.keys():
        value = getattr(config_metadata, tag)
        if value:
            comments_from_yaml += 1
        elif db_value := getattr(db_metadata, tag):
            value = db_value
            comments_from_db += 1
        elif tag.lower().endswith("_en"):
            # todo: hack for å hoppe over valgfrie engelske metadatafelter
            continue
        else:
            raise ValueError(
                f"Metadatafeltet {tag} mangler for {column}; angi det i YAML."
            )
        source = "fra konfigurasjon" if value else "fra databasekommentar"
        LOGGER.debug("%s.%s = %s (%s)", column, tag, value, source)
        setattr(result, tag, value)

    if comments_from_db > 0 or comments_from_yaml > 0:
        LOGGER.debug(
            "Tabell %s: Bruker %d kommentarer fra referansetabellen, %d fra konfigurasjon.",
            column, comments_from_db, comments_from_yaml,
        )

    return result


def _fetch_column_metadata(
    schema: str, table_name: str, column: str, cursor: Cursor
) -> Metadata:
    execute_sql(
        cursor,
        "select comments from sys.dba_col_comments where owner = :owner "
        "and table_name = :table_name and column_name = :column_name",
        owner=schema.upper(),
        table_name=table_name.upper(),
        column_name=column.upper(),
    )
    row = cursor.fetchone()
    return parse_metadata(row[0] if row else None)


def _fetch_table_metadata(schema: str, table_name: str, cursor: Cursor) -> Metadata:
    execute_sql(
        cursor,
        "select comments from sys.dba_tab_comments where owner = :owner "
        "and table_name = :table_name",
        owner=schema.upper(),
        table_name=table_name.upper(),
    )
    row = cursor.fetchone()
    return parse_metadata(row[0] if row else None)


def parse_metadata(comment: str | None) -> Metadata:
    """Parser navne- og innholdstagger fra en databasekommentar."""
    if not comment:
        return Metadata()
    matches = list(re.finditer(r"#(NAVN(?:_EN)?|INNHOLD(?:_EN)?)\b", comment, re.I))
    values: dict[str, str] = {}
    seen: set[str] = set()
    for index, match in enumerate(matches):
        tag = match.group(1).upper()
        if tag in seen:
            raise ValueError(f"Databasekommentaren inneholder taggen #{tag} flere ganger.")
        seen.add(tag)

        end = matches[index + 1].start() if index + 1 < len(matches) else len(comment)
        value = comment[match.end(): end].strip()
        if value:
            values[tag.lower()] = value

    return Metadata.model_validate(values)


def read_aggregate_last_updated(connection: oracledb.Connection, schema: str, aggregate: Aggregate) -> datetime | None:
    """Henter siste lastetid fra prepub-tabellen hvis den finnes."""
    schema = validate_identifier(schema)
    table_name = validate_identifier(aggregate.prepub_tabell)
    table = f"{schema}.{table_name}"
    try:
        # dette kan skje når man laster fra DB til CSV før prepub-tabellen er opprettet
        if not table_exists(connection, schema, table_name):
            return None
        with connection.cursor() as cursor:
            execute_sql(cursor, f"SELECT MAX(dvh_lastet_tid) FROM {table}")
            row = cursor.fetchone()
            return row[0] if row else None
    except Exception as exc:
        LOGGER.debug("Feil ved henting av siste oppdateringstid for %s", table, exc_info=True)
        raise
