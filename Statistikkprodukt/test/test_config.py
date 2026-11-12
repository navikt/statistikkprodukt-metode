import pytest
from pydantic import ValidationError

from statistikkprodukt.config import (
    ConfigError,
    Configuration,
    Tellepunkt,
    load_configuration,
    resolve_secret,
)


def configuration():
    """Bygger en gyldig konfigurasjon for en liten aggregattabell."""
    return Configuration.model_validate(
        {
            "connection": {
                "database": "DWHP",
                "schema": "TEST",
                "user": "testuser",
                "password": "env://TEST_PASSWORD",
            },
            "aggregater": [
                {
                    "id": "21000",
                    "navn": "Navn",
                    "navn-en": "Name",
                    "innhold": "Innhold",
                    "innhold-en": "Content",
                    "tabell": "TABELL",
                    "obt-tabell": "OBT_TABELL",
                    "sporring_sql": "select.sql",
                    "periodisering": {
                        "navn": "Periodisering",
                        "navn-en": "Periodization",
                        "kolonne": "periode",
                        "innhold": "Månedsperiode",
                        "innhold-en": "Month period",
                    },
                    "inkrementell-strategi": "overskriv_ikke",
                    "tellepunkter": [{"kolonne": "antall"}],
                    "formula": "~ periode / region",
                    "prikking": True,
                    "saerskilt": False,
                    "variabler": [{"kolonne": "region"}],
                }
            ],
        }
    )


def test_configuration_rejects_unknown_keys():
    """Sikrer at ukjente konfigurasjonsnøkler avvises."""
    config = configuration().model_dump(by_alias=True)
    config["unknown"] = True
    with pytest.raises(ValidationError):
        Configuration.model_validate(config)


def test_aggregate_id_does_not_include_nav_prefix():
    """Krever aggregat-ID i tallformat uten NAV-prefiks."""
    config = configuration().model_dump(by_alias=True)
    config["aggregater"][0]["id"] = "NAV21000"

    with pytest.raises(ValidationError):
        Configuration.model_validate(config)


def test_aggregate_id_integer_is_converted_to_string():
    """Konverterer en numerisk aggregat-ID til streng."""
    config = configuration().model_dump(by_alias=True)
    config["aggregater"][0]["id"] = 21000

    assert Configuration.model_validate(config).aggregater[0].id == "21000"


def test_loads_aggregate_configurations_matching_mapper(tmp_path):
    """Leser en konfigurasjonsfil per aggregat fra mønsteret i hovedfilen."""
    aggregate_directory = tmp_path / "aggregater"
    aggregate_directory.mkdir()
    (tmp_path / "konfigurasjon.yml").write_text(
        """
connection:
  database: DWHP
  schema: TEST
  user: testuser
  password: env://TEST_PASSWORD
mapper:
  - aggregater/**
aggregater:
  - tabell
""",
        encoding="utf-8",
    )
    for table in ("tabell", "annen"):
        (aggregate_directory / f"{table}.yml").write_text(
            f"""
definisjon:
  navn: {table}
  navn-en: {table}
  innhold: {table} 
  innhold-en: {table}
  id: 21000
  obt-tabell: OBT_TABELL
  periodisering:
    navn: Periode
    navn-en: Period
    kolonne: periode
    innhold: Periode
    innhold-en: Month
  inkrementell-strategi: overskriv_ikke
  tellepunkter:
    - navn: Antall
      navn-en: Count
      kolonne: antall
      innhold: Antall i gruppen
      innhold-en: Count in group
  formula: "~ periode * region"
  prikking: true
  saerskilt: false
  variabler:
    - navn: Region
      navn-en: Region
      kolonne: region
      innhold: Område
      innhold-en: Area
""",
            encoding="utf-8",
        )

    config, config_directory = load_configuration(tmp_path / "konfigurasjon.yml")

    assert config_directory == tmp_path
    assert [aggregate.tabell for aggregate in config.aggregater] == ["tabell"]
    aggregate = config.aggregater[0]
    assert aggregate.obt_tabell == "obt_tabell"
    assert aggregate.sporring_sql == "aggregater/tabell.sql"
    assert aggregate.periodisering.kolonne == "periode"
    assert aggregate.inkrementell_strategi.value == "overskriv_ikke"
    assert isinstance(aggregate.tellepunkter[0], Tellepunkt)
    assert aggregate.tellepunkter[0].kolonne == "antall"


def test_aggregate_table_and_sql_are_inferred_from_filename(tmp_path):
    """Utleder tabell og SQL-sti fra aggregatfilen."""
    aggregate_directory = tmp_path / "aggregater"
    aggregate_directory.mkdir()
    (tmp_path / "konfigurasjon.yml").write_text(
        """
connection:
  database: DWHP
  schema: TEST
  user: testuser
  password: env://TEST_PASSWORD
mapper:
  - aggregater/**
aggregater:
  - annet_navn
""",
        encoding="utf-8",
    )
    (aggregate_directory / "annet_navn.yml").write_text(
        """
definisjon:
  navn: dummy
  navn-en: dummy
  innhold: dummy 
  innhold-en: dummy
  id: "21000"
  obt-tabell: OBT_TABELL
  periodisering:
    kolonne: periode
  inkrementell-strategi: overskriv_ikke
  tellepunkter:
    - kolonne: antall
  formula: "~ periode * region"
  prikking: true
  saerskilt: false
  variabler:
    - kolonne: region
""",
        encoding="utf-8",
    )

    config, _ = load_configuration(tmp_path / "konfigurasjon.yml")

    assert config.aggregater[0].tabell == "annet_navn"
    assert config.aggregater[0].sporring_sql == "aggregater/annet_navn.sql"


def test_aggregate_names_must_be_unique_across_search_paths(tmp_path):
    """Avviser samme aggregatnavn i flere mapper."""
    for directory in ("første", "andre"):
        aggregate_directory = tmp_path / directory
        aggregate_directory.mkdir()
        (aggregate_directory / "tabell.yml").write_text("", encoding="utf-8")
    (tmp_path / "konfigurasjon.yml").write_text(
        """
connection:
  database: DWHP
  schema: TEST
  user: testuser
  password: env://TEST_PASSWORD
mapper:
  - første/**
  - andre/**
aggregater:
  - tabell
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="finnes i flere konfigurasjonsfiler"):
        load_configuration(tmp_path / "konfigurasjon.yml")


def test_resolve_env_reference(monkeypatch):
    """Slår opp miljøvariabelreferanser for hemmeligheter."""
    monkeypatch.setenv("TEST_PASSWORD", "secret")
    assert resolve_secret("env://TEST_PASSWORD", password=True) == "secret"
