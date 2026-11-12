from pathlib import Path
from uuid import uuid4

from statistikkprodukt import app, oracle
from statistikkprodukt.config import DATABASE_PROFILES, load_configuration, StorageOption
from statistikkprodukt.testenv import (
    CONTAINER_PORT,
    ORACLE_PASSWORD,
    ORACLE_SERVICE,
    ORACLE_USER,
)


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
  obt-tabell: {source_table}
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
select periode, region, sum(antall) as antall
from {source_table}
group by periode, region
""",
        encoding="utf-8",
    )

    try:
        with oracle_connection.cursor() as cursor:
            cursor.execute(
                f"CREATE TABLE SYSTEM.{source_table} ("
                "periode varchar2(6 CHAR), region VARCHAR2(100 CHAR), antall NUMBER)"
            )
            cursor.executemany(
                f"INSERT INTO SYSTEM.{source_table} VALUES (:1, :2, :3)",
                [
                    ('202501', "Bergen", 7.0),
                    ('202501', "Oslo", 10.0),
                    ('202502', "Bergen", 6.0),
                    ('202502', "Oslo", 3.0),
                ],
            )
        oracle_connection.commit()

        configuration, config_directory = load_configuration(
            tmp_path / "konfigurasjon.yml"
        )

        app.create_tables(
            configuration, config_directory, create_in_database=True
        )
        app.run(configuration, config_directory, StorageOption.DB, StorageOption.DB, Path("dummy"), Path("dummy"), dry_run=False)

        with oracle_connection.cursor() as cursor:
            cursor.execute(
                f"SELECT periode, region, antall "
                f"FROM SYSTEM.{aggregate_table}_prepub ORDER BY periode, region"
            )
            prepub_rows = cursor.fetchall()
            cursor.execute(
                f"SELECT periode, region, antall, primary, suppressed "
                f"FROM SYSTEM.{aggregate_table}_kontroll ORDER BY periode, region"
            )
            kontroll_rows = cursor.fetchall()

        expected_prepub = [
            ('I alt', "I alt", 17),
            ('202501', "I alt", 17),
            ('202501', "Bergen", 7),
            ('202501', "Oslo", 10),
            ('I alt', "I alt", 9),
            ('202502', "I alt", 9),
            ('202502', "Bergen", None),
            ('202502', "Oslo", None)
        ]
        expected_kontroll = [
            ('I alt', "I alt", 17, 0, 0),
            ('202501', "I alt", 17, 0, 0),
            ('202501', "Bergen", 7, 0, 0),
            ('202501', "Oslo", 10, 0, 0),
            ('I alt', "I alt", 9, 0, 0),
            ('202502', "I alt", 9, 0, 0),
            ('202502', "Bergen", 6, 0, 1),
            ('202502', "Oslo", 3, 1, 1)
        ]

        assert sorted(prepub_rows) == sorted(expected_prepub)
        assert sorted(kontroll_rows) == sorted(expected_kontroll)

        with oracle_connection.cursor() as cursor:
            cursor.execute(f"SELECT felt, verdi FROM SYSTEM.{aggregate_table}_meta")
            meta = {field: value.read() if value is not None else None for field, value in cursor.fetchall()}
            cursor.execute(
                "SELECT status, aggregat_id, tabell, navn, antall_rader, antall_prikket "
                f"FROM SYSTEM.{log_table} WHERE tabell = :tabell",
                tabell=aggregate_table,
            )
            log_rows = [
                (status, aggregat_id, table, name.read(), count, masked)
                for status, aggregat_id, table, name, count, masked in cursor.fetchall()
            ]

        assert meta["aggregat_id"] == "21000"
        assert meta["navn"] == "Aggregerte verdier"
        assert meta["periodisering"] == "periode"
        assert log_rows == [("OK", "21000", aggregate_table, "Aggregerte verdier", 8, 2)]
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
