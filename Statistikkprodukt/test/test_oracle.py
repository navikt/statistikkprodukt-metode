from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import oracledb
import pandas as pd
import pytest
from statistikkprodukt import app, oracle
from statistikkprodukt.config import IncrementalMode, Metadata, StorageOption
from statistikkprodukt.oracle import metadata
from statistikkprodukt.oracle import (
    write_result,
    parse_metadata,
    read_periods,
    read_query,
    read_target_periods,
)
from statistikkprodukt.app_models import Kjorelogg, LasteResultat, TableMetadata
from test.test_config import configuration


def test_read_metadata_uses_obt_table(monkeypatch):
    """Leser tabell- og kolonnemetadata fra OBT-tabellen."""
    calls = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

    class Connection:
        def cursor(self):
            return Cursor()

    def fetch_table(schema, table_name, cursor):
        calls.append(("table", schema, table_name))
        return Metadata(
            navn="DB table",
            navn_en="DB table",
            innhold="DB table",
            innhold_en="DB table",
        )

    def fetch_column(schema, table_name, column, cursor):
        calls.append(("column", schema, table_name, column))
        return Metadata(
            navn=f"DB {column}",
            navn_en=f"DB {column}",
            innhold=f"DB {column}",
            innhold_en=f"DB {column}",
        )

    monkeypatch.setattr(metadata, "_fetch_table_metadata", fetch_table)
    monkeypatch.setattr(metadata, "_fetch_column_metadata", fetch_column)

    aggregate = configuration().aggregater[0]
    aggregate.obt_tabell = "obt.obt_kilde"
    result = metadata.read_metadata(Connection(), "TEST", aggregate)

    assert calls == [
        ("table", "obt", "obt_kilde"),
        ("column", "obt", "obt_kilde", "periode"),
        ("column", "obt", "obt_kilde", "region"),
        ("column", "obt", "obt_kilde", "antall"),
    ]
    assert result.columns["periode"].navn == "Periodisering"
    period_comment = next(
        statement
        for statement in metadata.comment_statements("TEST", "target_prepub", result)
        if statement.startswith("COMMENT ON COLUMN test.target_prepub.periode")
    )
    assert period_comment == (
        "COMMENT ON COLUMN test.target_prepub.periode IS "
        "'#NAVN Periodisering #NAVN_EN Periodization #INNHOLD Månedsperiode "
        "#INNHOLD_EN Month period'"
    )


def test_read_metadata_defaults_to_obt_and_overrides_only_yaml_fields(monkeypatch):
    aggregate = configuration().aggregater[0].model_copy(update={
        "navn": None, "navn_en": None, "innhold": None, "innhold_en": "YAML override",
    })
    db_metadata = Metadata(
        navn="OBT navn", navn_en="OBT name", innhold="OBT innhold", innhold_en="OBT content"
    )
    monkeypatch.setattr(metadata, "_fetch_table_metadata", lambda *_: db_metadata)
    monkeypatch.setattr(metadata, "_fetch_column_metadata", lambda *_: db_metadata)

    resolved = metadata.read_metadata(MagicMock(), "TEST", aggregate)
    assert resolved.table.model_dump() == {
        "navn": "OBT navn", "navn_en": "OBT name",
        "innhold": "OBT innhold", "innhold_en": "YAML override",
    }


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("aggregat_id", ["00000", "00001", "09999", "21000"])
def test_development_id_warns_only_when_writing_to_database(monkeypatch, tmp_path, caplog, dry_run, aggregat_id):
    config = configuration()
    aggregate = config.aggregater[0].model_copy(update={"id": aggregat_id})
    monkeypatch.setattr(oracle, "read_metadata", MagicMock())
    monkeypatch.setattr(oracle, "write_result", MagicMock())
    monkeypatch.setattr(oracle, "write_comments", MagicMock())
    monkeypatch.setattr(oracle, "write_metadata_table", MagicMock())
    transfer = app.Transfer(MagicMock(), StorageOption.CSV, StorageOption.DB, tmp_path, tmp_path)

    app._write_database(config, aggregate, pd.DataFrame(), pd.DataFrame(), transfer, dry_run=dry_run)

    if dry_run or aggregat_id == "21000":
        assert "uten gyldig NAV-ID" not in caplog.text
    else:
        assert "uten gyldig NAV-ID" in caplog.text
        assert f"id='{aggregat_id}'" in caplog.text
        assert "må fjernes før publisering" in caplog.text


def test_write_log_only_inserts_and_binds_all_lobs():
    connection = MagicMock()
    aggregate = configuration().aggregater[0]
    logg = Kjorelogg({}, "db", "db", [aggregate])
    logg.fullfor_suksess()
    oracle.write_log(connection, "TEST", logg)

    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.execute.assert_not_called()
    cursor.executemany.assert_called_once()
    assert cursor.executemany.call_args.args[0].startswith("INSERT INTO test.ost_logg ")
    sizes = cursor.setinputsizes.call_args.kwargs
    assert sizes["konfigurasjon"] == oracledb.DB_TYPE_CLOB
    assert sizes["perioder"] == oracledb.DB_TYPE_CLOB
    assert sizes["feilmelding"] == oracledb.DB_TYPE_CLOB
    connection.rollback.assert_not_called()
    connection.commit.assert_called_once()


def test_write_log_rolls_back_its_own_failed_insert():
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.executemany.side_effect = ValueError("Innsetting feilet")
    logg = Kjorelogg({}, "db", "db", configuration().aggregater)
    logg.fullfor_suksess()

    with pytest.raises(ValueError, match="Innsetting feilet"):
        oracle.write_log(connection, "TEST", logg)
    connection.rollback.assert_called_once()
    connection.commit.assert_not_called()


def test_auxiliary_schema_creation_checks_dictionary_and_reuses_cursor():
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    aggregate = configuration().aggregater[0]
    cursor.fetchall.return_value = [(oracle.LOG_TABLE.upper(),), (aggregate.meta_tabell.upper(),)]

    app._create_log_and_meta_tables(connection, "TEST", [aggregate], create_in_database=True)

    connection.cursor.assert_called_once()
    cursor.execute.assert_called_once_with(
        "SELECT table_name FROM all_tables WHERE owner = :owner", owner="TEST"
    )


def test_metadata_and_log_roundtrip_preserves_lobs_and_json(oracle_connection):
    aggregate = configuration().aggregater[0].model_copy(update={
        "tabell": f"ost_test_{uuid4().hex[:12]}",
    })
    long_text = "Æøå" * 5000
    table_metadata = TableMetadata(
        Metadata(navn="OBT navn", navn_en="Name", innhold=long_text, innhold_en="Content"),
        {},
    )
    logg = Kjorelogg({"beskrivelse": long_text}, "db", "db", [aggregate])
    logg.resultater = [
        LasteResultat(aggregate.id, aggregate.tabell, ["2025,01", "2025-02"], 8, 1, 2, table_metadata)
    ]
    logg.fullfor_feil(ValueError(long_text))
    success = Kjorelogg({}, "db", "db", [aggregate])
    success.fullfor_suksess()
    if oracle.table_exists(oracle_connection, "SYSTEM", oracle.LOG_TABLE):
        with oracle_connection.cursor() as cursor:
            cursor.execute(f"DROP TABLE SYSTEM.{oracle.LOG_TABLE} PURGE")
    try:
        app._create_log_and_meta_tables(
            oracle_connection, "SYSTEM", [aggregate], create_in_database=True
        )
        oracle.write_metadata_table(oracle_connection, "SYSTEM", aggregate, table_metadata)
        table_metadata.table.navn = "Oppdatert"
        oracle.write_metadata_table(oracle_connection, "SYSTEM", aggregate, table_metadata)
        oracle.write_log(oracle_connection, "SYSTEM", logg)
        oracle.write_log(oracle_connection, "SYSTEM", success)

        with oracle_connection.cursor() as cursor:
            cursor.execute(f"SELECT felt, verdi FROM SYSTEM.{aggregate.meta_tabell}")
            meta = {name: value.read() for name, value in cursor.fetchall()}
            assert meta["navn"] == "Oppdatert"
            assert meta["innhold"] == long_text
            assert len(meta) == len(metadata.META_FIELDS)

            cursor.execute(
                "SELECT status, db_bruker, commit_hash, konfigurasjon, perioder, feilmelding, "
                "navn, starttid, sluttid, antall_rader, "
                "JSON_VALUE(konfigurasjon, '$.beskrivelse' RETURNING CLOB) "
                "FROM SYSTEM.ost_logg WHERE kjoring_id = :id",
                id=logg.kjoring_id,
            )
            status, user, commit_hash, config, periods, error, name, start, end, rows, json_text = cursor.fetchone()
            assert status == "FEILET"
            assert user == "SYSTEM"
            assert commit_hash == logg.commit_hash
            assert config == {"beskrivelse": long_text}
            assert periods == ["2025,01", "2025-02"]
            assert error.read() == long_text
            assert name == "Oppdatert"
            assert rows == 8
            assert start == logg.starttid and start.tzinfo is None
            assert end == logg.sluttid and end.tzinfo is None
            assert json_text.read() == long_text

            cursor.execute(
                "SELECT status, antall_rader FROM SYSTEM.ost_logg WHERE kjoring_id = :id",
                id=success.kjoring_id,
            )
            assert cursor.fetchone() == ("OK", None)
    finally:
        with oracle_connection.cursor() as cursor:
            if oracle.table_exists(oracle_connection, "SYSTEM", oracle.LOG_TABLE):
                cursor.execute(
                    "DELETE FROM SYSTEM.ost_logg WHERE kjoring_id IN (:1, :2)",
                    [logg.kjoring_id, success.kjoring_id],
                )
                oracle_connection.commit()
            if oracle.table_exists(oracle_connection, "SYSTEM", aggregate.meta_tabell):
                cursor.execute(f"DROP TABLE SYSTEM.{aggregate.meta_tabell} PURGE")


def test_metadata_tags_are_order_independent_and_multiline():
    """Parser metadata uavhengig av rekkefølge og linjeskift."""
    metadata = parse_metadata(
        "#INNHOLD Første linje\nandre linje #NAVN Navn #NAVN_EN English"
    )
    assert metadata.model_dump() == {
        "innhold": "Første linje\nandre linje",
        "navn": "Navn",
        "navn_en": "English",
        "innhold_en": None,
    }
    with pytest.raises(ValueError, match="flere ganger"):
        parse_metadata("#NAVN Én #NAVN To")
    with pytest.raises(ValueError, match="flere ganger"):
        parse_metadata("#NAVN #NAVN To")


def test_create_tables_writes_column_comments_to_all_comments(
        oracle_connection, monkeypatch, tmp_path
):
    config = configuration()
    config.connection.schema_name = "SYSTEM"
    aggregate = config.aggregater[0]
    aggregate.tabell = f"sb_test_{uuid4().hex[:12]}"
    aggregate.sporring_sql = "create_test.sql"
    (tmp_path / aggregate.sporring_sql).write_text(
        "select cast(202501 as varchar2(6)) as periode, 'Oslo' as region, 10 as antall from dual",
        encoding="utf-8",
    )

    column_metadata = {
        "periode": Metadata(
            navn="Periode",
            navn_en="Period",
            innhold="Måned",
            innhold_en="Month",
        ),
        "region": Metadata(
            navn="Region",
            navn_en="Region",
            innhold="Geografisk område",
            innhold_en="Geographical area",
        ),
        "antall": Metadata(
            navn="Antall",
            navn_en="Count",
            innhold="Antall personer",
            innhold_en="Number of people",
        ),
    }

    class ConnectionProxy:
        def cursor(self):
            return oracle_connection.cursor()

        def close(self):
            pass

    table_metadata = Metadata(
        navn="Tabell",
        navn_en="Table",
        innhold="Tabellinnhold",
        innhold_en="Table content",
    )
    expected_table_comment = (
        "#NAVN Tabell #NAVN_EN Table #INNHOLD Tabellinnhold #INNHOLD_EN Table content"
    )
    monkeypatch.setattr(app, "_connect", lambda _: ConnectionProxy())
    monkeypatch.setattr(
        oracle,
        "read_metadata",
        lambda *args: TableMetadata(table=table_metadata, columns=column_metadata),
    )
    tables = [aggregate.prepub_tabell, aggregate.kontroll_tabell]

    try:
        app.create_tables(config, tmp_path, create_in_database=True)

        for table_name in tables:
            with oracle_connection.cursor() as cursor:
                cursor.execute(
                    "SELECT column_name, comments FROM sys.all_col_comments "
                    "WHERE owner = :owner AND table_name = :table_name "
                    "AND comments IS NOT NULL",
                    owner="SYSTEM",
                    table_name=table_name.upper(),
                )
                comments = dict(cursor.fetchall())
                cursor.execute(
                    "SELECT comments FROM sys.all_tab_comments "
                    "WHERE owner = :owner AND table_name = :table_name",
                    owner="SYSTEM",
                    table_name=table_name.upper(),
                )
                table_comment = cursor.fetchone()[0]

            assert table_comment == expected_table_comment
            assert comments == {
                "PERIODE": (
                    "#NAVN Periode #NAVN_EN Period #INNHOLD Måned #INNHOLD_EN Month"
                ),
                "REGION": (
                    "#NAVN Region #NAVN_EN Region #INNHOLD Geografisk område "
                    "#INNHOLD_EN Geographical area"
                ),
                "ANTALL": (
                    "#NAVN Antall #NAVN_EN Count #INNHOLD Antall personer "
                    "#INNHOLD_EN Number of people"
                ),
            }
    finally:
        with oracle_connection.cursor() as cursor:
            for table_name in [*tables, aggregate.meta_tabell]:
                cursor.execute(
                    "SELECT 1 FROM all_tables WHERE owner = :owner AND table_name = :table_name",
                    owner="SYSTEM",
                    table_name=table_name.upper(),
                )
                if cursor.fetchone():
                    cursor.execute(f"DROP TABLE SYSTEM.{table_name} PURGE")
        oracle_connection.commit()


def test_validate_string_columns_rejects_non_text_columns(oracle_connection):
    table = f"sb_test_{uuid4().hex[:12]}"
    try:
        with oracle_connection.cursor() as cursor:
            cursor.execute(
                f"CREATE TABLE SYSTEM.{table} (periode VARCHAR2(6 CHAR), region NUMBER)"
            )

        with pytest.raises(ValueError, match="Kolonnen region.*NUMBER"):
            oracle.validate_string_columns(
                oracle_connection, "SYSTEM", table, ["periode", "region"]
            )
    finally:
        with oracle_connection.cursor() as cursor:
            cursor.execute(f"DROP TABLE SYSTEM.{table} PURGE")
        oracle_connection.commit()


@pytest.mark.parametrize(
    ("sql", "column_name"),
    [
        (
            "select 202501 as periode, 'Oslo' as region, 10 as antall from dual",
            "periode",
        ),
        (
            "select '202501' as periode, 10 as region, 10 as antall from dual",
            "region",
        ),
    ],
)
def test_read_query_rejects_non_text_period_and_variables(
        oracle_connection, sql, column_name
):
    with pytest.raises(ValueError, match=f"Kolonnen {column_name}.*tekstkolonne"):
        read_query(oracle_connection, sql, configuration().aggregater[0])


def test_read_periods_rejects_non_text_period(oracle_connection):
    with pytest.raises(ValueError, match="Kolonnen periode.*tekstkolonne"):
        read_periods(oracle_connection, "select 202501 as periode from dual", "periode")


def test_write_database_writes_column_comments_to_all_comments(
        oracle_connection, monkeypatch
):
    config = configuration()
    config.connection.schema_name = "SYSTEM"
    aggregate = config.aggregater[0]
    aggregate.tabell = f"sb_test_{uuid4().hex[:12]}"
    metadata = TableMetadata(
        table=Metadata(
            navn="Tabell",
            navn_en="Table",
            innhold="Tabellinnhold",
            innhold_en="Table content",
        ),
        columns={
            "periode": Metadata(
                navn="Periode",
                navn_en="Period",
                innhold="Måned",
                innhold_en="Month",
            ),
            "region": Metadata(
                navn="Region",
                navn_en="Region",
                innhold="Geografisk område",
                innhold_en="Geographical area",
            ),
            "antall": Metadata(
                navn="Antall",
                navn_en="Count",
                innhold="Antall personer",
                innhold_en="Number of people",
            ),
        },
    )
    monkeypatch.setattr(oracle, "read_metadata", lambda *args: metadata)
    tables = [aggregate.prepub_tabell, aggregate.kontroll_tabell]

    try:
        with oracle_connection.cursor() as cursor:
            for table_name in tables:
                cursor.execute(
                    f"CREATE TABLE SYSTEM.{table_name} ("
                    "periode NUMBER, region VARCHAR2(100 CHAR), "
                    "antall NUMBER, primary NUMBER(1), suppressed NUMBER(1))"
                )
            cursor.execute(
                oracle.create_meta_table_statement("SYSTEM", aggregate.meta_tabell)
            )

        frame = pd.DataFrame(
            {
                "periode": [202501],
                "region": ["Oslo"],
                "antall": [10],
                "primary": [True],
                "suppressed": [True],
            }
        )
        app._write_database(
            config,
            aggregate,
            frame,
            frame,
            app.Transfer(
                oracle_connection,
                StorageOption.DB,
                StorageOption.DB,
                Path("dummy"),
                Path("dummy")
            ),
            dry_run=False,
        )

        for table_name in tables:
            with oracle_connection.cursor() as cursor:
                cursor.execute(
                    "SELECT column_name, comments FROM sys.all_col_comments "
                    "WHERE owner = :owner AND table_name = :table_name "
                    "AND comments IS NOT NULL",
                    owner="SYSTEM",
                    table_name=table_name.upper(),
                )
                comments = dict(cursor.fetchall())
                cursor.execute(
                    "SELECT comments FROM sys.all_tab_comments "
                    "WHERE owner = :owner AND table_name = :table_name",
                    owner="SYSTEM",
                    table_name=table_name.upper(),
                )
                table_comment = cursor.fetchone()[0]

            assert table_comment == (
                "#NAVN Tabell #NAVN_EN Table #INNHOLD Tabellinnhold "
                "#INNHOLD_EN Table content"
            )
            assert comments == {
                "PERIODE": (
                    "#NAVN Periode #NAVN_EN Period #INNHOLD Måned #INNHOLD_EN Month"
                ),
                "REGION": (
                    "#NAVN Region #NAVN_EN Region #INNHOLD Geografisk område "
                    "#INNHOLD_EN Geographical area"
                ),
                "ANTALL": (
                    "#NAVN Antall #NAVN_EN Count #INNHOLD Antall personer "
                    "#INNHOLD_EN Number of people"
                ),
            }
    finally:
        with oracle_connection.cursor() as cursor:
            for table_name in [*tables, aggregate.meta_tabell]:
                cursor.execute(
                    "SELECT 1 FROM all_tables WHERE owner = :owner AND table_name = :table_name",
                    owner="SYSTEM",
                    table_name=table_name.upper(),
                )
                if cursor.fetchone():
                    cursor.execute(f"DROP TABLE SYSTEM.{table_name} PURGE")
        oracle_connection.commit()


def test_empty_database_result_does_not_inspect_or_mutate_connection():
    """Sikrer at tomme resultater ikke utfører databaseoperasjoner."""

    class NoDatabaseCalls:
        def __getattr__(self, name):
            """Feiler testen dersom en metode eller egenskap brukes."""
            raise AssertionError(f"Unexpected database call: {name}")

    aggregate = configuration().aggregater[0]
    write_result(
        NoDatabaseCalls(),
        "TEST",
        aggregate,
        "tabell_prepub",
        pd.DataFrame(columns=["periode", "region", "antall", "primary", "suppressed"]),
    )


def test_insert_schema_errors_are_reported_by_oracle(oracle_connection):
    """Lar Oracle avvise kolonner som ikke finnes i måltabellen."""
    table = f"sb_test_{uuid4().hex[:12]}"
    aggregate = configuration().aggregater[0].model_copy(update={"tabell": table})
    try:
        with oracle_connection.cursor() as cursor:
            cursor.execute(
                f"CREATE TABLE SYSTEM.{table} ("
                "periode NUMBER, region VARCHAR2(100 CHAR), "
                "antall NUMBER, primary NUMBER(1), suppressed NUMBER(1))"
            )

        with pytest.raises(oracledb.DatabaseError):
            write_result(
                oracle_connection,
                "SYSTEM",
                aggregate,
                table,
                pd.DataFrame(
                    {"periode": [202501], "region": ["Oslo"], "ukjent": [10]}
                ),
            )

        with oracle_connection.cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) FROM SYSTEM.{table}")
            assert cursor.fetchone()[0] == 0
    finally:
        with oracle_connection.cursor() as cursor:
            cursor.execute(f"DROP TABLE SYSTEM.{table} PURGE")
        oracle_connection.commit()


@pytest.mark.parametrize("periods", [None, [], [202502], [202501, 202502]])
def test_read_query_filters_periods_in_oracle(oracle_connection, periods):
    sql = (
        "select '202501' as periode, 'Oslo' as region, 10 as antall from dual "
        "union all select '202502', 'Oslo', 20 from dual;"
    )
    aggregate = configuration().aggregater[0]
    assert read_periods(oracle_connection, sql, "periode") == {"202501", "202502"}

    frame = read_query(
        oracle_connection, sql, aggregate, periods_to_load=periods
    )
    assert sorted(frame["periode"].tolist()) == (["202501", "202502"] if periods is None else [str(value) for value in periods])


def test_read_query_binds_string_periods_in_oracle(oracle_connection):
    period = "x') OR 1=1 --"
    sql = (
        "select 'x'') OR 1=1 --' as periode, 'Oslo' as region, 10 as antall from dual "
        "union all select '2025-02', 'Oslo', 20 from dual"
    )
    frame = read_query(
        oracle_connection, sql, configuration().aggregater[0], periods_to_load=[period]
    )
    assert frame["periode"].tolist() == [period]


def test_read_target_periods_uses_configured_schema(oracle_connection):
    table = f"sb_test_{uuid4().hex[:12]}"
    prepub_table = f"{table}_prepub"
    aggregate = configuration().aggregater[0].model_copy(update={"tabell": table})
    try:
        with oracle_connection.cursor() as cursor:
            cursor.execute(f"CREATE TABLE SYSTEM.{prepub_table} (periode VARCHAR2(6 CHAR))")
            cursor.executemany(
                f"INSERT INTO SYSTEM.{prepub_table} (periode) VALUES (:1)",
                [("202501",), ("202502",), ("202502",)],
            )
        assert read_target_periods(oracle_connection, "SYSTEM", aggregate) == {"202501", "202502"}
    finally:
        with oracle_connection.cursor() as cursor:
            cursor.execute(f"DROP TABLE SYSTEM.{prepub_table} PURGE")
        oracle_connection.commit()


def test_read_query_handles_more_than_1000_periods_in_oracle(oracle_connection):
    frame = read_query(
        oracle_connection,
        "select '202501' as periode, 'Oslo' as region, 10 as antall from dual",
        configuration().aggregater[0],
        periods_to_load=[str(period) for period in range(202000, 203001)],
    )
    assert frame["periode"].tolist() == ["202501"]


@pytest.mark.parametrize(
    ("mode", "loaded", "expected"),
    [
        (
                "overskriv_ikke",
                ["202503"],
                [("202500", 0), ("202501", 1), ("202502", 2), ("202503", 30)],
        ),
        (
                "overskriv_alt",
                ["202501", "202502", "202503"],
                [("202500", 0), ("202501", 10), ("202502", 20), ("202503", 30)],
        ),
        (
                "overskriv_siste_periode",
                ["202503"],
                [("202500", 0), ("202501", 1), ("202502", 2), ("202503", 30)],
        ),
    ],
)
def test_process_aggregate_only_masks_selected_periods(
        oracle_connection, monkeypatch, tmp_path, mode, loaded, expected
):
    table = f"sb_test_{uuid4().hex[:12]}"
    config = configuration()
    config.connection.schema_name = "system"
    aggregate = config.aggregater[0]
    aggregate.tabell = table
    aggregate.inkrementell_strategi = IncrementalMode(mode)
    (tmp_path / aggregate.sporring_sql).write_text(
        "select '202501' as periode, 'Oslo' as region, 10 as antall from dual "
        "union all select '202502', 'Oslo', 20 from dual "
        "union all select '202503', 'Oslo', 30 from dual;"
    )
    # == periods passed to prikking will be loaded in the database.
    # Store those here to use in an assert later on
    masked_periods = []

    # Record the periods passed to masking, independently of the resulting table contents.
    def mask(data, *args, **kwargs):
        masked_periods.extend(data["periode"].tolist())
        result = data.copy()
        result["primary"] = pd.Series(True, index=result.index, dtype=bool)
        result["suppressed"] = pd.Series(True, index=result.index, dtype=bool)
        return data.copy(), result

    monkeypatch.setattr(app, "prikking", mask)
    monkeypatch.setattr(
        oracle,
        "read_metadata",
        lambda *args: TableMetadata(table=Metadata(), columns={}),
    )
    monkeypatch.setattr(oracle, "write_comments", lambda *args: None)
    prepub_table = f"{table}_prepub"
    kontroll_table = f"{table}_kontroll"
    try:
        with oracle_connection.cursor() as cursor:
            cursor.execute(
                f"CREATE TABLE SYSTEM.{prepub_table} ("
                "periode VARCHAR2(6 CHAR), region VARCHAR2(100 CHAR), antall NUMBER)"
            )
            cursor.execute(
                f"CREATE TABLE SYSTEM.{kontroll_table} ("
                "periode VARCHAR2(6 CHAR), region VARCHAR2(100 CHAR), "
                "antall NUMBER, primary NUMBER(1), suppressed NUMBER(1))"
            )
            cursor.execute(oracle.create_meta_table_statement("SYSTEM", aggregate.meta_tabell))
            existing = [("202500", "Oslo", 0), ("202501", "Oslo", 1), ("202502", "Oslo", 2)]
            if mode == "overskriv_siste_periode":
                # Make 202503 the existing latest period so this mode must replace it.
                existing.append(("202503", "Oslo", 3))
            cursor.executemany(
                f"INSERT INTO SYSTEM.{prepub_table} VALUES (:1, :2, :3)", existing
            )
            cursor.executemany(
                f"INSERT INTO SYSTEM.{kontroll_table} VALUES (:1, :2, :3, 1, 1)",
                existing,
            )

        app._process_aggregate(
            config,
            aggregate,
            tmp_path,
            app.Transfer(
                oracle_connection,
                StorageOption.DB,
                StorageOption.DB,
                Path("dummy"),
                Path("dummy"),
            ),
            dry_run=False,
        )

        assert sorted(masked_periods) == loaded
        with (oracle_connection.cursor() as cursor):
            for name in (prepub_table, kontroll_table):
                cursor.execute(f"SELECT periode, antall FROM SYSTEM.{name} ORDER BY periode")
                assert cursor.fetchall() == expected
    finally:
        with oracle_connection.cursor() as cursor:
            cursor.execute(f"DROP TABLE SYSTEM.{prepub_table} PURGE")
            cursor.execute(f"DROP TABLE SYSTEM.{kontroll_table} PURGE")
            cursor.execute(f"DROP TABLE SYSTEM.{aggregate.meta_tabell} PURGE")
        oracle_connection.commit()
