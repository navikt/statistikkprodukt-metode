from unittest.mock import Mock

import pytest
import yaml
from pydantic import ValidationError

from statistikkprodukt.config import (
    AggregateConfiguration,
    ConfigError,
    Configuration,
    ConfigurationSource,
    Tellepunkt,
    load_configuration,
    resolve_config,
)
from statistikkprodukt.config.loader import _find_aggregate_configurations


def configuration():
    """Bygger en gyldig konfigurasjon for en liten aggregattabell."""
    return Configuration.model_validate(
        {
            "version": 1,
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
                    "referanse-tabell": "OBT_TABELL",
                    "sporring_sql": "select.sql",
                    "periodisering": {
                        "navn": "Periodisering",
                        "navn-en": "Periodization",
                        "kolonne": "periode",
                        "innhold": "Månedsperiode",
                        "innhold-en": "Month period",
                    },
                    "inkrementell-strategi": "legg_til_nye_perioder",
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


def test_configuration_version_is_required():
    config = configuration().model_dump(by_alias=True)
    config.pop("version")

    with pytest.raises(ValidationError):
        Configuration.model_validate(config)


def test_configuration_rejects_unsupported_version():
    config = configuration().model_dump(by_alias=True)
    config["version"] = 2

    with pytest.raises(ValidationError):
        Configuration.model_validate(config)


def test_source_and_aggregate_configuration_versions_are_required():
    config = configuration()
    with pytest.raises(ValidationError):
        ConfigurationSource.model_validate(
            {
                "connection": config.connection.model_dump(by_alias=True),
                "mapper": ["aggregater/**"],
                "aggregater": ["tabell"],
            }
        )
    with pytest.raises(ValidationError):
        AggregateConfiguration.model_validate(
            {"definisjon": config.aggregater[0].model_dump(by_alias=True)}
        )


def test_source_and_aggregate_configuration_reject_unsupported_versions():
    config = configuration()
    with pytest.raises(ValidationError):
        ConfigurationSource.model_validate(
            {
                "version": 2,
                "connection": config.connection.model_dump(by_alias=True),
                "mapper": ["aggregater/**"],
                "aggregater": ["tabell"],
            }
        )
    with pytest.raises(ValidationError):
        AggregateConfiguration.model_validate(
            {
                "version": 2,
                "definisjon": config.aggregater[0].model_dump(by_alias=True),
            }
        )


@pytest.mark.parametrize(
    ("subject_code", "subject_area"),
    [
        ("AM", "Arbeidsmarked"),
        ("SF", "Sykefravær"),
        ("AU", "Arbeidsavklaringspenger og uføretrygd"),
        ("FA", "Familie"),
        ("OT", "Oppfølging og tiltak"),
        ("PE", "Pensjon"),
        ("SB", "Saksbehandling"),
        ("GA", "Flere statistikkområder"),
    ],
)
def test_statistikkbank_accepts_subject_code_and_area_mapping(subject_code, subject_area):
    config = configuration().model_dump(by_alias=True)
    config["aggregater"][0]["statistikkbank"] = {
        "subject-code": subject_code,
        "subject-area": subject_area,
    }

    parsed = Configuration.model_validate(config)

    assert parsed.aggregater[0].statistikkbank.subject_code == subject_code


def test_statistikkbank_subject_mapping_error_includes_mapping():
    config = configuration().model_dump(by_alias=True)
    config["aggregater"][0]["statistikkbank"] = {
        "subject-code": "AM",
        "subject-area": "Familie",
    }

    with pytest.raises(ValidationError) as error:
        Configuration.model_validate(config)

    assert (
        "AM: Arbeidsmarked\nSF: Sykefravær\n"
        "AU: Arbeidsavklaringspenger og uføretrygd\nFA: Familie\n"
        "OT: Oppfølging og tiltak\nPE: Pensjon\nSB: Saksbehandling\n"
        "GA: Flere statistikkområder"
    ) in str(error.value)


@pytest.mark.parametrize(
    "formula",
    [
        "~ region",
        "~ periode",
        "~periode*region",
        "~ periode + region",
        "~ periode / region",
        "~ periode : region",
        "~ (periode + region) / (region * periode)",
        "~ ((region))",
        "~\tperiode\n+ region ",
    ],
)
def test_aggregate_accepts_formula_with_dimensions_and_supported_operators(formula):
    config = configuration().model_dump(by_alias=True)
    config["aggregater"][0]["formula"] = formula

    assert Configuration.model_validate(config).aggregater[0].formula == formula


@pytest.mark.parametrize("formula", ["periode * region", "antall ~ region", " ~ region"])
def test_aggregate_formula_must_start_with_tilde(formula):
    config = configuration().model_dump(by_alias=True)
    config["aggregater"][0]["formula"] = formula

    with pytest.raises(ValidationError, match="formula må starte med ~"):
        Configuration.model_validate(config)


@pytest.mark.parametrize(
    "formula",
    [
        "~",
        "~ ()",
        "~ (region",
        "~ region)",
        "~ region +",
        "~ * region",
        "~ region periode",
        "~ region (periode)",
        "~ region ** periode",
        "~ region - periode",
        "~ region ^ periode",
        "~ region + 1",
        "~ region ~ periode",
        "~ ukjent",
        "~ antall",
        "~ region + antall",
        "~ tellepunkt",
        "~ verdi",
        "~ log(region)",
        "~ region; periode",
        "~ region$",
        "~ region#",
        "~ region @ + periode",
        "~ region + 1periode",
        "~ region + _periode",
        "~ region # kommentar",
        "~ `region`",
    ],
)
def test_aggregate_rejects_invalid_formula(formula):
    config = configuration().model_dump(by_alias=True)
    config["aggregater"][0]["formula"] = formula

    with pytest.raises(ValidationError, match="formula må inneholde et gyldig uttrykk"):
        Configuration.model_validate(config)


def test_aggregate_formula_uses_configured_column_names():
    config = configuration().model_dump(by_alias=True)
    aggregate = config["aggregater"][0]
    aggregate["periodisering"]["kolonne"] = "maaned"
    aggregate["variabler"] = [{"kolonne": "alder"}, {"kolonne": "kjonn"}]
    aggregate["formula"] = "~ maaned * (alder + kjonn)"

    Configuration.model_validate(config)

    aggregate["formula"] = "~ periode * region"
    with pytest.raises(ValidationError, match="formula må inneholde et gyldig uttrykk"):
        Configuration.model_validate(config)


@pytest.mark.parametrize("mode", ["overskriv_siste_periode", "overskriv_ikke", "overskriv_alt"])
def test_configuration_rejects_removed_incremental_mode(mode):
    config = configuration().model_dump(by_alias=True)
    config["aggregater"][0]["inkrementell-strategi"] = mode

    with pytest.raises(
        ValidationError,
        match="inkrementell-strategi må være én av legg_til_nye_perioder, overskriv_eksisterende_perioder",
    ):
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


@pytest.mark.parametrize("agg_id", [
    "00000", "00001", "09999", "0", "0000", "000000", "10000", "19999",
    "NAV00001", "NAV21000", "nav21000", "23000",
])
@pytest.mark.parametrize("allow_development_id", [False, True])
def test_aggregate_id_validation_only_bypasses_development_id(agg_id, allow_development_id):
    config = configuration().model_dump(by_alias=True)
    config["aggregater"][0]["id"] = agg_id
    context = {"ingen_id_validering": allow_development_id}

    if agg_id in {"00000", "00001", "09999"} and allow_development_id:
        assert Configuration.model_validate(config, context=context).aggregater[0].id == agg_id
    else:
        with pytest.raises(ValidationError, match="uten NAV-prefiks"):
            Configuration.model_validate(config, context=context)


@pytest.mark.parametrize("agg_id", ["20000", "22999"])
def test_aggregate_id_accepts_range_boundaries(agg_id):
    config = configuration().model_dump(by_alias=True)
    config["aggregater"][0]["id"] = agg_id

    assert Configuration.model_validate(config).aggregater[0].id == agg_id


@pytest.mark.parametrize("agg_id", ["00000", "00001", "09999", "21000"])
def test_configuration_rejects_duplicate_ids_including_test_series(agg_id):
    config = configuration().model_dump(by_alias=True)
    aggregate = config["aggregater"][0]
    aggregate["id"] = agg_id
    config["aggregater"].append({**aggregate, "tabell": "annen"})

    with pytest.raises(ValidationError, match="id må være unike"):
        Configuration.model_validate(config, context={"ingen_id_validering": True})


def test_configuration_accepts_distinct_test_and_registered_ids():
    config = configuration().model_dump(by_alias=True)
    aggregate = config["aggregater"][0]
    config["aggregater"] = [
        {**aggregate, "id": aggregat_id, "tabell": f"tabell_{index}"}
        for index, aggregat_id in enumerate(["00000", "00001", "09999", "21000"])
    ]

    result = Configuration.model_validate(config, context={"ingen_id_validering": True})
    assert [item.id for item in result.aggregater] == ["00000", "00001", "09999", "21000"]


def test_aggregate_metadata_is_required():
    for field in ("navn", "navn-en", "innhold", "innhold-en"):
        incomplete = configuration().model_dump(by_alias=True)
        incomplete["aggregater"][0].pop(field)
        with pytest.raises(ValidationError):
            Configuration.model_validate(incomplete)


def test_column_metadata_is_optional():
    config = configuration().model_dump(by_alias=True)
    aggregate = config["aggregater"][0]
    columns = [aggregate["periodisering"], *aggregate["tellepunkter"], *aggregate["variabler"]]
    for column in columns:
        for field in ("navn", "navn-en", "innhold", "innhold-en"):
            column.pop(field)

    Configuration.model_validate(config)


def test_aggregate_rejects_names_too_long_for_derived_tables():
    config = configuration().model_dump(by_alias=True)
    config["aggregater"][0]["tabell"] = "a" * 120

    with pytest.raises(ValidationError, match="Oracle-navn"):
        Configuration.model_validate(config)


def test_loads_aggregate_configurations_matching_mapper(tmp_path):
    """Leser en konfigurasjonsfil per aggregat fra mønsteret i hovedfilen."""
    aggregate_directory = tmp_path / "aggregater"
    aggregate_directory.mkdir()
    (tmp_path / "konfigurasjon.yml").write_text(
        """
version: 1
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
        (aggregate_directory / f"{table}.sql").write_text("select 1 from dual")
        (aggregate_directory / f"{table}.yml").write_text(
            f"""
version: 1
definisjon:
  navn: {table}
  navn-en: {table}
  innhold: {table} 
  innhold-en: {table}
  id: 21000
  referanse-tabell: OBT_TABELL
  periodisering:
    navn: Periode
    navn-en: Period
    kolonne: periode
    innhold: Periode
    innhold-en: Month
  inkrementell-strategi: legg_til_nye_perioder
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
    assert config.version == 1
    assert [aggregate.tabell for aggregate in config.aggregater] == ["tabell"]
    aggregate = config.aggregater[0]
    assert aggregate.referanse_tabell == "obt_tabell"
    assert aggregate.sporring_sql == "aggregater/tabell.sql"
    assert aggregate.periodisering.kolonne == "periode"
    assert aggregate.inkrementell_strategi.value == "legg_til_nye_perioder"
    assert isinstance(aggregate.tellepunkter[0], Tellepunkt)
    assert aggregate.tellepunkter[0].kolonne == "antall"


def test_aggregate_table_and_sql_are_inferred_from_filename(tmp_path):
    """Utleder tabell og SQL-sti fra aggregatfilen."""
    aggregate_directory = tmp_path / "aggregater"
    aggregate_directory.mkdir()
    (tmp_path / "konfigurasjon.yml").write_text(
        """
version: 1
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
    (aggregate_directory / "annet_navn.sql").write_text("select 1 from dual")
    (aggregate_directory / "annet_navn.yml").write_text(
        """
version: 1
definisjon:
  navn: dummy
  navn-en: dummy
  innhold: dummy 
  innhold-en: dummy
  id: "21000"
  referanse-tabell: OBT_TABELL
  periodisering:
    kolonne: periode
  inkrementell-strategi: legg_til_nye_perioder
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
version: 1
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

    with pytest.raises(ConfigError, match="har flere YAML-filer"):
        load_configuration(tmp_path / "konfigurasjon.yml")


@pytest.mark.parametrize("yaml_suffix", [".yml", ".yaml"])
@pytest.mark.parametrize("prefix", ["", "nested/"])
def test_loads_separate_directories_with_overlapping_patterns_and_preserves_order(
    tmp_path, monkeypatch, yaml_suffix, prefix
):
    yaml_directory = tmp_path / prefix / "yaml"
    sql_directory = tmp_path / prefix / "sql"
    yaml_directory.mkdir(parents=True)
    sql_directory.mkdir(parents=True)
    config = configuration()
    for table, agg_id in (("first", "21000"), ("second", "21001")):
        aggregate = config.aggregater[0].model_dump(mode="json", by_alias=True)
        aggregate.pop("tabell")
        aggregate.pop("sporring_sql")
        aggregate["id"] = agg_id
        (yaml_directory / f"{table.upper()}{yaml_suffix}").write_text(
            yaml.safe_dump({"version": 1, "definisjon": aggregate}),
            encoding="utf-8",
        )
        (sql_directory / f"{table.upper()}.SQL").write_text("select 1 from dual")
    config_directory = tmp_path
    config_path = config_directory / "konfigurasjon.yml"
    config_path.write_text(
        yaml.safe_dump({
            "version": 1,
            "connection": config.connection.model_dump(mode="json", by_alias=True)
            | {"password": "env://TEST_PASSWORD"},
            "mapper": [
                f"{prefix}yaml/FIRST{yaml_suffix}", f"{prefix}yaml/**",
                f"{prefix}sql/**", f"{prefix}yaml/*{yaml_suffix}", f"{prefix}sql/*.SQL",
            ],
            "aggregater": ["SECOND", "FIRST"],
        }),
        encoding="utf-8",
    )

    monkeypatch.chdir(yaml_directory)
    loaded, loaded_directory = load_configuration(config_path)

    assert loaded_directory == config_directory
    assert [aggregate.tabell for aggregate in loaded.aggregater] == ["second", "first"]
    assert [aggregate.sporring_sql for aggregate in loaded.aggregater] == [
        f"{prefix}sql/{table}.SQL" for table in ("SECOND", "FIRST")
    ]


@pytest.mark.parametrize("suffixes", [(".yml", ".yaml"), (".sql", ".SQL")])
def test_duplicate_files_report_both_paths_and_patterns(tmp_path, suffixes):
    aggregate_directory = tmp_path / "aggregater"
    aggregate_directory.mkdir()
    paths = [aggregate_directory / f"tabell{suffix}" for suffix in suffixes]
    for path in paths:
        path.write_text("")

    with pytest.raises(ConfigError) as error:
        _find_aggregate_configurations(["aggregater/*"], ["tabell"], tmp_path)

    message = str(error.value)
    assert "har flere" in message
    assert "søkemønstrene 'aggregater/*'" in message
    assert all(str(path) in message for path in paths)
    assert "Ugyldig mønster" not in message


@pytest.mark.parametrize(
    ("suffixes", "missing"),
    [([".yml"], "SQL"), ([".sql"], "YAML"), ([], "YAML og SQL")],
)
def test_missing_files_report_type_and_search_patterns(tmp_path, suffixes, missing):
    aggregate_directory = tmp_path / "aggregater"
    aggregate_directory.mkdir()
    for suffix in suffixes:
        (aggregate_directory / f"tabell{suffix}").write_text("")

    with pytest.raises(ConfigError) as error:
        _find_aggregate_configurations(
            ["aggregater/*.yml", "aggregater/*.sql"], ["tabell"], tmp_path
        )

    message = str(error.value)
    assert f"tabell (mangler {missing}-fil)" in message
    assert "'aggregater/*.yml', 'aggregater/*.sql'" in message
    assert str(tmp_path) in message


def test_yaml_only_pattern_does_not_discover_sibling_sql(tmp_path):
    aggregate_directory = tmp_path / "aggregater"
    aggregate_directory.mkdir()
    (aggregate_directory / "tabell.yml").write_text("")
    (aggregate_directory / "tabell.sql").write_text("")

    with pytest.raises(ConfigError, match="tabell \\(mangler SQL-fil\\)"):
        _find_aggregate_configurations(["aggregater/*.yml"], ["tabell"], tmp_path)


def test_invalid_search_pattern_reports_pattern(tmp_path):
    pattern = str(tmp_path / "aggregater" / "*")
    with pytest.raises(ConfigError) as error:
        _find_aggregate_configurations([pattern], ["tabell"], tmp_path)

    assert f"Ugyldig mønster for aggregatkonfigurasjoner: {pattern}" == str(error.value)


def test_aggregate_validation_error_reports_yaml_path(tmp_path):
    aggregate_directory = tmp_path / "aggregater"
    aggregate_directory.mkdir()
    aggregate_path = aggregate_directory / "tabell.yml"
    aggregate_path.write_text("version: 1\ndefinisjon: {}\n", encoding="utf-8")
    (aggregate_directory / "tabell.sql").write_text("")
    config_path = tmp_path / "konfigurasjon.yml"
    config_path.write_text(
        yaml.safe_dump({
            "version": 1,
            "connection": configuration().connection.model_dump(
                mode="json", by_alias=True
            ) | {"password": "env://TEST_PASSWORD"},
            "mapper": ["aggregater/*.yml", "aggregater/*.sql"],
            "aggregater": ["tabell"],
        }),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError) as error:
        load_configuration(config_path)

    assert f"Konfigurasjonen i {aggregate_path} er ugyldig:" in str(error.value)


@pytest.mark.parametrize("suffix", [".yml", ".sql"])
@pytest.mark.parametrize("location", ["beside", "outside"])
@pytest.mark.parametrize("symlink", [False, True])
def test_aggregate_files_must_resolve_to_subdirectories(
    tmp_path, suffix, location, symlink
):
    config_directory = tmp_path / "config"
    config_directory.mkdir()
    target_directory = config_directory if location == "beside" else tmp_path
    target = target_directory / f"tabell{suffix}"
    target.write_text("")
    if symlink:
        aggregate_directory = config_directory / "aggregater"
        aggregate_directory.mkdir()
        (aggregate_directory / target.name).symlink_to(target)
        pattern = "aggregater/*"
    else:
        pattern = "*" if location == "beside" else "../*"

    with pytest.raises(ConfigError) as error:
        _find_aggregate_configurations([pattern], ["tabell"], config_directory)

    message = str(error.value)
    assert "må ligge i en undermappe til hovedkonfigurasjonen" in message
    assert str(config_directory) in message
    assert str(target) in message
    assert "Ugyldig mønster" not in message


def test_resolve_env_reference(monkeypatch):
    """Slår opp miljøvariabelreferanser for hemmeligheter."""
    monkeypatch.setenv("TEST_PASSWORD", "secret")
    assert resolve_config("password", "env://TEST_PASSWORD", password=True) == "secret"


def test_resolve_terminal_reference(monkeypatch):
    """Leser terminalreferanser interaktivt når standard input er en terminal."""
    from statistikkprodukt.config import loader

    getpass = Mock(return_value="secret")
    monkeypatch.setattr(loader.sys, "stdin", Mock(isatty=Mock(return_value=True)))
    monkeypatch.setattr(loader.getpass, "getpass", getpass)

    assert resolve_config("password", "terminal://", password=True) == "secret"
    getpass.assert_called_once_with("Tast inn verdien for password: ")


def test_resolve_terminal_reference_requires_tty(monkeypatch):
    """Avviser terminalreferanser når standard input ikke er en terminal."""
    from statistikkprodukt.config import loader

    monkeypatch.setattr(loader.sys, "stdin", Mock(isatty=Mock(return_value=False)))

    with pytest.raises(ConfigError, match="standard input er en terminal"):
        resolve_config("password", "terminal://", password=True)


def test_aggregate_accepts_multiple_tellepunkter():
    config = configuration().model_dump(by_alias=True)
    config["aggregater"][0]["tellepunkter"] = [{"kolonne": "antall"}, {"kolonne": "kvinner"}]

    aggregate = Configuration.model_validate(config).aggregater[0]

    assert [t.kolonne for t in aggregate.tellepunkter] == ["antall", "kvinner"]


@pytest.mark.parametrize(
    "tellepunkter",
    [
        [{"kolonne": "antall"}, {"kolonne": "antall"}],
        [{"kolonne": "verdi"}],
        [{"kolonne": "tellepunkt"}],
    ],
)
def test_aggregate_rejects_duplicate_or_reserved_tellepunkter(tellepunkter):
    config = configuration().model_dump(by_alias=True)
    config["aggregater"][0]["tellepunkter"] = tellepunkter

    with pytest.raises(ValidationError):
        Configuration.model_validate(config)
