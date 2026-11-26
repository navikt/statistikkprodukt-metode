from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest
from oracledb.connection import Connection

from statistikkprodukt import oracle
from statistikkprodukt.app.last import last
from statistikkprodukt.app.skjema import create_tables
from statistikkprodukt.app.testenv import (
    CONTAINER_PORT,
    ORACLE_PASSWORD,
    ORACLE_SERVICE,
    ORACLE_USER,
)
from statistikkprodukt.config import (
    DATABASE_PROFILES,
    IncrementalMode,
    StorageOption,
    load_configuration,
)

pytestmark = pytest.mark.r_dependent

SCHEMA = "SYSTEM"
TELLEPUNKTER = ("antall", "antall_kvinner")
LEGG_TIL = IncrementalMode.LEGG_TIL_NYE_PERIODER
OVERSKRIV = IncrementalMode.OVERSKRIV_EKSISTERENDE_PERIODER

# Kildedata der ingen celler prikkes, slik at forventet innhold kan regnes ut direkte.
# Rader: (periode, region, antall, antall_kvinner)
KILDE_START = [
    ("2025M01", "Bergen", 20, 12),
    ("2025M01", "Oslo", 30, 15),
    ("2025M02", "Bergen", 25, 11),
    ("2025M02", "Oslo", 40, 20),
]
# 2025M01 er endret, 2025M02 er fjernet fra kilden og 2025M03 er ny.
KILDE_ENDRET = [
    ("2025M01", "Bergen", 22, 13),
    ("2025M01", "Oslo", 35, 18),
    ("2025M03", "Bergen", 50, 25),
    ("2025M03", "Oslo", 60, 30),
]


def _filtrer_perioder(kilde: list[tuple], *perioder: str) -> list[tuple]:
    return [rad for rad in kilde if rad[0] in perioder]


def _forventet_prepub(kilde: list[tuple]) -> list[tuple]:
    """Regner ut prepub-innholdet (med "I alt"-rader) for upriket kildedata."""
    totaler: dict[str, list[int]] = defaultdict(lambda: [0] * len(TELLEPUNKTER))
    for periode, _region, *verdier in kilde:
        totaler[periode] = [a + b for a, b in zip(totaler[periode], verdier)]
    return sorted(
        [tuple(rad) for rad in kilde]
        + [(periode, "I alt", *verdier) for periode, verdier in totaler.items()]
    )


def _forventet_kontroll(prepub: list[tuple]) -> list[tuple]:
    """Kontrolltabellen skal speile prepub med én rad per tellepunkt."""
    return sorted(
        (periode, region, tellepunkt, verdi, 0, 0)
        for periode, region, *verdier in prepub
        for tellepunkt, verdi in zip(TELLEPUNKTER, verdier)
    )


@dataclass
class E2EMiljo:
    """Et aggregat med kildetabell i testdatabasen og hjelpere for å laste og lese det."""

    connection: Connection
    config_path: Path
    source_table: str
    aggregate_table: str

    def konfigurer(self, strategi: IncrementalMode, kilde: list[tuple]) -> None:
        """Skriver aggregatkonfigurasjonen, fyller kildetabellen og oppretter måltabellene."""
        aggregate_directory = self.config_path.parent / "aggregater"
        aggregate_directory.mkdir(exist_ok=True)
        (aggregate_directory / f"{self.aggregate_table}.yml").write_text(
            _aggregat_yml(self.source_table, strategi), encoding="utf-8"
        )
        (aggregate_directory / f"{self.aggregate_table}.sql").write_text(
            f"""
select periode, region, sum(antall) as antall,
       sum(antall_kvinner) as antall_kvinner
from {self.source_table}
group by periode, region
""",
            encoding="utf-8",
        )
        with self.connection.cursor() as cursor:
            cursor.execute(
                f"CREATE TABLE {SCHEMA}.{self.source_table} ("
                "periode varchar2(7 CHAR), region VARCHAR2(100 CHAR), "
                "antall NUMBER, antall_kvinner NUMBER)"
            )
        self.sett_kilde_data(kilde)
        create_tables(*load_configuration(self.config_path), create_in_database=True)

    def sett_kilde_data(self, kilde: list[tuple]) -> None:
        """Erstatter innholdet i kildetabellen."""
        with self.connection.cursor() as cursor:
            cursor.execute(f"DELETE FROM {SCHEMA}.{self.source_table}")
            cursor.executemany(
                f"INSERT INTO {SCHEMA}.{self.source_table} VALUES (:1, :2, :3, :4)",
                kilde,
            )
        self.connection.commit()

    def kjor_last(self, **kwargs) -> None:
        configuration, config_directory = load_configuration(self.config_path)
        last(
            configuration, config_directory, StorageOption.DB, StorageOption.DB,
            Path("dummy"), Path("dummy"), dry_run=False, **kwargs,
        )

    def _hent(self, sql: str, **parametre) -> list[tuple]:
        with self.connection.cursor() as cursor:
            cursor.execute(sql, **parametre)
            return cursor.fetchall()

    def les_prepub_tabell(self) -> list[tuple]:
        return sorted(self._hent(
            f"SELECT periode, region, {', '.join(TELLEPUNKTER)} "
            f"FROM {SCHEMA}.{self.aggregate_table}_prepub"
        ))

    def les_kontroll_tabell(self) -> list[tuple]:
        return sorted(self._hent(
            "SELECT periode, region, tellepunkt, verdi, primary, suppressed "
            f"FROM {SCHEMA}.{self.aggregate_table}_kontroll"
        ))

    def les_meta_tabell(self) -> dict[tuple, str | None]:
        return {
            (row_type, keyword, language): value
            for row_type, keyword, language, value in self._hent(
                f"SELECT type, keyword, language, value FROM {SCHEMA}.{self.aggregate_table}_meta"
            )
        }

    def les_logg_tabell(self) -> list[tuple]:
        return self._hent(
            "SELECT status, aggregat_id, tabell, navn, antall_rader, antall_prikket "
            f"FROM {SCHEMA}.{oracle.LOG_TABLE} WHERE tabell = :tabell ORDER BY sluttid",
            tabell=self.aggregate_table,
        )

    def assert_innhold(self, kilde: list[tuple]) -> None:
        """Kontrollerer prepub mot forventet kildedata og at kontroll speiler prepub."""
        forventet = _forventet_prepub(kilde)
        assert self.les_prepub_tabell() == forventet
        assert self.les_kontroll_tabell() == _forventet_kontroll(forventet)

    def teardown(self) -> None:
        with self.connection.cursor() as cursor:
            for table in (
                    f"{self.aggregate_table}_prepub",
                    f"{self.aggregate_table}_kontroll",
                    f"{self.aggregate_table}_meta",
                    self.source_table,
            ):
                if oracle.table_exists(self.connection, SCHEMA, table):
                    cursor.execute(f"DROP TABLE {SCHEMA}.{table} PURGE")
            if oracle.table_exists(self.connection, SCHEMA, oracle.LOG_TABLE):
                cursor.execute(
                    f"DELETE FROM {SCHEMA}.{oracle.LOG_TABLE} WHERE tabell = :tabell",
                    tabell=self.aggregate_table,
                )
                self.connection.commit()


def _aggregat_yml(source_table: str, strategi: IncrementalMode) -> str:
    return f"""
version: 1
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
  inkrementell-strategi: {strategi.value}
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
"""


@pytest.fixture
def e2e(oracle_connection, monkeypatch, tmp_path):
    """Gir et E2E-miljø mot testdatabasen og rydder opp tabellene etterpå."""
    suffix = uuid4().hex[:12]
    aggregate_table = f"ost_e2e{suffix}_agg"

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
    config_path = tmp_path / "konfigurasjon.yml"
    config_path.write_text(
        f"""
version: 1
connection:
  database: DWHP
  schema: {SCHEMA}
  user: {ORACLE_USER}
  password: env://STATISTIKKPRODUKT_E2E_PASSWORD
mapper:
  - aggregater/**
aggregater:
  - {aggregate_table}
""",
        encoding="utf-8",
    )
    miljo = E2EMiljo(
        connection=oracle_connection,
        config_path=config_path,
        source_table=f"obt_e2e_{suffix}",
        aggregate_table=aggregate_table,
    )
    try:
        yield miljo
    finally:
        miljo.teardown()


def test_e2e_run(e2e):
    """Oppretter og fyller ett småaggregat fra en OBT-tabell, inkludert prikking."""
    e2e.konfigurer(OVERSKRIV, [
        ("2025M01", "Bergen", 7, 7),
        ("2025M01", "Oslo", 10, 10),
        ("2025M02", "Bergen", 6, 6),
        ("2025M02", "Oslo", 3, 3),
    ])
    e2e.kjor_last()

    expected_prepub = [
        ("2025M01", "I alt", 17, 17),
        ("2025M01", "Bergen", 7, 7),
        ("2025M01", "Oslo", 10, 10),
        ("2025M02", "I alt", 9, 9),
        ("2025M02", "Bergen", None, None),
        ("2025M02", "Oslo", None, None),
    ]
    expected_kontroll_per_tellepunkt = [
        ("2025M01", "I alt", 17, 0, 0),
        ("2025M01", "Bergen", 7, 0, 0),
        ("2025M01", "Oslo", 10, 0, 0),
        ("2025M02", "I alt", 9, 0, 0),
        ("2025M02", "Bergen", 6, 0, 1),
        ("2025M02", "Oslo", 3, 1, 1),
    ]
    expected_kontroll = [
        (*row[:2], tellepunkt, *row[2:])
        for tellepunkt in TELLEPUNKTER
        for row in expected_kontroll_per_tellepunkt
    ]

    assert e2e.les_prepub_tabell() == sorted(expected_prepub)
    assert e2e.les_kontroll_tabell() == sorted(expected_kontroll)

    meta = e2e.les_meta_tabell()
    assert meta["PX", "MATRIX", "NO"] == "21000"
    assert meta["PX", "TITLE", "NO"] == "Aggregerte verdier"
    [(siste_lastetid,)] = e2e._hent(
        f"SELECT MAX(dvh_lastet_tid) FROM {SCHEMA}.{e2e.aggregate_table}_prepub"
    )
    assert siste_lastetid is not None
    assert meta["PX", "LAST-UPDATED", None] == siste_lastetid.strftime("%Y-%m-%d %H:%M:%S")
    assert meta["CS", "TIMEVAL", None] == "periode"
    assert e2e.les_logg_tabell() == [("OK", "21000", e2e.aggregate_table, "Aggregerte verdier", 6, 4)]


def test_e2e_legg_til_nye_perioder(e2e):
    """Bare nye perioder lastes; eksisterende perioder beholdes uendret."""
    e2e.konfigurer(LEGG_TIL, KILDE_START)
    e2e.kjor_last()
    e2e.assert_innhold(KILDE_START)

    e2e.sett_kilde_data(KILDE_ENDRET)
    e2e.kjor_last()

    e2e.assert_innhold(KILDE_START + _filtrer_perioder(KILDE_ENDRET, "2025M03"))


def test_e2e_overskriv_eksisterende_perioder(e2e):
    """Alle perioder i kilden lastes på nytt; perioder som mangler i kilden beholdes."""
    e2e.konfigurer(OVERSKRIV, KILDE_START)
    e2e.kjor_last()
    e2e.assert_innhold(KILDE_START)

    e2e.sett_kilde_data(KILDE_ENDRET)
    e2e.kjor_last()

    e2e.assert_innhold(KILDE_ENDRET + _filtrer_perioder(KILDE_START, "2025M02"))


@pytest.mark.parametrize("strategi", [LEGG_TIL, OVERSKRIV])
def test_e2e_tom_kilde_fra_starten(e2e, strategi):
    """En tom kilde gir tomme måltabeller, metadata og en vellykket kjørelogg."""
    e2e.konfigurer(strategi, [])
    e2e.kjor_last()

    e2e.assert_innhold([])
    meta = e2e.les_meta_tabell()
    assert meta["PX", "MATRIX", "NO"] == "21000"
    assert meta["PX", "TITLE", "NO"] == "Aggregerte verdier"
    assert meta["PX", "LAST-UPDATED", None] is None
    assert meta["CS", "TIMEVAL", None] == "periode"
    assert e2e.les_logg_tabell() == [
        ("OK", "21000", e2e.aggregate_table, "Aggregerte verdier", 0, 0)
    ]


@pytest.mark.parametrize("strategi", [LEGG_TIL, OVERSKRIV])
def test_e2e_inkrementell_beholder_perioder_som_forsvinner_fra_kilden(e2e, strategi):
    """Perioder beholdes i måltabellene selv om noen eller alle forsvinner fra kilden."""
    e2e.konfigurer(strategi, KILDE_START)
    e2e.kjor_last()
    e2e.assert_innhold(KILDE_START)

    e2e.sett_kilde_data(_filtrer_perioder(KILDE_START, "2025M01"))
    e2e.kjor_last()
    e2e.assert_innhold(KILDE_START)

    e2e.sett_kilde_data([])
    e2e.kjor_last()
    e2e.assert_innhold(KILDE_START)


@pytest.mark.parametrize("strategi", [LEGG_TIL, OVERSKRIV])
def test_e2e_inkrementell_uten_nye_rader(e2e, strategi):
    """En ny kjøring med uendret kilde gir samme innhold og ingen duplikater."""
    e2e.konfigurer(strategi, KILDE_START)
    e2e.kjor_last()
    e2e.kjor_last()

    e2e.assert_innhold(KILDE_START)
    assert [rad[0] for rad in e2e.les_logg_tabell()] == ["OK", "OK"]


@pytest.mark.parametrize("strategi", [LEGG_TIL, OVERSKRIV])
def test_e2e_full_relast(e2e, strategi):
    """Full relast erstatter hele innholdet, også perioder som er fjernet fra kilden."""
    e2e.konfigurer(strategi, KILDE_START)
    e2e.kjor_last()

    e2e.sett_kilde_data(KILDE_ENDRET)
    e2e.kjor_last(full_relast=True)

    e2e.assert_innhold(KILDE_ENDRET)


@pytest.mark.parametrize("strategi", [LEGG_TIL, OVERSKRIV])
def test_e2e_valgte_perioder(e2e, strategi):
    """Valgte perioder lastes på nytt uansett strategi; andre perioder røres ikke."""
    e2e.konfigurer(strategi, KILDE_START)
    e2e.kjor_last()

    e2e.sett_kilde_data(KILDE_ENDRET)
    e2e.kjor_last(selected_periods=["2025M01"])

    e2e.assert_innhold(
        _filtrer_perioder(KILDE_ENDRET, "2025M01") + _filtrer_perioder(KILDE_START, "2025M02")
    )
