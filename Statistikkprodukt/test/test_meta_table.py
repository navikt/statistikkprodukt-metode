from datetime import datetime
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from statistikkprodukt.app.models import TableMetadata
from statistikkprodukt.config import Aggregate, Metadata
from statistikkprodukt.oracle.aggregate_table import read_aggregate_last_updated
from statistikkprodukt.oracle.meta_table import (
    create_meta_table_statement,
    metadata_rows,
    write_metadata_table,
)
from test.test_config import configuration


def aggregate_with_metadata():
    data = configuration().aggregater[0].model_dump(by_alias=True)
    data["statistikkbank"] = {
        "subject": "Befolkning",
        "subject-en": "Population",
        "subject-kort": "BEF",
        "subject-area": "Arbeidsmarked",
        "subject-code": "AM",
        "note": "Norsk merknad",
        "note-en": "English note",
        "notex": "Brudd",
        "notex-en": "Break",
        "discontinued": "no",
        "official-statistics": "yEs",
        "sq": 0,
        "ekstra-kontakter": [
            {"navn": "Datateam", "telefonnummer": "1234", "epost": "a@example.org"},
            {"navn": "Analyseteam", "epost": "b@example.org"},
        ],
    }
    data["periodisering"]["statistikkbank"] = {"sq": -24}
    data["variabler"][0].update({
        "navn": "Region", "navn-en": "Region",
        "statistikkbank": {"sq": 0},
    })
    data["tellepunkter"][0].update({
        "navn": "Antall", "navn-en": "Count",
        "innhold": "Dette er ikke enheten",
        "enhet": "personer", "enhet-en": "persons",
        "statistikkbank": {"precision": 1, "decimals": 0},
    })
    return Aggregate.model_validate(data)


def resolved_metadata(aggregate):
    return TableMetadata(
        Metadata(navn="Løst navn", navn_en="Resolved name", innhold="Innhold",
                 innhold_en="Content"),
        {
            item.kolonne: Metadata(
                navn=item.navn, navn_en=item.navn_en,
                innhold=item.innhold, innhold_en=item.innhold_en,
            )
            for item in (
                aggregate.periodisering, *aggregate.variabler, *aggregate.tellepunkter
            )
        },
    )


def test_metadata_mapping():
    aggregate = aggregate_with_metadata()
    last_updated = datetime(2026, 11, 24, 13, 45, 12)
    rows = metadata_rows(aggregate, resolved_metadata(aggregate), last_updated)
    assert all(row["scope"] is None for row in rows)
    values = {(row["type"], row["keyword"], row["language"]): row["value"]
              for row in rows if row["keyword"] != "CONTACT"}
    assert values == {
        ("PX", "MATRIX", "NO"): "21000",
        ("PX", "TITLE", "NO"): "Løst navn",
        ("PX", "TITLE", "EN"): "Resolved name",
        ("PX", "CONTENTS", "NO"): "Innhold",
        ("PX", "CONTENTS", "EN"): "Content",
        ("PX", "SUBJECT", "NO"): "Befolkning",
        ("PX", "SUBJECT", "EN"): "Population",
        ("PX", "SUBJECT-AREA", "NO"): "Arbeidsmarked",
        ("PX", "SUBJECT-CODE", "NO"): "AM",
        ("PX", "NOTE", "NO"): "Norsk merknad",
        ("PX", "NOTE", "EN"): "English note",
        ("PX", "NOTEX", "NO"): "Brudd",
        ("PX", "NOTEX", "EN"): "Break",
        ("PX", "DISCONTINUED", None): "NO",
        ("PX", "OFFICIAL-STATISTICS", None): "YES",
        ("PX", "LAST-UPDATED", None): "2026-11-24 13:45:12",
        ("CS", "STUB", None): "region,periode",
        ("CS", "TIMEVAL", None): "periode",
        ("CS", "DATA", None): "antall#1#unit_antall",
        ("CR", "STAT_VAR", "NO"): "statistikkvariabel",
        ("CR", "STAT_VAR", "EN"): "statistical variable",
        ("CR", "periode", "NO"): "Periodisering",
        ("CR", "periode", "EN"): "Periodization",
        ("CR", "region", "NO"): "Region",
        ("CR", "region", "EN"): "Region",
        ("CR", "antall", "NO"): "Antall",
        ("CR", "antall", "EN"): "Count",
        ("CR", "unit_antall", "NO"): "personer",
        ("CR", "unit_antall", "EN"): "persons",
        ("SQ", "STAT_VAR", None): "0",
        ("SQ", "periode", None): "-24",
        ("SQ", "region", None): "0",
    }
    assert [row["value"] for row in rows if row["keyword"] == "CONTACT"] == [
        "Datateam##1234#a@example.org", "Analyseteam###b@example.org",
    ]


@pytest.mark.parametrize("field", ["discontinued", "official-statistics"])
@pytest.mark.parametrize("value", [True, False])
def test_yes_no_metadata_rejects_booleans(field, value):
    data = aggregate_with_metadata().model_dump(by_alias=True)
    data["statistikkbank"][field] = value
    with pytest.raises(ValidationError):
        Aggregate.model_validate(data)


@pytest.mark.parametrize(("precision", "unit", "expected"), [
    (None, None, "antall##"),
    (0, None, "antall#0#"),
    (None, "personer", "antall##unit_antall"),
    (2, "personer", "antall#2#unit_antall"),
])
def test_optional_data_settings(precision, unit, expected):
    data = configuration().aggregater[0].model_dump(by_alias=True)
    data["tellepunkter"][0].update({
        "enhet": unit, "statistikkbank": {"precision": precision},
    })
    aggregate = Aggregate.model_validate(data)
    rows = metadata_rows(aggregate, resolved_metadata(aggregate), None)
    assert next(row["value"] for row in rows if row["keyword"] == "DATA") == expected
    assert not any(row["value"] == "None" for row in rows)


@pytest.mark.parametrize("suffix", ["no", "en"])
@pytest.mark.parametrize("field", ["periodisering", "variabler", "tellepunkter"])
def test_language_columns_are_rejected_before_database_write(field, suffix):
    data = aggregate_with_metadata().model_dump(by_alias=True)
    variable = data[field] if field == "periodisering" else data[field][0]
    column = f"{variable['kolonne']}__{suffix}"
    variable["kolonne"] = column
    data["formula"] = (
        f"~ {data['periodisering']['kolonne']} / {data['variabler'][0]['kolonne']}"
    )
    aggregate = Aggregate.model_validate(data)
    connection = MagicMock()
    with pytest.raises(
        NotImplementedError,
        match=f"Kolonnenavn {column} med __no-/__en suffiks er ikke implementert enda",
    ):
        write_metadata_table(connection, "TEST", aggregate, resolved_metadata(aggregate))
    connection.commit.assert_not_called()


@pytest.mark.parametrize("field", ["precision", "decimals"])
def test_negative_precision_rejected(field):
    data = aggregate_with_metadata().model_dump(by_alias=True)
    data["tellepunkter"][0]["statistikkbank"][field] = -1
    with pytest.raises(ValidationError):
        Aggregate.model_validate(data)


def test_last_updated_skips_timestamp_query_when_prepub_table_is_missing():
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = None

    assert read_aggregate_last_updated(connection, "TEST", aggregate_with_metadata()) is None

    connection.cursor.assert_called_once()
    cursor.execute.assert_called_once_with(
        "SELECT 1 FROM all_tables WHERE owner = :owner AND table_name = :table_name",
        owner="TEST", table_name="TABELL_PREPUB",
    )


def test_write_replaces_metadata_transactionally():
    aggregate = aggregate_with_metadata()
    metadata = resolved_metadata(aggregate)
    last_updated = datetime(2026, 11, 24, 13, 45, 12)
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.side_effect = [(1,), (last_updated,)]
    write_metadata_table(connection, "TEST", aggregate, metadata)
    assert [call.args[0] for call in cursor.execute.call_args_list] == [
        "SELECT 1 FROM all_tables WHERE owner = :owner AND table_name = :table_name",
        "SELECT MAX(dvh_lastet_tid) FROM test.tabell_prepub",
        "DELETE FROM test.tabell_meta",
    ]
    cursor.executemany.assert_called_once_with(
        "INSERT INTO test.tabell_meta (type, keyword, scope, language, value) "
        "VALUES (:type, :keyword, :scope, :language, :value)",
        metadata_rows(aggregate, metadata, last_updated),
    )
    assert [call[0] for call in cursor.method_calls] == [
        "execute", "fetchone", "execute", "fetchone", "execute", "executemany",
    ]
    connection.commit.assert_called_once()
    connection.rollback.assert_not_called()


def test_failed_insert_rolls_back_delete():
    aggregate = aggregate_with_metadata()
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = (None,)
    cursor.executemany.side_effect = ValueError("Innsetting feilet")
    with pytest.raises(ValueError, match="Innsetting feilet"):
        write_metadata_table(connection, "TEST", aggregate, resolved_metadata(aggregate))
    connection.rollback.assert_called_once()
    connection.commit.assert_not_called()


@pytest.mark.parametrize("text", ["a" * 4001, "ø" * 2001, "😀" * 1001])
def test_long_value_rejected_before_delete(text):
    aggregate = aggregate_with_metadata()
    metadata = resolved_metadata(aggregate)
    metadata.table.innhold = text
    connection = MagicMock()
    with pytest.raises(ValueError, match="PX/CONTENTS overskrider 4000 byte i UTF-8"):
        write_metadata_table(connection, "TEST", aggregate, metadata)
    connection.commit.assert_not_called()


@pytest.mark.parametrize("text", ["a" * 4000, "ø" * 2000, "😀" * 1000])
def test_value_at_utf8_byte_limit_is_accepted(text):
    aggregate = aggregate_with_metadata()
    metadata = resolved_metadata(aggregate)
    metadata.table.innhold = text
    rows = metadata_rows(aggregate, metadata, None)
    assert next(row["value"] for row in rows
                if row["keyword"] == "CONTENTS" and row["language"] == "NO") == text


def test_schema_uses_new_format():
    statement = create_meta_table_statement("TEST", "tabell_meta")
    assert "value              VARCHAR2(4000 CHAR)" in statement
    assert "language           CHAR(2)," in statement
    assert "dvh_lastet_tid DATE DEFAULT SYSDATE NOT NULL" in statement
    with pytest.raises(ValueError):
        create_meta_table_statement("TEST; DROP TABLE x", "tabell_meta")
