import pytest
from pydantic import ValidationError

from statistikkprodukt.config import ConfigError, Configuration, load_configuration, resolve_secret


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
            "smale_aggregater": [
                {
                    "nav_id": "NAV21000",
                    "navn": "Navn",
                    "navn_en": "Name",
                    "innhold": "Innhold",
                    "innhold_en": "Content",
                    "tabell": "TABELL",
                    "obt_tabell": "OBT_TABELL",
                    "sporring_sql": "select.sql",
                    "periodisering": "periode",
                    "inkrementell": "overskriv_ikke",
                    "tallkolonne": {"kolonne": "antall"},
                    "formula": "~ periode / region",
                    "prikking": True,
                    "saerskilt": False,
                    "variabler": [{"kolonne": "periode"}, {"kolonne": "region"}],
                }
            ],
        }
    )


def test_configuration_rejects_unknown_keys():
    """Sikrer at ukjente konfigurasjonsnøkler avvises."""
    config = configuration().model_dump()
    config["unknown"] = True
    with pytest.raises(ValidationError):
        Configuration.model_validate(config)


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
smale_aggregater:
  - tabell
""",
        encoding="utf-8",
    )
    for table in ("tabell", "annen"):
        (aggregate_directory / f"{table}.yml").write_text(
            f"""
definisjon:
  navn: {table}
  navn_en: {table}
  innhold: {table} 
  innhold_en: {table}
  nav_id: NAV21000
  obt_tabell: OBT_TABELL
  periodisering: periode
  inkrementell: overskriv_ikke
  tallkolonne:
    kolonne: antall
  formula: "~ periode * region"
  prikking: true
  saerskilt: false
  variabler:
    - kolonne: periode
    - kolonne: region
""",
            encoding="utf-8",
        )

    config, config_directory = load_configuration(tmp_path / "konfigurasjon.yml")

    assert config_directory == tmp_path
    assert [aggregate.tabell for aggregate in config.smale_aggregater] == ["tabell"]
    assert config.smale_aggregater[0].obt_tabell == "obt_tabell"
    assert config.smale_aggregater[0].sporring_sql == "aggregater/tabell.sql"


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
smale_aggregater:
  - annet_navn
""",
        encoding="utf-8",
    )
    (aggregate_directory / "annet_navn.yml").write_text(
        """
definisjon:
  navn: dummy
  navn_en: dummy
  innhold: dummy 
  innhold_en: dummy
  nav_id: NAV21000
  obt_tabell: OBT_TABELL
  periodisering: periode
  inkrementell: overskriv_ikke
  tallkolonne:
    kolonne: antall
  formula: "~ periode * region"
  prikking: true
  saerskilt: false
  variabler:
    - kolonne: periode
    - kolonne: region
""",
        encoding="utf-8",
    )

    config, _ = load_configuration(tmp_path / "konfigurasjon.yml")

    assert config.smale_aggregater[0].tabell == "annet_navn"
    assert config.smale_aggregater[0].sporring_sql == "aggregater/annet_navn.sql"


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
smale_aggregater:
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
