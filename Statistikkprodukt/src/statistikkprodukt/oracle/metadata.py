from __future__ import annotations

import getpass
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from importlib.metadata import PackageNotFoundError, version
from typing import Any
from uuid import uuid4

import oracledb
from oracledb import Cursor
from statistikkprodukt.config import Aggregate, Metadata, validate_identifier
from statistikkprodukt.oracle.operations import table_exists

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

META_COLUMNS = (
    "nav_id",
    "tabell",
    "navn",
    "navn_en",
    "innhold",
    "innhold_en",
    "periodisering",
)

def create_meta_table_statement(schema: str, table_name: str) -> str:
    """Lager DDL for metadatatabellen ({tabell}_meta) med faste kolonner."""
    table = f"{validate_identifier(schema)}.{validate_identifier(table_name)}"
    return (
        f"CREATE TABLE {table} (\n"
        "  nav_id        VARCHAR2(10)   NOT NULL,\n"
        "  tabell        VARCHAR2(128)  NOT NULL,\n"
        "  navn          VARCHAR2(4000),\n"
        "  navn_en       VARCHAR2(4000),\n"
        "  innhold       VARCHAR2(4000),\n"
        "  innhold_en    VARCHAR2(4000),\n"
        "  periodisering VARCHAR2(128),\n"
        "  dvh_sist_oppdatert     TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL\n"
        ")"
    )


def metadata_rows(aggregate: SmallAggregate, metadata: TableMetadata) -> list[dict]:
    """Bygger radene i metadatatabellen: én for tabellen og én per kolonne.

    Aggregatfeltene fra YAML gjentas på hver rad, slik at hver rad kan leses alene.
    """
    common = {
        "nav_id": aggregate.nav_id,
        "tabell": aggregate.tabell,
        "periodisering": aggregate.periodisering,
    }

def write_metadata_table(
        connection: oracledb.Connection,
        schema: str,
        aggregate: SmallAggregate,
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
            cursor.executemany(f"INSERT INTO {table} ({names}) VALUES ({binds})", rows)
        connection.commit()
    except oracledb.DatabaseError as exc:
        connection.rollback()
        (error,) = exc.args
        if getattr(error, "code", None) == 942:  # ORA-00942: tabellen finnes ikke
            raise RuntimeError(
                f"Metadatatabellen {table} finnes ikke. Kjør "
                "'statistikkprodukt skjema opprett' for å opprette den."
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

#TODO: subject_kort må enten legges til i yml eller hentes fra navnet på tabellen.
LOG_TABLE = "ost_{subject_kort}_logg"

_MAX_BYTES = 4000

LOGG_COLUMNS = (
    "nav_id",
    "tabell",
    "objekttype",
    "kolonne",
    "rekkefolge",
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
    "kjoring_id",
    "starttid",
    "sluttid",
    "status",
    "os_bruker",
    "programversjon",
    "konfigurasjon",
    "kilde",
    "maal",
    "antall_aggregater",
    "aggregater",
    "nav_ider",
    "antall_rader",
    "antall_prikket",
    "detaljer",
    "feilmelding",
)






def logg_rows(aggregate: SmallAggregate, metadata: TableMetadata) -> list[dict]:
    """Bygger rad i loggtabellen: én per kjøring.
    """
    common = {
        "nav_id": aggregate.nav_id,
        "tabell": aggregate.tabell,
        "obt_tabell": aggregate.obt_tabell,
        "periodisering": aggregate.periodisering,
        "inkrementell": str(aggregate.inkrementell),
        "formula": aggregate.formula,
        "prikking": int(aggregate.prikking),
        "saerskilt": int(aggregate.saerskilt),
    }

@dataclass
class AggregatResultat:
    """Hva som ble gjort med ett småaggregat i en kjøring."""
    tabell: str
    nav_id: str
    perioder: list[Any]
    antall_rader: int
    antall_primaerprikket: int
    antall_prikket: int


@dataclass
class Kjorelogg:
    """Samler informasjon om én kjøring frem til den skrives som én rad."""
    konfigurasjon: str
    kilde: str
    maal: str
    aggregater: list[tuple[str, str]]  # (tabell, nav_id) for alle valgte aggregater
    kjoring_id: str = field(default_factory=lambda: str(uuid4()))
    starttid: datetime = field(default_factory=datetime.now)
    sluttid: datetime | None = None
    status: str = "PÅGÅR"
    feilmelding: str | None = None
    resultater: list[AggregatResultat] = field(default_factory=list)

    def fullfor(self, feil: BaseException | None = None) -> None:
        """Setter sluttid og status (OK eller FEILET)."""
        self.sluttid = datetime.now()
        self.status = "FEILET" if feil else "OK"
        self.feilmelding = str(feil) if feil else None

    def som_rad(self) -> dict[str, Any]:
        """Gjør loggen om til bindverdier for INSERT."""
        return {
            "kjoring_id": self.kjoring_id,
            "starttid": self.starttid,
            "sluttid": self.sluttid or datetime.now(),
            "status": self.status,
            "os_bruker": _os_user(),
            "programversjon": _program_version(),
            "konfigurasjon": _truncate(self.konfigurasjon),
            "kilde": self.kilde,
            "maal": self.maal,
            "antall_aggregater": len(self.aggregater),
            "aggregater": _truncate(", ".join(tabell for tabell, _ in self.aggregater)),
            "nav_ider": _truncate(", ".join(nav_id for _, nav_id in self.aggregater)),
            "antall_rader": sum(r.antall_rader for r in self.resultater),
            "antall_prikket": sum(r.antall_prikket for r in self.resultater),
            "detaljer": json.dumps(
                [asdict(r) for r in self.resultater], ensure_ascii=False, default=str
            ),
            "feilmelding": _truncate(self.feilmelding),
        }

def create_logg_table_statement(schema: str, table_name: str) -> str:
    """Lager DDL for loggtabellen (ost_{subject_kort}_logg) med faste kolonner."""
    table = f"{validate_identifier(schema)}.{validate_identifier(table_name)}"
    return (
        f"CREATE TABLE {table} (\n"
        "  nav_id        VARCHAR2(10)   NOT NULL,\n"
        "  tabell        VARCHAR2(128)  NOT NULL,\n"
        "  objekttype    VARCHAR2(20)   NOT NULL,\n"  # tabell, variabel eller tallkolonne
        "  kolonne       VARCHAR2(128),\n"  # NULL for raden som beskriver tabellen
        "  rekkefolge    NUMBER(4)      NOT NULL,\n"
        "  navn          VARCHAR2(4000),\n"
        "  navn_en       VARCHAR2(4000),\n"
        "  innhold       VARCHAR2(4000),\n"
        "  innhold_en    VARCHAR2(4000),\n"
        "  obt_tabell    VARCHAR2(261),\n"
        "  periodisering VARCHAR2(128),\n"
        "  inkrementell  VARCHAR2(30),\n"
        "  formula       VARCHAR2(4000),\n"
        "  prikking      NUMBER(1),\n"
        "  saerskilt     NUMBER(1),\n"
        "  kjoring_id        VARCHAR2(36)   NOT NULL,\n"
        "  starttid          TIMESTAMP      NOT NULL,\n"
        "  sluttid           TIMESTAMP      NOT NULL,\n"
        "  status            VARCHAR2(10)   NOT NULL,\n"  # OK eller FEILET
        "  db_bruker         VARCHAR2(128)  DEFAULT SYS_CONTEXT('USERENV', 'SESSION_USER'),\n"
        "  os_bruker         VARCHAR2(128),\n"
        "  programversjon    VARCHAR2(50),\n"
        "  konfigurasjon     VARCHAR2(4000),\n"
        "  kilde             VARCHAR2(10),\n"
        "  maal              VARCHAR2(10),\n"
        "  antall_aggregater NUMBER,\n"
        "  aggregater        VARCHAR2(4000),\n"
        "  nav_ider          VARCHAR2(4000),\n"
        "  antall_rader      NUMBER,\n"
        "  antall_prikket    NUMBER,\n"
        "  detaljer          CLOB,\n"  # JSON med perioder og antall per aggregat
        "  feilmelding       VARCHAR2(4000)\n"
        "  dvh_sist_oppdatert     TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL\n"
        ")"
    )

def ensure_log_table(connection: oracledb.Connection, schema: str) -> None:
    """Oppretter loggtabellen i skjemaet hvis den ikke finnes."""
    if table_exists(connection, schema, LOG_TABLE):
        return
    try:
        with connection.cursor() as cursor:
            cursor.execute(create_log_table_statement(schema))
        LOGGER.info("Opprettet loggtabellen %s.%s.", schema, LOG_TABLE)
    except oracledb.DatabaseError as exc:
        (error,) = exc.args
        if getattr(error, "code", None) != 955:  # ORA-00955: finnes allerede
            raise


def write_log(connection: oracledb.Connection, schema: str, logg: Kjorelogg) -> None:
    """Legger til én rad i loggtabellen i en egen transaksjon."""
    table = f"{validate_identifier(schema)}.{LOG_TABLE}"
    names = ", ".join(LOG_COLUMNS)
    binds = ", ".join(f":{name}" for name in LOG_COLUMNS)
    # En feilet kjøring kan ha etterlatt en åpen transaksjon; den skal ikke følge med loggraden.
    connection.rollback()
    ensure_log_table(connection, schema)
    with connection.cursor() as cursor:
        cursor.setinputsizes(detaljer=oracledb.DB_TYPE_CLOB)
        cursor.execute(f"INSERT INTO {table} ({names}) VALUES ({binds})", logg.som_rad())
    connection.commit()
    LOGGER.info("Kjøringen er logget i %s med id %s.", table, logg.kjoring_id)


def _truncate(text: str | None, max_bytes: int = _MAX_BYTES) -> str | None:
    """Kutter tekst slik at den får plass i VARCHAR2(4000) (bytes, ikke tegn)."""
    if text is None:
        return None
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[: max_bytes - 3].decode("utf-8", errors="ignore") + "..."


def _os_user() -> str | None:
    try:
        return getpass.getuser()
    except Exception:  # f.eks. i containere uten brukernavn
        return None


def _program_version() -> str | None:
    try:
        return version("statistikkprodukt")
    except PackageNotFoundError:
        return None
