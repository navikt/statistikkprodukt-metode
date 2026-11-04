from pathlib import Path
from uuid import uuid4

import oracledb
import pandas as pd
import pytest
from statistikkprodukt import app, oracle
from statistikkprodukt.config import InkrementalMode, Metadata, StorageOption
from statistikkprodukt.oracle import metadata
from statistikkprodukt.oracle import (
    write_result,
    parse_metadata,
    read_periods,
    read_query,
    read_target_periods,
)
from statistikkprodukt.oracle.metadata import TableMetadata
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
        return Metadata()

    def fetch_column(schema, table_name, column, cursor):
        calls.append(("column", schema, table_name, column))
        return Metadata()

    monkeypatch.setattr(metadata, "_fetch_table_metadata", fetch_table)
    monkeypatch.setattr(metadata, "_fetch_column_metadata", fetch_column)
    monkeypatch.setattr(metadata, "_merge_metadata", lambda name, config, db: db)

    aggregate = configuration().smale_aggregater[0]
    aggregate.obt_tabell = "obt.obt_kilde"
    metadata.read_metadata(Connection(), "TEST", aggregate)

    assert calls == [
        ("table", "obt", "obt_kilde"),
        ("column", "obt", "obt_kilde", "periode"),
        ("column", "obt", "obt_kilde", "region"),
        ("column", "obt", "obt_kilde", "antall"),
    ]


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
    aggregate = config.smale_aggregater[0]
    aggregate.tabell = f"sb_test_{uuid4().hex[:12]}"
    aggregate.sporring_sql = "create_test.sql"
    (tmp_path / aggregate.sporring_sql).write_text(
        "select 202501 as periode, 'Oslo' as region, 10 as antall from dual",
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
            for table_name in tables:
                cursor.execute(
                    "SELECT 1 FROM all_tables WHERE owner = :owner AND table_name = :table_name",
                    owner="SYSTEM",
                    table_name=table_name.upper(),
                )
                if cursor.fetchone():
                    cursor.execute(f"DROP TABLE SYSTEM.{table_name} PURGE")
        oracle_connection.commit()


def test_write_database_writes_column_comments_to_all_comments(
        oracle_connection, monkeypatch
):
    config = configuration()
    config.connection.schema_name = "SYSTEM"
    aggregate = config.smale_aggregater[0]
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
                    "periode NUMBER, region VARCHAR2(100), "
                    "antall NUMBER, primary NUMBER(1), suppressed NUMBER(1))"
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
            for table_name in tables:
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

    aggregate = configuration().smale_aggregater[0]
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
    aggregate = configuration().smale_aggregater[0].model_copy(update={"tabell": table})
    try:
        with oracle_connection.cursor() as cursor:
            cursor.execute(
                f"CREATE TABLE SYSTEM.{table} ("
                "periode NUMBER, region VARCHAR2(100), "
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
        "select 202501 as periode, 'Oslo' as region, 10 as antall from dual "
        "union all select 202502, 'Oslo', 20 from dual;"
    )
    aggregate = configuration().smale_aggregater[0]
    assert read_periods(oracle_connection, sql, "periode") == {202501, 202502}

    frame = read_query(
        oracle_connection, sql, aggregate, periods_to_load=periods
    )
    assert sorted(frame["periode"].tolist()) == ([202501, 202502] if periods is None else periods)


def test_read_query_binds_string_periods_in_oracle(oracle_connection):
    period = "x') OR 1=1 --"
    sql = (
        "select 'x'') OR 1=1 --' as periode, 'Oslo' as region, 10 as antall from dual "
        "union all select '2025-02', 'Oslo', 20 from dual"
    )
    frame = read_query(
        oracle_connection, sql, configuration().smale_aggregater[0], periods_to_load=[period]
    )
    assert frame["periode"].tolist() == [period]


def test_read_target_periods_uses_configured_schema(oracle_connection):
    table = f"sb_test_{uuid4().hex[:12]}"
    prepub_table = f"{table}_prepub"
    aggregate = configuration().smale_aggregater[0].model_copy(update={"tabell": table})
    try:
        with oracle_connection.cursor() as cursor:
            cursor.execute(f"CREATE TABLE SYSTEM.{prepub_table} (periode NUMBER(6,0))")
            cursor.executemany(
                f"INSERT INTO SYSTEM.{prepub_table} (periode) VALUES (:1)",
                [(202501,), (202502,), (202502,)],
            )
        assert read_target_periods(oracle_connection, "SYSTEM", aggregate) == {202501, 202502}
    finally:
        with oracle_connection.cursor() as cursor:
            cursor.execute(f"DROP TABLE SYSTEM.{prepub_table} PURGE")
        oracle_connection.commit()


def test_read_query_handles_more_than_1000_periods_in_oracle(oracle_connection):
    frame = read_query(
        oracle_connection,
        "select 202501 as periode, 'Oslo' as region, 10 as antall from dual",
        configuration().smale_aggregater[0],
        periods_to_load=list(range(202000, 203001)),
    )
    assert frame["periode"].tolist() == [202501]


@pytest.mark.parametrize(
    ("mode", "loaded", "expected"),
    [
        (
                "overskriv_ikke",
                [202503],
                [(202500, 0), (202501, 1), (202502, 2), (202503, 30)],
        ),
        (
                "overskriv_alt",
                [202501, 202502, 202503],
                [(202500, 0), (202501, 10), (202502, 20), (202503, 30)],
        ),
        (
                "overskriv_siste_periode",
                [202503],
                [(202500, 0), (202501, 1), (202502, 2), (202503, 30)],
        ),
    ],
)
def test_process_aggregate_only_masks_selected_periods(
        oracle_connection, monkeypatch, tmp_path, mode, loaded, expected
):
    table = f"sb_test_{uuid4().hex[:12]}"
    config = configuration()
    config.connection.schema_name = "system"
    aggregate = config.smale_aggregater[0]
    aggregate.tabell = table
    aggregate.inkrementell = InkrementalMode(mode)
    (tmp_path / aggregate.sporring_sql).write_text(
        "select 202501 as periode, 'Oslo' as region, 10 as antall from dual "
        "union all select 202502, 'Oslo', 20 from dual "
        "union all select 202503, 'Oslo', 30 from dual;"
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
                "periode NUMBER, region VARCHAR2(100), antall NUMBER)"
            )
            cursor.execute(
                f"CREATE TABLE SYSTEM.{kontroll_table} ("
                "periode NUMBER, region VARCHAR2(100), "
                "antall NUMBER, primary NUMBER(1), suppressed NUMBER(1))"
            )
            existing = [(202500, "Oslo", 0), (202501, "Oslo", 1), (202502, "Oslo", 2)]
            if mode == "overskriv_siste_periode":
                # Make 202503 the existing latest period so this mode must replace it.
                existing.append((202503, "Oslo", 3))
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
        oracle_connection.commit()
