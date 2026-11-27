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
LOG_COLUMNS = {
    "kjoring_id": "#NAVN Kjøre ID #NAVN_EN Run ID #INNHOLD Unik kjøre ID #INNHOLD_EN Unique run ID",
    "starttid": "#NAVN Starttid #NAVN_EN Start time #INNHOLD Starttidspunkt for kjøringen #INNHOLD_EN Start time for the run",
    "sluttid": "#NAVN Sluttid #NAVN_EN End time #INNHOLD Sluttid for kjøringen #INNHOLD_EN End time for the run",
    "status": "#NAVN Status #NAVN_EN Status #INNHOLD Status for kjøringen #INNHOLD_EN Status for the run",
    "db_bruker": "#NAVN Databasebruker #NAVN_EN Database user #INNHOLD Databasebruker ansvarlig for kjøringen #INNHOLD_EN Database user responsible for the run",
    "commit_hash": "#NAVN Commit hash #NAVN_EN Commit hash #INNHOLD Commit hash for programversjonen som utførte kjøringen #INNHOLD_EN Commit hash of the program version used for the run",
    "python_pakke_versjon": "#NAVN Python-pakkeversjon #NAVN_EN Python package version #INNHOLD Versjon av Python-pakken som utførte kjøringen #INNHOLD_EN Version of the Python package used for the run",
    "r_pakke_versjon": "#NAVN R-pakkeversjon #NAVN_EN R package version #INNHOLD Versjon av R-pakken som ble brukt under kjøringen #INNHOLD_EN Version of the R package used for the run",
    "konfigurasjon": "#NAVN Konfigurasjon #NAVN_EN Configuration #INNHOLD Konfigurasjonen som ble brukt under kjøringen, lagret som JSON #INNHOLD_EN Configuration used for the run, stored as JSON",
    "kilde": "#NAVN Kilde #NAVN_EN Source #INNHOLD Om dataene ble lest fra database eller CSV #INNHOLD_EN Whether data was read from the database or CSV",
    "maal": "#NAVN Mål #NAVN_EN Target #INNHOLD Navn på smalt aggregat #INNHOLD_EN Name of the small aggregat",
    "antall_aggregater": "#NAVN Antall aggregater #NAVN_EN Number of aggregates #INNHOLD Antall smale aggregater som ble kjørt under kjøringen #INNHOLD_EN Number of small aggregates that was run",
    "feilmelding": "#NAVN Feilmelding #NAVN_EN Error message #INNHOLD Feilmelding som oppstod under kjøringen #INNHOLD_EN Error message during the run",
    "aggregat_id": "#NAVN Aggregat ID #NAVN_EN Aggregate ID #INNHOLD Smalt aggergat ID #INNHOLD_EN Small aggregate ID",
    "tabell": "#NAVN Tabell #NAVN_EN Table #INNHOLD Tabellnavnet for det smale aggregatet #INNHOLD_EN Table name for the small aggregate",
    "navn": "#NAVN Tabellnavn DMO #NAVN_EN Table name DMO #INNHOLD Tabellnavn for det smale aggregatet fra metadata #INNHOLD_EN Table name for the small aggregate from metadata",
    "navn_en": "#NAVN Engelsk tabellnavn DMO #NAVN_EN English table name DMO #INNHOLD Engelsk tabellnavn for det smale aggregatet fra metadata #INNHOLD_EN English table name for the small aggregate from metadata",
    "innhold": "#NAVN Innhold DMO #NAVN_EN Content DMO #INNHOLD Beskrivelse av det smale aggregatet #INNHOLD_EN Description of the small aggregate",
    "innhold_en": "#NAVN Engelsk innhold DMO #NAVN_EN English content DMO #INNHOLD Engelsk beskrivelse av det smale aggregatet #INNHOLD_EN English description of the small aggregate",
    "referanse_tabell": "#NAVN Bredt aggergat navn #NAVN_EN Big aggregate name #INNHOLD Navnet på det bredt aggergatet det smale aggregatet bygger på #INNHOLD_EN Name of the big aggregate that the small aggregate is buildt on",
    "periodisering": "#NAVN Periodisering #NAVN_EN Periode #INNHOLD Kolonnen som definerer perioden for det smale aggregatet #INNHOLD_EN Column that defines the period for the small aggregate",
    "inkrementell": "#NAVN Inkrementell #NAVN_EN Incremental #INNHOLD Inkremell kjøremetode #INNHOLD_EN Incremental run method",
    "formula": "#NAVN Formula #NAVN_EN Formula #INNHOLD Prikkeformula som forklarer forholdet mellom kolonner i det smale aggregatet #INNHOLD_EN Suppression formula that explains the relationship between columns in the small aggregate",
    "prikking": "#NAVN Prikking flagg #NAVN_EN Suppression flag #INNHOLD Binær verdi som viser om det smale aggregatet ble prikket eller ikke #INNHOLD_EN Binary value that tells if the small aggregate was suppressed",
    "saerskilt": "#NAVN Særskilt flagg #NAVN_EN Special category flag #INNHOLD Prikkenivået hvor særskilt er sann betyr det høyeste nivå er valgt. Det vanlige nivået vil være usann, hvis prikkeflagget er sann. #INNHOLD_EN Suppression level where special is true means that the highest level is used. If false the normal level is used, if the suppression flag is true.",
    "sporring_sql": "#NAVN SQL spørring #NAVN_EN SQL query #INNHOLD SQL spørring som definerer det smale aggregatet #INNHOLD_EN SQL query that defines the small aggregate",
    "perioder": "#NAVN Perioder #NAVN_EN Periods #INNHOLD Periodene som er blitt kjørt under kjøringen #INNHOLD_EN The periods run",
    "antall_rader": "#NAVN Antall rader #NAVN_EN Number of rows #INNHOLD Antall rader i resultatet for det smale aggregatet #INNHOLD_EN Number of rows in the small aggregate",
    "antall_primaerprikket": "#NAVN Antall primærprikket #NAVN_EN Number of primary suppressed #INNHOLD Antall verdier som ble primærtprikket under kjøringen #INNHOLD_EN Number of rows that was primary suppressed",
    "antall_prikket": "#NAVN Antall prikket #NAVN_EN Number of suppressed #INNHOLD Antall verdier som ble prikket i det smale aggregatet #INNHOLD_EN Number of suppressed values in the small aggregate",
    "dvh_lastet_tid": "#NAVN DVH lastet tidspunkt #NAVN_EN DWH loaded timestamp #INNHOLD Lastetidspunkt for når raden ble skrevet til databasen #INNHOLD_EN Timestamp for when the row was written to the database",
}

LOG_INSERT_COLUMNS = tuple(
    column for column in LOG_COLUMNS if column not in {"dvh_lastet_tid"}
)


def create_log_table_statement(schema: str) -> str:
    """Lager DDL for den felles loggtabellen ost_logg."""
    table = f"{validate_identifier(schema)}.{LOG_TABLE}"
    return (
        f"CREATE TABLE {table} (\n"
        "  kjoring_id            VARCHAR2(36 CHAR)   NOT NULL,\n"
        "  aggregat_id           VARCHAR2(5 CHAR)    NOT NULL,\n"
        "  starttid              TIMESTAMP      NOT NULL,\n"
        "  sluttid               TIMESTAMP      NOT NULL,\n"
        "  status                VARCHAR2(10 CHAR)   NOT NULL,\n"  # OK eller FEILET
        "  tabell                VARCHAR2(128 CHAR)  NOT NULL,\n"
        "  referanse_tabell      VARCHAR2(261 CHAR),\n"
        "  formula               VARCHAR2(4000 CHAR),\n"
        "  inkrementell          VARCHAR2(128 CHAR),\n"
        "  antall_aggregater     NUMBER,\n"
        "  antall_rader          NUMBER,\n"
        "  antall_primaerprikket NUMBER,\n"
        "  antall_prikket        NUMBER,\n"
        "  periodisering         VARCHAR2(128 CHAR),\n"
        "  kilde                 VARCHAR2(10 CHAR),\n"
        "  maal                  VARCHAR2(10 CHAR),\n"
        "  db_bruker             VARCHAR2(128 CHAR),\n"
        "  commit_hash           VARCHAR2(64 CHAR),\n"
        "  python_pakke_versjon  VARCHAR2(128 CHAR),\n"
        "  r_pakke_versjon       VARCHAR2(128 CHAR),\n"
        "  navn                  VARCHAR2(4000 char),\n"
        "  navn_en               VARCHAR2(4000 char),\n"
        "  innhold               CLOB,\n"
        "  innhold_en            CLOB,\n"
        "  prikking              NUMBER(1),\n"
        "  saerskilt             NUMBER(1),\n"
        "  sporring_sql          CLOB,\n"
        "  feilmelding           CLOB,\n"
        "  perioder              CLOB CHECK (perioder IS JSON),\n"
        "  konfigurasjon         CLOB CHECK (konfigurasjon IS JSON),\n"
        "  dvh_lastet_tid        DATE DEFAULT SYSDATE NOT NULL\n"
        ")"
    )


def log_comment_statements(schema: str) -> list[str]:
    """Lager Oracle-setninger for kommentarer på loggtabellens kolonner."""
    table = f"{validate_identifier(schema)}.{LOG_TABLE}"
    statements = []
    for column, comment in LOG_COLUMNS.items():
        escaped_comment = comment.replace("'", "''")
        statements.append(
            f"COMMENT ON COLUMN {table}.{validate_identifier(column)} IS "
            f"'{escaped_comment}'"
        )
    return statements


def write_log(connection: oracledb.Connection, schema: str, logg: Kjorelogg) -> None:
    """Legger til kjøringens rader uten å opprette eller endre databaseskjemaet."""
    table = f"{validate_identifier(schema)}.{LOG_TABLE}"
    rows = logg.som_rader()
    if not rows:
        LOGGER.debug("Ingen loggrader å skrive.")
        return
    LOGGER.debug("Skriver %d loggrader til %s.", len(rows), table)
    names = ", ".join(LOG_INSERT_COLUMNS)
    binds = ", ".join(f":{name}" for name in LOG_INSERT_COLUMNS)
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
