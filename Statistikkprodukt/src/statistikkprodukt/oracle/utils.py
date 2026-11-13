from __future__ import annotations

import logging
from typing import Any

from oracledb import Cursor

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
