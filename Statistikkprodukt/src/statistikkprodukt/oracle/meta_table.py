from __future__ import annotations

import logging

import oracledb
from statistikkprodukt.app.models import TableMetadata
from statistikkprodukt.config import Aggregate, validate_identifier
from statistikkprodukt.oracle.utils import execute_sql, executemany_sql

LOGGER = logging.getLogger(__name__)

# Metadatatabellen er på radformat: én rad per felt, med feltnavnet i "felt"
# og innholdet i "verdi". dvh_sist_oppdatert settes av databasen.
META_FIELDS = (
    "id",
    "tabell",
    "navn",
    "navn_en",
    "innhold",
    "innhold_en",
    "periodisering",
)
META_COLUMNS = ("felt", "verdi")


def create_meta_table_statement(schema: str, table_name: str) -> str:
    """Lager DDL for metadatatabellen ({tabell}_meta) på radformat (felt/verdi)."""
    table = f"{validate_identifier(schema)}.{validate_identifier(table_name)}"
    return (
        f"CREATE TABLE {table} (\n"
        "  felt               VARCHAR2(128 CHAR)  NOT NULL,\n"
        "  verdi              CLOB,\n"
        "  dvh_sist_oppdatert TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL\n"
        ")"
    )


def metadata_rows(aggregate: Aggregate, metadata: TableMetadata) -> list[dict]:
    """Bygger radene i metadatatabellen: én rad per felt i META_FIELDS."""
    values = {
        "id": aggregate.id,
        "tabell": aggregate.tabell,
        "navn": metadata.table.navn,
        "navn_en": metadata.table.navn_en,
        "innhold": metadata.table.innhold,
        "innhold_en": metadata.table.innhold_en,
        "periodisering": aggregate.periodisering.kolonne,
    }
    return [{"felt": felt, "verdi": values[felt]} for felt in META_FIELDS]


def write_metadata_table(
    connection: oracledb.Connection,
    schema: str,
    aggregate: Aggregate,
    metadata: TableMetadata,
) -> None:
    """Overskriver hele {tabell}_meta med gjeldende metadata i én transaksjon."""
    table = f"{validate_identifier(schema)}.{validate_identifier(aggregate.meta_tabell)}"
    rows = metadata_rows(aggregate, metadata)
    names = ", ".join(META_COLUMNS)
    binds = ", ".join(f":{name}" for name in META_COLUMNS)
    try:
        with connection.cursor() as cursor:
            # DELETE i stedet for TRUNCATE: TRUNCATE er DDL og committer implisitt.
            execute_sql(cursor, f"DELETE FROM {table}")
            cursor.setinputsizes(verdi=oracledb.DB_TYPE_CLOB)
            executemany_sql(
                cursor, f"INSERT INTO {table} ({names}) VALUES ({binds})", rows
            )
        connection.commit()
    except oracledb.DatabaseError as exc:
        connection.rollback()
        (error,) = exc.args
        if getattr(error, "code", None) == 942:  # ORA-00942: tabellen finnes ikke
            raise RuntimeError(
                f"Metadatatabellen {table} finnes ikke. Kjør "
                "'statistikkprodukt skjema opprett --db' for å opprette den."
            ) from exc
        raise
    LOGGER.info("Overskrev %s med %d rader.", table, len(rows))
