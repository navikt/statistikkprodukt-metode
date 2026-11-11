from __future__ import annotations

import getpass
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from typing import Any
from uuid import uuid4

import oracledb
from oracledb import Cursor
from statistikkprodukt.config import Aggregate, Metadata, validate_identifier

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

# Metadatatabellen er på radformat: én rad per felt, med feltnavnet i "felt"
# og innholdet i "verdi". dvh_sist_oppdatert settes av databasen.
META_FIELDS = (
    "nav_id",
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
        "  felt               VARCHAR2(128)  NOT NULL,\n"
        "  verdi              VARCHAR2(4000),\n"
        "  dvh_sist_oppdatert TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL\n"
        ")"
    )


def metadata_rows(aggregate: Aggregate, metadata: TableMetadata) -> list[dict]:
    """Bygger radene i metadatatabellen: én rad per felt i META_FIELDS."""
    values = {
        "nav_id": aggregate.id,
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

def log_table(aggregate: Aggregate) -> str:
    """Navnet på loggtabellen for aggregatets emne: ost_{emne}_logg.

    Emnet er andre ledd i tabellnavnet, f.eks. ost_befolkning_alder -> ost_befolkning_logg.
    """
    parts = aggregate.tabell.split("_")
    if len(parts) < 3 or parts[0] != "ost":
        raise ValueError(
            f"Kan ikke finne emnet for loggtabellen fra tabellnavnet {aggregate.tabell!r}; "
            "forventet formen ost_{emne}_{navn}."
        )
    return f"ost_{parts[1]}_logg"

_MAX_BYTES = 4000

# Loggtabellen har én rad per aggregat per kjøring. Kjøringsfeltene gjentas på hver
# rad, og aggregatets konfigurasjon og metadata lagres slik de var ved kjøringen
# (metadatatabellen overskrives, loggen gjør det ikke).
LOG_COLUMNS = (
    # Kjøringen
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
    "feilmelding",
    # Aggregatet
    "nav_id",
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


@dataclass
class AggregatResultat:
    """Hva som ble gjort med ett aggregat i en kjøring."""
    nav_id: str
    perioder: list[Any]
    antall_rader: int
    antall_primaerprikket: int
    antall_prikket: int


@dataclass
class Kjorelogg:
    """Samler informasjon om én kjøring frem til den skrives til loggtabellen."""
    konfigurasjon: str
    kilde: str
    maal: str
    aggregater: list[Aggregate]  # alle valgte aggregater
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

    def som_rader(self) -> list[tuple[Aggregate, dict[str, Any]]]:
        """Gjør loggen om til bindverdier for INSERT, én rad per aggregat.

        Aggregater uten resultat (f.eks. ved feil) får tomme resultatfelt.
        """
        kjoring = {
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
            "feilmelding": _truncate(self.feilmelding),
        }
        resultater = {resultat.nav_id: resultat for resultat in self.resultater}
        return [
            (
                aggregate,
                {
                    **kjoring,
                    **_aggregat_felt(aggregate),
                    **_resultat_felt(resultater.get(aggregate.id)),
                },
            )
            for aggregate in self.aggregater
        ]


def _aggregat_felt(aggregate: Aggregate) -> dict[str, Any]:
    """Aggregatets konfigurasjon og metadata slik de var ved kjøringen."""
    return {
        "nav_id": aggregate.id,
        "tabell": aggregate.tabell,
        "navn": _truncate(aggregate.navn),
        "navn_en": _truncate(aggregate.navn_en),
        "innhold": _truncate(aggregate.innhold),
        "innhold_en": _truncate(aggregate.innhold_en),
        "obt_tabell": aggregate.obt_tabell,
        "periodisering": aggregate.periodisering.kolonne,
        "inkrementell": str(aggregate.inkrementell_strategi),
        "formula": _truncate(aggregate.formula),
        "prikking": int(aggregate.prikking),
        "saerskilt": int(aggregate.saerskilt),
        "sporring_sql": aggregate.sporring_sql,
    }


def _resultat_felt(resultat: AggregatResultat | None) -> dict[str, Any]:
    """Hva kjøringen gjorde med aggregatet; tomt hvis aggregatet ikke ble ferdig."""
    if resultat is None:
        return dict.fromkeys(
            ("perioder", "antall_rader", "antall_primaerprikket", "antall_prikket")
        )
    return {
        "perioder": _truncate(", ".join(str(periode) for periode in resultat.perioder)),
        "antall_rader": resultat.antall_rader,
        "antall_primaerprikket": resultat.antall_primaerprikket,
        "antall_prikket": resultat.antall_prikket,
    }


def create_log_table_statement(schema: str, table_name: str) -> str:
    """Lager DDL for loggtabellen (ost_{emne}_logg) med faste kolonner."""
    table = f"{validate_identifier(schema)}.{validate_identifier(table_name)}"
    return (
        f"CREATE TABLE {table} (\n"
        "  kjoring_id            VARCHAR2(36)   NOT NULL,\n"
        "  starttid              TIMESTAMP      NOT NULL,\n"
        "  sluttid               TIMESTAMP      NOT NULL,\n"
        "  status                VARCHAR2(10)   NOT NULL,\n"  # OK eller FEILET
        "  db_bruker             VARCHAR2(128)  DEFAULT SYS_CONTEXT('USERENV', 'SESSION_USER'),\n"
        "  os_bruker             VARCHAR2(128),\n"
        "  programversjon        VARCHAR2(50),\n"
        "  konfigurasjon         VARCHAR2(4000),\n"
        "  kilde                 VARCHAR2(10),\n"
        "  maal                  VARCHAR2(10),\n"
        "  antall_aggregater     NUMBER,\n"
        "  feilmelding           VARCHAR2(4000),\n"
        "  nav_id                VARCHAR2(10)   NOT NULL,\n"
        "  tabell                VARCHAR2(128)  NOT NULL,\n"
        "  navn                  VARCHAR2(4000),\n"
        "  navn_en               VARCHAR2(4000),\n"
        "  innhold               VARCHAR2(4000),\n"
        "  innhold_en            VARCHAR2(4000),\n"
        "  obt_tabell            VARCHAR2(261),\n"
        "  periodisering         VARCHAR2(128),\n"
        "  inkrementell          VARCHAR2(30),\n"
        "  formula               VARCHAR2(4000),\n"
        "  prikking              NUMBER(1),\n"
        "  saerskilt             NUMBER(1),\n"
        "  sporring_sql          CLOB,\n"
        "  perioder              VARCHAR2(4000),\n"
        "  antall_rader          NUMBER,\n"
        "  antall_primaerprikket NUMBER,\n"
        "  antall_prikket        NUMBER,\n"
        "  dvh_sist_oppdatert    TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL\n"
        ")"
    )


def ensure_log_table(connection: oracledb.Connection, schema: str, table_name: str) -> None:
    """Oppretter loggtabellen i skjemaet hvis den ikke finnes."""
    try:
        with connection.cursor() as cursor:
            cursor.execute(create_log_table_statement(schema, table_name))
        LOGGER.info("Opprettet loggtabellen %s.%s.", schema, table_name)
    except oracledb.DatabaseError as exc:
        (error,) = exc.args
        if getattr(error, "code", None) != 955:  # ORA-00955: finnes allerede
            raise


def write_log(connection: oracledb.Connection, schema: str, logg: Kjorelogg) -> None:
    """Legger til kjøringens rader i loggtabellene i en egen transaksjon.

    Hvert aggregat logges i loggtabellen for sitt emne (se log_table).
    """
    rows_per_table: dict[str, list[dict[str, Any]]] = {}
    for aggregate, row in logg.som_rader():
        rows_per_table.setdefault(log_table(aggregate), []).append(row)

    names = ", ".join(LOG_COLUMNS)
    binds = ", ".join(f":{name}" for name in LOG_COLUMNS)
    # En feilet kjøring kan ha etterlatt en åpen transaksjon; den skal ikke følge med loggradene.
    connection.rollback()
    for table_name in rows_per_table:
        ensure_log_table(connection, schema, table_name)
    with connection.cursor() as cursor:
        for table_name, rows in rows_per_table.items():
            table = f"{validate_identifier(schema)}.{validate_identifier(table_name)}"
            cursor.setinputsizes(sporring_sql=oracledb.DB_TYPE_CLOB)
            cursor.executemany(f"INSERT INTO {table} ({names}) VALUES ({binds})", rows)
            LOGGER.info(
                "Kjøringen er logget i %s med id %s (%d rader).",
                table, logg.kjoring_id, len(rows),
            )
    connection.commit()


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
