from pathlib import Path
from uuid import uuid4

import pytest
from statistikkprodukt import oracle
from statistikkprodukt.app.last import last
from statistikkprodukt.app.skjema import create_tables
from statistikkprodukt.config import DATABASE_PROFILES, load_configuration, StorageOption
from statistikkprodukt.app.testenv import (
    CONTAINER_PORT,
    ORACLE_PASSWORD,
    ORACLE_SERVICE,
    ORACLE_USER,
)


@pytest.mark.r_dependent
def test_e2e_run(
        oracle_connection, monkeypatch, tmp_path
):
    """Oppretter og fyller ett småaggregat fra en OBT-tabell."""
    suffix = uuid4().hex[:12]
    source_table = f"obt_e2e_{suffix}"
    aggregate_table = f"ost_e2e{suffix}_agg"
    log_table = oracle.LOG_TABLE
    aggregate_directory = tmp_path / "aggregater"
    aggregate_directory.mkdir()

    monkeypatch.setenv("STATISTIKKPRODUKT_E2E_PASSWORD", ORACLE_PASSWORD)
    monkeypatch.setitem(
        DATABASE_PROFILES,
        "DWHP",
        {
            "host": "127.0.0.1",
            "port": CONTAINER_PORT,
            "service_name": ORACLE_SERVICE,
        },
    )

    (tmp_path / "konfigurasjon.yml").write_text(
        f"""
connection:
  database: DWHP
  schema: SYSTEM
  user: {ORACLE_USER}
  password: env://STATISTIKKPRODUKT_E2E_PASSWORD
mapper:
  - aggregater/**
aggregater:
  - {aggregate_table}
""",
        encoding="utf-8",
    )
    (aggregate_directory / f"{aggregate_table}.yml").write_text(
        f"""
definisjon:
  id: "21000"
  referanse-tabell: {source_table}
  navn: Aggregerte verdier
  navn-en: Aggregated values
  innhold: Verdier gruppert etter periode og region.
  innhold-en: Values grouped by period and region.
  periodisering:
    navn: Periode
    navn-en: Period
    kolonne: periode
    innhold: Periode.
    innhold-en: Period.
  inkrementell-strategi: overskriv_alt
  tellepunkter:
    - navn: Antall
      navn-en: Count
      kolonne: antall
      innhold: Summert antall.
      innhold-en: Summed count.
    - navn: Antall kvinner
      navn-en: Number of women
      kolonne: antall_kvinner
      innhold: Summert antall kvinner.
      innhold-en: Summed number of women.
  formula: "~ periode / region"
  prikking: true
  saerskilt: false
  variabler:
    - navn: Region
      navn-en: Region
      kolonne: region
      innhold: Geografisk område.
      innhold-en: Geographic area.
""",
        encoding="utf-8",
    )
    (aggregate_directory / f"{aggregate_table}.sql").write_text(
        f"""
select periode, region, sum(antall) as antall,
       sum(antall_kvinner) as antall_kvinner
from {source_table}
group by periode, region
""",
        encoding="utf-8",
    )

    try:
        with oracle_connection.cursor() as cursor:
            cursor.execute(
                f"CREATE TABLE SYSTEM.{source_table} ("
                "periode varchar2(6 CHAR), region VARCHAR2(100 CHAR), "
                "antall NUMBER, antall_kvinner NUMBER)"
            )
            cursor.executemany(
                f"INSERT INTO SYSTEM.{source_table} VALUES (:1, :2, :3, :4)",
                [
                    ('202501', "Bergen", 7.0, 7.0),
                    ('202501', "Oslo", 10.0, 10.0),
                    ('202502', "Bergen", 6.0, 6.0),
                    ('202502', "Oslo", 3.0, 3.0),
                ],
            )
        oracle_connection.commit()

        configuration, config_directory = load_configuration(
            tmp_path / "konfigurasjon.yml"
        )

        create_tables(
            configuration, config_directory, create_in_database=True
        )
        last(configuration, config_directory, StorageOption.DB, StorageOption.DB, Path("dummy"), Path("dummy"), dry_run=False)

        with oracle_connection.cursor() as cursor:
            cursor.execute(
                f"SELECT periode, region, antall, antall_kvinner "
                f"FROM SYSTEM.{aggregate_table}_prepub ORDER BY periode, region"
            )
            prepub_rows = cursor.fetchall()
            cursor.execute(
                f"SELECT periode, region, tellepunkt, verdi, primary, suppressed "
                f"FROM SYSTEM.{aggregate_table}_kontroll "
                f"ORDER BY periode, region, tellepunkt"
            )
            kontroll_rows = cursor.fetchall()

        expected_prepub = [
            ('202501', "I alt", 17, 17),
            ('202501', "Bergen", 7, 7),
            ('202501', "Oslo", 10, 10),
            ('202502', "I alt", 9, 9),
            ('202502', "Bergen", None, None),
            ('202502', "Oslo", None, None)
        ]
        expected_kontroll_per_tellepunkt = [
            ('202501', "I alt", "antall", 17, 0, 0),
            ('202501', "Bergen", "antall", 7, 0, 0),
            ('202501', "Oslo", "antall", 10, 0, 0),
            ('202502', "I alt", "antall", 9, 0, 0),
            ('202502', "Bergen", "antall", 6, 0, 1),
            ('202502', "Oslo", "antall", 3, 1, 1),
        ]
        expected_kontroll = expected_kontroll_per_tellepunkt + [
            (*row[:2], "antall_kvinner", *row[3:])
            for row in expected_kontroll_per_tellepunkt
        ]

        assert sorted(prepub_rows) == sorted(expected_prepub)
        assert sorted(kontroll_rows) == sorted(expected_kontroll)

        with oracle_connection.cursor() as cursor:
            cursor.execute(
                f"SELECT type, keyword, language, value FROM SYSTEM.{aggregate_table}_meta"
            )
            meta = {(row_type, keyword, language): value
                    for row_type, keyword, language, value in cursor.fetchall()}
            cursor.execute(
                "SELECT status, aggregat_id, tabell, navn, antall_rader, antall_prikket "
                f"FROM SYSTEM.{log_table} WHERE tabell = :tabell",
                tabell=aggregate_table,
            )
            log_rows = [
                (status, aggregat_id, table, name, count, masked)
                for status, aggregat_id, table, name, count, masked in cursor.fetchall()
            ]

        assert meta["PX", "MATRIX", None] == "21000"
        assert meta["PX", "TITLE", "NO"] == "Aggregerte verdier"
        assert meta["CS", "TIMEVAL", None] == "periode"
        assert log_rows == [("OK", "21000", aggregate_table, "Aggregerte verdier", 6, 4)]
    finally:
        with oracle_connection.cursor() as cursor:
            for table in (
                f"{aggregate_table}_prepub",
                f"{aggregate_table}_kontroll",
                f"{aggregate_table}_meta",
                source_table,
            ):
                if oracle.table_exists(oracle_connection, "SYSTEM", table):
                    cursor.execute(f"DROP TABLE SYSTEM.{table} PURGE")
            if oracle.table_exists(oracle_connection, "SYSTEM", log_table):
                cursor.execute(
                    f"DELETE FROM SYSTEM.{log_table} WHERE tabell = :tabell",
                    tabell=aggregate_table,
                )
                oracle_connection.commit()
