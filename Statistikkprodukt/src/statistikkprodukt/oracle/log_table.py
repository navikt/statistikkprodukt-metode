from __future__ import annotations

import logging

import oracledb
from statistikkprodukt.app.models import Kjorelogg
from statistikkprodukt.config import validate_identifier
from statistikkprodukt.oracle.utils import executemany_sql

LOGGER = logging.getLogger(__name__)

LOG_TABLE = "ost_logg"

# Loggtabellen har én rad per aggregat per kjøring. Kjøringsfeltene gjentas på hver
# rad, og aggregatets konfigurasjon og metadata lagres slik de var ved kjøringen
# (metadatatabellen overskrives, loggen gjør det ikke).
LOG_COLUMNS = (
    # Kjøringen
    "kjoring_id",
    "starttid",
    "sluttid",
    "status",
    "commit_hash",
    "konfigurasjon",
    "kilde",
    "maal",
    "antall_aggregater",
    "feilmelding",
    # Aggregatet
    "aggregat_id",
    "tabell",
    "navn",
    "navn_en",
    "innhold",
    "innhold_en",
    "obt_tabell",
    "periodisering",
    "inkrementell",
    "formula",
    "prikking",
    "saerskilt",
    "sporring_sql",
    # Resultatet for aggregatet
    "perioder",
    "antall_rader",
    "antall_primaerprikket",
    "antall_prikket",
)


def create_log_table_statement(schema: str) -> str:
    """Lager DDL for den felles loggtabellen ost_logg."""
    table = f"{validate_identifier(schema)}.{LOG_TABLE}"
    return (
        f"CREATE TABLE {table} (\n"
        "  kjoring_id            VARCHAR2(36 CHAR)   NOT NULL,\n"
        "  starttid              TIMESTAMP      NOT NULL,\n"
        "  sluttid               TIMESTAMP      NOT NULL,\n"
        "  status                VARCHAR2(10 CHAR)   NOT NULL,\n"  # OK eller FEILET
        "  db_bruker             VARCHAR2(128 CHAR)  DEFAULT SYS_CONTEXT('USERENV', 'SESSION_USER'),\n"
        "  commit_hash           VARCHAR2(64 CHAR),\n"
        "  konfigurasjon         CLOB CHECK (konfigurasjon IS JSON),\n"
        "  kilde                 VARCHAR2(10 CHAR),\n"
        "  maal                  VARCHAR2(10 CHAR),\n"
        "  antall_aggregater     NUMBER,\n"
        "  feilmelding           CLOB,\n"
        "  aggregat_id           VARCHAR2(5 CHAR)    NOT NULL,\n"
        "  tabell                VARCHAR2(128 CHAR)  NOT NULL,\n"
        "  navn                  VARCHAR2(4000 char),\n"
        "  navn_en               VARCHAR2(4000 char),\n"
        "  innhold               CLOB,\n"
        "  innhold_en            CLOB,\n"
        "  obt_tabell            VARCHAR2(261 CHAR),\n"
        "  periodisering         VARCHAR2(128 CHAR),\n"
        "  inkrementell          VARCHAR2(30 CHAR),\n"
        "  formula               VARCHAR2(4000 CHAR),\n"
        "  prikking              NUMBER(1),\n"
        "  saerskilt             NUMBER(1),\n"
        "  sporring_sql          CLOB,\n"
        "  perioder              CLOB CHECK (perioder IS JSON),\n"
        "  antall_rader          NUMBER,\n"
        "  antall_primaerprikket NUMBER,\n"
        "  antall_prikket        NUMBER,\n"
        "  dvh_sist_oppdatert    TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL\n"
        ")"
    )


def write_log(connection: oracledb.Connection, schema: str, logg: Kjorelogg) -> None:
    """Legger til kjøringens rader uten å opprette eller endre databaseskjemaet."""
    table = f"{validate_identifier(schema)}.{LOG_TABLE}"
    rows = logg.som_rader()
    if not rows:
        LOGGER.debug("Ingen loggrader å skrive.")
        return
    LOGGER.debug("Skriver %d loggrader til %s.", len(rows), table)
    names = ", ".join(LOG_COLUMNS)
    binds = ", ".join(f":{name}" for name in LOG_COLUMNS)
    try:
        with connection.cursor() as cursor:
            cursor.setinputsizes(
                starttid=oracledb.DB_TYPE_TIMESTAMP,
                sluttid=oracledb.DB_TYPE_TIMESTAMP,
                konfigurasjon=oracledb.DB_TYPE_CLOB,
                perioder=oracledb.DB_TYPE_CLOB,
                **{name: oracledb.DB_TYPE_CLOB for name in (
                    "feilmelding", "innhold", "innhold_en", "sporring_sql",
                )},
            )
            executemany_sql(
                cursor, f"INSERT INTO {table} ({names}) VALUES ({binds})", rows
            )
        connection.commit()
    except Exception as exc:
        LOGGER.debug("Feil ved skriving av kjøreloggen; ruller tilbake: %r.", exc)
        connection.rollback()
        if isinstance(exc, oracledb.DatabaseError):
            (error,) = exc.args
            if getattr(error, "code", None) == 942:
                raise RuntimeError(
                    f"Loggtabellen {table} finnes ikke. Kjør "
                    "'statistikkprodukt skjema opprett --db' for å opprette den."
                ) from exc
        raise
    LOGGER.info(
        "Kjøringen er logget i %s med id %s (%d rader).",
        table, logg.kjoring_id, len(rows),
    )
