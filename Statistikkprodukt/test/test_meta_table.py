from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from statistikkprodukt.app.models import TableMetadata
from statistikkprodukt.config import Aggregate, Metadata
from statistikkprodukt.oracle.meta_table import (
    create_meta_table_statement,
    metadata_rows,
    write_metadata_table,
)
from test.test_config import configuration


def aggregate_with_metadata():
    data = configuration().aggregater[0].model_dump(by_alias=True)
    data["statistikkbank"] = {
        "tema": "Befolkning",
        "tema-en": "Population",
        "tema-kode": "BEF",
        "fagomraade": "Arbeidsmarked",
        "fagomraade-kode": "AM",
        "merknad": "Norsk merknad",
        "merknad-en": "English note",
        "fremhevet-merknad": "Brudd",
        "fremhevet-merknad-en": "Break",
        "avsluttet": False,
        "offisiell-statistikk": True,
        "startvisning-antall-tellepunkter": 0,
        "kontakter": [
            {"seksjon": "Statistikk", "telefonnummer": "1234", "epost": "a@example.org"},
            {"seksjon": "Analyse", "epost": "b@example.org"},
        ],
    }
    data["periodisering"]["statistikkbank"] = {"startvisning-antall-verdier": -24}
    data["variabler"][0].update({
        "navn": "Region", "navn-en": "Region",
        "statistikkbank": {"startvisning-antall-verdier": 0},
    })
    data["tellepunkter"][0].update({
        "navn": "Antall", "navn-en": "Count",
        "innhold": "Dette er ikke enheten",
        "enhet": "personer", "enhet-en": "persons",
        "statistikkbank": {"desimaler-visning": 1, "desimaler-lagring": 0},
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
    rows = metadata_rows(aggregate, resolved_metadata(aggregate))
    assert all(row["scope"] is None for row in rows)
    values = {(row["type"], row["keyword"], row["language"]): row["value"]
              for row in rows if row["keyword"] != "CONTACT"}
    assert values == {
        ("PX", "MATRIX", None): "21000",
        ("PX", "TITLE", "NO"): "Løst navn",
        ("PX", "TITLE", "EN"): "Resolved name",
        ("PX", "CONTENTS", "NO"): "Innhold",
        ("PX", "CONTENTS", "EN"): "Content",
        ("PX", "SUBJECT", "NO"): "Befolkning",
        ("PX", "SUBJECT", "EN"): "Population",
        ("PX", "SUBJECT_KORT", None): "BEF",
        ("PX", "SUBJECT-AREA", "NO"): "Arbeidsmarked",
        ("PX", "SUBJECT-CODE", None): "AM",
        ("PX", "NOTE", "NO"): "Norsk merknad",
        ("PX", "NOTE", "EN"): "English note",
        ("PX", "NOTEX", "NO"): "Brudd",
        ("PX", "NOTEX", "EN"): "Break",
        ("PX", "DISCONTINUED", None): "NO",
        ("PX", "OFFICIAL-STATISTICS", None): "YES",
        ("CS", "STUB", None): "region",
        ("CS", "HEADING", None): "periode",
        ("CS", "TIMEVAL", None): "periode",
        ("CS", "DATA", None): "antall#0#unit_antall",
        ("CR", "periode", "NO"): "Periodisering",
        ("CR", "periode", "EN"): "Periodization",
        ("CR", "region", "NO"): "Region",
        ("CR", "region", "EN"): "Region",
        ("CR", "antall", "NO"): "Antall",
        ("CR", "antall", "EN"): "Count",
        ("CR", "unit_antall", "NO"): "personer",
        ("CR", "unit_antall", "EN"): "persons",
        ("CR", "precision_antall", None): "1",
        ("SQ", "STAT_VAR", None): "0",
        ("SQ", "periode", None): "-24",
        ("SQ", "region", None): "0",
    }
    assert [row["value"] for row in rows if row["keyword"] == "CONTACT"] == [
        "Statistikk##1234#a@example.org", "Analyse###b@example.org",
    ]


@pytest.mark.parametrize(("precision", "unit", "expected"), [
    (None, None, "antall"),
    (0, None, "antall#0"),
    (None, "personer", "antall#unit_antall"),
    (2, "personer", "antall#2#unit_antall"),
])
def test_optional_data_settings(precision, unit, expected):
    data = configuration().aggregater[0].model_dump(by_alias=True)
    data["tellepunkter"][0].update({
        "enhet": unit, "statistikkbank": {"desimaler-lagring": precision},
    })
    aggregate = Aggregate.model_validate(data)
    rows = metadata_rows(aggregate, resolved_metadata(aggregate))
    assert next(row["value"] for row in rows if row["keyword"] == "DATA") == expected
    assert next(row["value"] for row in rows if row["keyword"] == "STAT_VAR") == "1"
    assert not any(row["value"] == "None" for row in rows)


@pytest.mark.parametrize("suffix", ["no", "en"])
@pytest.mark.parametrize("field", ["periodisering", "variabler", "tellepunkter"])
def test_language_columns_are_rejected_before_database_write(field, suffix):
    data = aggregate_with_metadata().model_dump(by_alias=True)
    variable = data[field] if field == "periodisering" else data[field][0]
    column = f"{variable['kolonne']}__{suffix}"
    variable["kolonne"] = column
    aggregate = Aggregate.model_validate(data)
    connection = MagicMock()
    with pytest.raises(
        NotImplementedError,
        match=f"Kolonnenavn {column} med __no-/__en suffiks er ikke implementert enda",
    ):
        write_metadata_table(connection, "TEST", aggregate, resolved_metadata(aggregate))
    connection.cursor.assert_not_called()
    connection.commit.assert_not_called()


@pytest.mark.parametrize("field", ["desimaler-visning", "desimaler-lagring"])
def test_negative_precision_rejected(field):
    data = aggregate_with_metadata().model_dump(by_alias=True)
    data["tellepunkter"][0]["statistikkbank"][field] = -1
    with pytest.raises(ValidationError):
        Aggregate.model_validate(data)


def test_zero_display_precision_is_written():
    data = aggregate_with_metadata().model_dump(by_alias=True)
    data["tellepunkter"][0]["statistikkbank"]["desimaler-visning"] = 0
    aggregate = Aggregate.model_validate(data)
    rows = metadata_rows(aggregate, resolved_metadata(aggregate))
    assert next(row["value"] for row in rows if row["keyword"] == "precision_antall") == "0"


def test_write_replaces_metadata_transactionally():
    aggregate = aggregate_with_metadata()
    metadata = resolved_metadata(aggregate)
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    write_metadata_table(connection, "TEST", aggregate, metadata)
    cursor.execute.assert_called_once_with("DELETE FROM test.tabell_meta")
    cursor.executemany.assert_called_once_with(
        "INSERT INTO test.tabell_meta (type, keyword, scope, language, value) "
        "VALUES (:type, :keyword, :scope, :language, :value)",
        metadata_rows(aggregate, metadata),
    )
    assert [call[0] for call in cursor.method_calls] == [
        "execute", "executemany",
    ]
    connection.commit.assert_called_once()
    connection.rollback.assert_not_called()


def test_failed_insert_rolls_back_delete():
    aggregate = aggregate_with_metadata()
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
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
    connection.cursor.assert_not_called()


@pytest.mark.parametrize("text", ["a" * 4000, "ø" * 2000, "😀" * 1000])
def test_value_at_utf8_byte_limit_is_accepted(text):
    aggregate = aggregate_with_metadata()
    metadata = resolved_metadata(aggregate)
    metadata.table.innhold = text
    rows = metadata_rows(aggregate, metadata)
    assert next(row["value"] for row in rows
                if row["keyword"] == "CONTENTS" and row["language"] == "NO") == text


def test_schema_uses_new_format():
    statement = create_meta_table_statement("TEST", "tabell_meta")
    assert "value              VARCHAR2(4000 CHAR)" in statement
    assert "language           CHAR(2)," in statement
    assert "dvh_sist_oppdatert TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL" in statement
    with pytest.raises(ValueError):
        create_meta_table_statement("TEST; DROP TABLE x", "tabell_meta")
