from __future__ import annotations

import logging
from typing import Any

from oracledb import Connection, Cursor

LOGGER = logging.getLogger(__name__)


def execute_sql(cursor: Cursor, statement: str, *args: Any, **kwargs: Any) -> Cursor:
    """Logger SQL-setningen på DEBUG-nivå før den kjøres."""
    LOGGER.debug("Kjører SQL:\n%s", statement)
    return cursor.execute(statement, *args, **kwargs)


def executemany_sql(
    cursor: Cursor, statement: str, *args: Any, **kwargs: Any
) -> Cursor:
    """Logger SQL-setningen på DEBUG-nivå før den kjøres for flere rader."""
    LOGGER.debug("Kjører SQL:\n%s", statement)
    return cursor.executemany(statement, *args, **kwargs)


def table_exists(connection: Connection, schema: str, table_name: str) -> bool:
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
