"""Oversetter aggregatmetadata til radformat og skriver dem til Oracle."""

from __future__ import annotations

import logging
import re

import oracledb
from statistikkprodukt.app.models import TableMetadata
from statistikkprodukt.config import Aggregate, validate_identifier
from statistikkprodukt.oracle.utils import execute_sql, executemany_sql

LOGGER = logging.getLogger(__name__)

META_COLUMNS = ("type", "keyword", "scope", "language", "value")
# Oversatte felt gir separate NO-/EN-rader fra feltet og feltets _en-variant.
PX_TRANSLATED_FIELDS = (
    ("tema", "SUBJECT"),
    ("merknad", "NOTE"),
    ("fremhevet_merknad", "NOTEX"),
)
# Språkuavhengige felt gir én felles rad med LANGUAGE=NULL.
PX_COMMON_FIELDS = (
    ("tema_kode", "SUBJECT_KORT"),
    ("fagomraade_kode", "SUBJECT-CODE"),
    ("avsluttet", "DISCONTINUED"),
    ("offisiell_statistikk", "OFFICIAL-STATISTICS"),
)


def create_meta_table_statement(schema: str, table_name: str) -> str:
    """Lager DDL for PX-metadata med gruppering, nøkkelord og språk."""
    table = f"{validate_identifier(schema)}.{validate_identifier(table_name)}"
    return (
        f"CREATE TABLE {table} (\n"
        "  type               CHAR(2)             NOT NULL,\n"
        "  keyword            VARCHAR2(256 CHAR)  NOT NULL,\n"
        "  scope              VARCHAR2(256 CHAR),\n"
        "  language           CHAR(2),\n"
        "  value              VARCHAR2(4000 CHAR),\n"
        "  dvh_lastet_tid DATE DEFAULT SYSDATE NOT NULL\n"
        ")"
    )


def _column_name(column: str) -> str:
    """Lowercase kolonnenavnet og fjerner eventuelt __no-/__en-suffiks."""
    return re.sub(r"__(no|en)$", "", column.lower())


class _Rows:
    """Samler bindverdier for metadatarader"""

    def __init__(self) -> None:
        """Oppretter en tom samling metadatarader."""
        self.rows: list[dict] = []

    def add(
        self, row_type: str, keyword: str, value: str | int | bool | None,
        language: str | None = None,
    ) -> None:
        """Legger til en tekstrad; hopper over None og oversetter bool til YES/NO.

        Avviser verdier over 4000 UTF-8-byte før metadatatabellen endres.
        """
        if value is None:
            return
        text = ("YES" if value else "NO") if isinstance(value, bool) else str(value)
        if len(text.encode("utf-8")) > 4000:
            raise ValueError(
                f"Metadata for {row_type}/{keyword} overskrider 4000 byte i UTF-8."
            )
        self.rows.append(dict(
            type=row_type, keyword=keyword, scope=None, language=language, value=text
        ))

    def translated(self, row_type: str, keyword: str, source, field: str) -> None:
        """Legger til NO-/EN-rader fra field og field_en når verdiene finnes."""
        for language, suffix in (("NO", ""), ("EN", "_en")):
            self.add(row_type, keyword, getattr(source, field + suffix), language)


def _px_rows(rows: _Rows, aggregate: Aggregate, metadata: TableMetadata) -> None:
    """Mapper tabellmetadata til PX-nøkkelord og én CONTACT-rad per kontakt."""
    rows.add("PX", "MATRIX", aggregate.id)
    rows.translated("PX", "TITLE", metadata.table, "navn")
    rows.translated("PX", "CONTENTS", metadata.table, "innhold")

    settings = aggregate.statistikkbank
    rows.add("PX", "SUBJECT-AREA", settings.fagomraade, "NO")
    for field, keyword in PX_TRANSLATED_FIELDS:
        rows.translated("PX", keyword, settings, field)
    for field, keyword in PX_COMMON_FIELDS:
        rows.add("PX", keyword, getattr(settings, field))

    for contact in settings.kontakter:
        value = "#".join((
            contact.seksjon, "", contact.telefonnummer or "", contact.epost
        ))
        rows.add("PX", "CONTACT", value)


def _cs_rows(rows: _Rows, aggregate: Aggregate) -> None:
    """Angir dimensjonsrekkefølge, tidskolonne og måltall uten språk.

    Variabler legges i STUB og periodisering i HEADING og TIMEVAL.
    DATA bruker kolonne#desimaler-lagring#unit_kolonne når feltene finnes.
    """
    rows.add("CS", "STUB", ",".join(dict.fromkeys(
        _column_name(variable.kolonne) for variable in aggregate.variabler
    )))
    rows.add("CS", "HEADING", _column_name(aggregate.periodisering.kolonne))
    rows.add("CS", "TIMEVAL", _column_name(aggregate.periodisering.kolonne))

    data = []
    for measure in aggregate.tellepunkter:
        column = _column_name(measure.kolonne)
        precision = measure.statistikkbank.desimaler_lagring
        unit = f"unit_{column}" if measure.enhet or measure.enhet_en else None
        parts = [column]
        if precision is not None:
            parts.append(str(precision))
        if unit is not None:
            parts.append(unit)
        data.append("#".join(parts))

    # Eksempel:
    #   DATA = "tellepunkt#2#unit_tellepunkt,antall#0,andel#1#unit_andel"
    rows.add("CS", "DATA", ",".join(dict.fromkeys(data)))


def _cr_rows(rows: _Rows, aggregate: Aggregate, metadata: TableMetadata) -> None:
    """Legger til kolonnenavn, måleenheter og språkfri visningspresisjon.
    """
    for variable in (
        aggregate.periodisering, *aggregate.variabler, *aggregate.tellepunkter
    ):
        # Ikke støtt flere språkvarianter av samme kolonne i CR enda - trenger mer definisjonsarbeid.
        column = _column_name(variable.kolonne)
        if column != variable.kolonne:
            raise NotImplementedError(
                f"Kolonnenavn {variable.kolonne} med __no-/__en suffiks er ikke implementert enda")

        variable_metadata = metadata.columns.get(column)
        rows.add("CR", column, variable_metadata.navn, "NO")
        if value := getattr(variable_metadata, "navn_en", None):
            rows.add("CR", column, value, "EN")

    for tellepunkt in aggregate.tellepunkter:
        column = _column_name(tellepunkt.kolonne)
        fields = (
            (f"unit_{column}", "NO", tellepunkt.enhet),
            (f"unit_{column}", "EN", tellepunkt.enhet_en),
            (f"precision_{column}", None, tellepunkt.statistikkbank.desimaler_visning),
        )
        for keyword, language, value in fields:
            if value is not None:
                rows.add("CR", keyword, value, language)


def _sq_rows(rows: _Rows, aggregate: Aggregate) -> None:
    """Angir startvisning med STAT_VAR og valgfrie antall per dimensjon."""
    rows.add("SQ", "STAT_VAR", aggregate.statistikkbank.startvisning_antall_tellepunkter)
    for variable in (aggregate.periodisering, *aggregate.variabler):
        column = _column_name(variable.kolonne)
        if column != variable.kolonne:
            raise NotImplementedError(
                f"Kolonnenavn {variable.kolonne} med __no-/__en suffiks er ikke implementert enda")

        count = variable.statistikkbank.startvisning_antall_verdier
        if count is not None:
            rows.add("SQ", column, count)


def metadata_rows(aggregate: Aggregate, metadata: TableMetadata) -> list[dict]:
    """Oversetter konfigurasjon og løste metadata til PX-, CS-, CR- og SQ-rader."""
    rows = _Rows()
    _px_rows(rows, aggregate, metadata)
    _cs_rows(rows, aggregate)
    _cr_rows(rows, aggregate, metadata)
    _sq_rows(rows, aggregate)
    return rows.rows


def write_metadata_table(
    connection: oracledb.Connection,
    schema: str,
    aggregate: Aggregate,
    metadata: TableMetadata,
) -> None:
    """Overskriver hele {tabell}_meta med gjeldende metadata i én transaksjon."""
    table = f"{validate_identifier(schema)}.{validate_identifier(aggregate.meta_tabell)}"
    rows = metadata_rows(aggregate, metadata)
    LOGGER.debug("Skriver %d metadatarader til %s.", len(rows), table)
    names = ", ".join(META_COLUMNS)
    binds = ", ".join(f":{name}" for name in META_COLUMNS)
    try:
        with connection.cursor() as cursor:
            # DELETE i stedet for TRUNCATE: TRUNCATE er DDL og committer implisitt.
            execute_sql(cursor, f"DELETE FROM {table}")
            executemany_sql(
                cursor, f"INSERT INTO {table} ({names}) VALUES ({binds})", rows
            )
        connection.commit()
    except Exception as exc:
        LOGGER.debug("Feil ved skriving av %s; ruller tilbake: %r.", table, exc)
        connection.rollback()
        if (
            isinstance(exc, oracledb.DatabaseError)
            and exc.args
            and getattr(exc.args[0], "code", None) == 942
        ):
            raise RuntimeError(
                f"Metadatatabellen {table} finnes ikke. Kjør "
                "'statistikkprodukt skjema opprett --db' for å opprette den."
            ) from exc
        raise
    LOGGER.info("Overskrev %s med %d rader.", table, len(rows))
