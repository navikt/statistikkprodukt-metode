"""Oversetter aggregatmetadata til radformat og skriver dem til Oracle."""

from __future__ import annotations

import logging
import re
from datetime import datetime

import oracledb

from statistikkprodukt.app.models import TableMetadata
from statistikkprodukt.config import Aggregate, validate_identifier
from statistikkprodukt.oracle.aggregate_table import read_aggregate_last_updated
from statistikkprodukt.oracle.utils import execute_sql, executemany_sql

LOGGER = logging.getLogger(__name__)

META_COLUMNS = ("type", "keyword", "scope", "language", "value")


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


def _normalize_column_name(column: str) -> str:
    return column.lower()


def _column_name(column: str) -> str:
    """Lowercase kolonnenavnet og fjerner eventuelt __no-/__en-suffiks."""
    return re.sub(r"__(no|en)$", "", _normalize_column_name(column))


class _Rows:
    """Samler bindverdier for metadatarader"""

    def __init__(self) -> None:
        """Oppretter en tom samling metadatarader."""
        self.rows: list[dict] = []

    def add(
            self, row_type: str, keyword: str, value: str | int | None,
            language: str | None,
    ) -> None:
        """Legger til en tekstrad; hopper over None.

        Avviser verdier over 4000 UTF-8-byte før metadatatabellen endres.
        """
        if value is None:
            return
        text = str(value)
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
            value = getattr(source, field + suffix, None)
            if value is not None:
                self.add(row_type, keyword, value, language)


def _px_rows(rows: _Rows, aggregate: Aggregate, metadata: TableMetadata, aggregate_last_updated: datetime|None) -> None:
    """Mapper tabellmetadata til PX-nøkkelord og én CONTACT-rad per kontakt."""
    rows.add("PX", "MATRIX", aggregate.id, language="NO")
    rows.translated("PX", "TITLE", metadata.table, "navn")
    rows.translated("PX", "CONTENTS", metadata.table, "innhold")

    statistikkbank_config = aggregate.statistikkbank
    rows.translated("PX", "SUBJECT", statistikkbank_config, "subject")
    rows.translated("PX", "NOTE", statistikkbank_config, "note")
    rows.translated("PX", "NOTEX", statistikkbank_config, "notex")
    rows.add("PX", "SUBJECT-AREA", statistikkbank_config.subject_area, language="NO")
    rows.add("PX", "SUBJECT-CODE", statistikkbank_config.subject_code, language="NO")
    rows.add("PX", "DISCONTINUED", statistikkbank_config.discontinued, None)
    rows.add("PX", "OFFICIAL-STATISTICS", statistikkbank_config.official_statistics, None)
    last_updated_str = "" if aggregate_last_updated is None else aggregate_last_updated.strftime("%Y-%m-%d %H:%M:%S")
    rows.add("PX", "LAST-UPDATED", last_updated_str, None)

    for contact in statistikkbank_config.ekstra_kontakter:
        parts = [
            contact.navn,
            contact.beskrivelse or "",
            contact.telefonnummer or "",
            contact.epost
        ]
        for part in parts:
            if "#" in part:
                raise ValueError(f"Metadata for kontakt {contact.navn} skal ikke inneholde '#': {part}")
        value = "#".join(parts)
        rows.add("PX", "CONTACT", value, language="NO")


def _cs_rows(rows: _Rows, aggregate: Aggregate) -> None:
    """Mapper metadata for variabler og tellepunkter til CS-rader"""
    stub_columns = [_column_name(variable.kolonne) for variable in aggregate.variabler]
    stub_columns.append(_column_name(aggregate.periodisering.kolonne))
    rows.add("CS", "STUB", ",".join(_column_name(column) for column in stub_columns), None)
    rows.add("CS", "TIMEVAL", _column_name(aggregate.periodisering.kolonne), None)

    data_by_tellepunkt = []
    for measure in aggregate.tellepunkter:
        column = _column_name(measure.kolonne)
        precision = measure.statistikkbank.precision
        unit = f"unit_{column}"

        # data should not be localized
        parts = [
            column,
            str(precision) if precision is not None else "",
            unit if measure.enhet else "",
        ]
        for part in parts:
            if "#" in part or "," in part:
                raise ValueError(f"Metadata for tellepunkt {measure.kolonne} skal ikke inneholde '#' eller ',': {part}")

        row_value = "#".join(parts)
        data_by_tellepunkt.append(row_value)

    # Eksempel:
    #   DATA = "tellepunkt#2#unit_tellepunkt,antall##,andel#1#unit_andel"
    rows.add("CS", "DATA", ",".join(data_by_tellepunkt), None)


def _cr_rows(rows: _Rows, aggregate: Aggregate, metadata: TableMetadata) -> None:
    """Mapper metadata for verdier til CR-rader"""
    rows.add("CR", "STAT_VAR", "statistikkvariabel", "NO")
    rows.add("CR", "STAT_VAR", "statistical variable", "EN")

    for variable in (
            aggregate.periodisering, *aggregate.variabler, *aggregate.tellepunkter
    ):
        # Ikke støtt flere språkvarianter av samme kolonne via lang suffiks i CR enda - trenger mer definisjonsarbeid.
        # _column_name fjerner __no-/__en-suffiks
        column_raw_lower = variable.kolonne.lower()
        if column_raw_lower.endswith("__en") or column_raw_lower.endswith("__no"):
            raise NotImplementedError(
                f"Kolonnenavn {variable.kolonne} med __no-/__en suffiks er ikke implementert enda")

        column = _column_name(variable.kolonne)
        variable_metadata = metadata.columns.get(column)
        rows.add("CR", column, variable_metadata.navn, "NO")
        if value := getattr(variable_metadata, "navn_en", None):
            rows.add("CR", column, value, "EN")

    for tellepunkt in aggregate.tellepunkter:
        column = _column_name(tellepunkt.kolonne)
        rows.translated("CR", f"unit_{column}", tellepunkt, "enhet")


def _sq_rows(rows: _Rows, aggregate: Aggregate) -> None:
    """Angir startvisning med STAT_VAR og valgfrie antall per dimensjon."""
    source_query_value = aggregate.statistikkbank.sq
    rows.add("SQ", "STAT_VAR", source_query_value, None)

    for variable in (aggregate.periodisering, *aggregate.variabler):
        column = _column_name(variable.kolonne)
        column_raw_lower = variable.kolonne.lower()
        if column_raw_lower.endswith("__en") or column_raw_lower.endswith("__no"):
            raise NotImplementedError(
                f"Kolonnenavn {variable.kolonne} med __no-/__en suffiks er ikke implementert enda")

        source_query_value = variable.statistikkbank.sq
        if source_query_value is not None:
            rows.add("SQ", column, source_query_value, None)


def metadata_rows(aggregate: Aggregate, metadata: TableMetadata, aggregate_last_updated: datetime|None) -> list[dict]:
    """Oversetter konfigurasjon og løste metadata til PX-, CS-, CR- og SQ-rader."""
    rows = _Rows()
    _px_rows(rows, aggregate, metadata, aggregate_last_updated)
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
    aggregate_last_updated = read_aggregate_last_updated(connection, schema, aggregate)
    rows = metadata_rows(aggregate, metadata, aggregate_last_updated)
    names = ", ".join(META_COLUMNS)
    binds = ", ".join(f":{name}" for name in META_COLUMNS)

    LOGGER.debug("Skriver %d metadatarader til %s.", len(rows), table)
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
