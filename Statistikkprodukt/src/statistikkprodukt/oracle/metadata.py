from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import oracledb
from oracledb import Cursor
from statistikkprodukt.config import SmallAggregate, Metadata, validate_identifier

LOGGER = logging.getLogger(__name__)


@dataclass
class TableMetadata:
    table: Metadata
    columns: dict[str, Metadata]


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


def read_metadata(
        connection: oracledb.Connection,
        schema: str,
        aggregate: SmallAggregate
) -> TableMetadata:
    """Fyller manglende aggregatmetadata fra Oracle-kommentarer.

    Data fra konfigurasjon prioriteres over databasekommentarer.
    """
    table_parts = aggregate.obt_tabell.split(".")
    owner, table_name = (table_parts[0], table_parts[1]) if len(table_parts) == 2 else (
        schema,
        table_parts[0],
    )
    with connection.cursor() as cursor:
        db_metadata = _fetch_table_metadata(owner, table_name, cursor)
        config_metadata = Metadata.model_validate(
            aggregate.model_dump(include=set(Metadata.model_fields))
        )
        table_metadata = _merge_metadata(table_name, config_metadata, db_metadata)

        columns_metadata = {}
        for variable in [*aggregate.variabler, aggregate.tallkolonne]:
            db_metadata = _fetch_column_metadata(owner, table_name, variable.kolonne, cursor)
            config_metadata = Metadata.model_validate(
                variable.model_dump(include=set(Metadata.model_fields))
            )
            column_metadata = _merge_metadata(variable.kolonne, config_metadata, db_metadata)

            columns_metadata[variable.kolonne] = column_metadata

    return TableMetadata(table=table_metadata, columns=dict(columns_metadata))


def _merge_metadata(column, config_metadata: Metadata, db_metadata: Metadata):
    """Kombinerer metadata fra konfigurasjon og databasekommentarer, prioritert etter konfigurasjon."""
    result = Metadata()
    for tag in Metadata.model_fields.keys():
        value = getattr(config_metadata, tag)
        if value:
            LOGGER.info("%s.%s hentes fra konfigurasjonen.", column, tag)
        elif db_value := getattr(db_metadata, tag):
            value = db_value
            LOGGER.info("%s.%s hentes fra databasekommentar.", column, tag)
        else:
            raise ValueError(
                f"Metadatafeltet {tag} mangler for {column}; angi det i YAML "
                "eller legg det til som databasekommentar."
            )
        LOGGER.debug("%s.%s = %s", column, tag, value)
        setattr(result, tag, value)

    return result


def _fetch_column_metadata(schema: str, table_name: str, column: str, cursor: Cursor) -> Metadata:
    cursor.execute(
        "select comments from sys.dba_col_comments where owner = :owner "
        "and table_name = :table_name and column_name = :column_name",
        owner=schema.upper(),
        table_name=table_name.upper(),
        column_name=column.upper(),
    )
    row = cursor.fetchone()
    return parse_metadata(row[0] if row else None)


def _fetch_table_metadata(schema: str, table_name: str, cursor: Cursor) -> Metadata:
    cursor.execute(
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
