from __future__ import annotations

import logging
import re

import oracledb
from oracledb import Cursor
from statistikkprodukt.app_models import Kjorelogg, TableMetadata
from statistikkprodukt.config import Aggregate, Metadata, validate_identifier

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


def read_metadata(
        connection: oracledb.Connection,
        schema: str,
        aggregate: Aggregate
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
        for variable in [
            aggregate.periodisering,
            *aggregate.variabler,
            *aggregate.tellepunkter,
        ]:
            column = variable.kolonne
            db_metadata = _fetch_column_metadata(
                owner, table_name, column, cursor
            )
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
    for tag in Metadata.model_fields.keys():
        value = getattr(config_metadata, tag)
        if value:
            LOGGER.info("%s.%s hentes fra konfigurasjonen.", column, tag)
        elif db_value := getattr(db_metadata, tag):
            value = db_value
            LOGGER.info("%s.%s hentes fra databasekommentar.", column, tag)
        else:
            raise ValueError(
                f"Metadatafeltet {tag} mangler for {column}; angi det i YAML."
            )
        LOGGER.debug("%s.%s = %s", column, tag, value)
        setattr(result, tag, value)

    return result

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
            cursor.execute(f"DELETE FROM {table}")
            cursor.setinputsizes(verdi=oracledb.DB_TYPE_CLOB)
            cursor.executemany(f"INSERT INTO {table} ({names}) VALUES ({binds})", rows)
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
        return
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
            cursor.executemany(f"INSERT INTO {table} ({names}) VALUES ({binds})", rows)
        connection.commit()
    except Exception as exc:
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
