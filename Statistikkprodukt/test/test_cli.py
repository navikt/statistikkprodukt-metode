import sys
from unittest.mock import Mock

import pytest
import yaml
from statistikkprodukt import app, cli
from test.test_config import configuration

AGGREGATE_NAME = "ost_am_befolkning_mnd_tabell"


def _configure(tmp_path, monkeypatch, agg_id):
    config = configuration().model_dump(mode="json", by_alias=True)
    config["connection"]["password"] = "env://TEST_PASSWORD"
    aggregate = config["aggregater"][0]
    aggregate["id"] = agg_id
    aggregate.pop("tabell")
    aggregate.pop("sporring_sql")
    aggregate_directory = tmp_path / "aggregater"
    aggregate_directory.mkdir()
    (aggregate_directory / f"{AGGREGATE_NAME}.yml").write_text(
        yaml.safe_dump({"version": 1, "definisjon": aggregate}), encoding="utf-8"
    )
    (aggregate_directory / f"{AGGREGATE_NAME}.sql").write_text("select 1 from dual")
    (tmp_path / "konfigurasjon.yml").write_text(
        yaml.safe_dump({
            "version": config["version"],
            "connection": config["connection"],
            "filsok": ["aggregater/*.yml", "aggregater/*.sql"],
            "aggregater": [AGGREGATE_NAME],
        }),
        encoding="utf-8",
    )
    register = tmp_path / "statistikkprodukt_register.md"
    register.write_text(
        "| Løpenr | Team | Statistikkprodukt |\n"
        "|--------+------+-------------------|\n"
        f"| 21000 | Team A | {AGGREGATE_NAME} |\n", encoding="utf-8"
    )
    monkeypatch.setenv("STATISTIKKPRODUKT_REGISTER_FIL", str(register))
    return tmp_path / "konfigurasjon.yml"


@pytest.mark.parametrize("command", [["last"], ["skjema", "opprett"]])
@pytest.mark.parametrize("allow_development_id", [False, True])
@pytest.mark.parametrize("agg_id", ["00000", "00001", "09999"])
def test_cli_gates_development_id(tmp_path, monkeypatch, command, allow_development_id, agg_id):
    path = _configure(tmp_path, monkeypatch, agg_id)
    execute = Mock()
    monkeypatch.setattr(app.last, "last", execute)
    monkeypatch.setattr(app.skjema, "create_tables", execute)
    flags = ["--ingen-id-validering"] if allow_development_id else []
    monkeypatch.setattr(sys, "argv", ["statistikkprodukt", *command, "--konfig-fil", str(path), *flags])

    if allow_development_id:
        cli.main()
        assert execute.call_args.args[0].aggregater[0].id == agg_id
    else:
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 2
        execute.assert_not_called()


@pytest.mark.parametrize("agg_id", ["NAV21000", "21001"])
def test_cli_flag_does_not_skip_other_id_checks(tmp_path, monkeypatch, agg_id):
    path = _configure(tmp_path, monkeypatch, agg_id)
    execute = Mock()
    monkeypatch.setattr(app.last, "last", execute)
    monkeypatch.setattr(
        sys, "argv",
        ["statistikkprodukt", "last", "--konfig-fil", str(path), "--ingen-id-validering"],
    )

    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    execute.assert_not_called()


@pytest.mark.parametrize("command", [["last"], ["skjema", "opprett"]])
def test_cli_accepts_id_registered_in_pipe_table(tmp_path, monkeypatch, command):
    path = _configure(tmp_path, monkeypatch, "21000")
    execute = Mock()
    monkeypatch.setattr(app.last, "last", execute)
    monkeypatch.setattr(app.skjema, "create_tables", execute)
    monkeypatch.setattr(sys, "argv", ["statistikkprodukt", *command, "--konfig-fil", str(path)])

    cli.main()

    execute.assert_called_once()


def test_cli_accepts_repeated_period_filters(tmp_path, monkeypatch):
    path = _configure(tmp_path, monkeypatch, "21000")
    execute = Mock()
    monkeypatch.setattr(app.last, "last", execute)
    monkeypatch.setattr(
        sys, "argv",
        [
            "statistikkprodukt", "last", "--konfig-fil", str(path),
            "-p", "2025", "--period", "2025M02", "-p", "2025K4",
        ],
    )

    cli.main()

    assert execute.call_args.kwargs["selected_periods"] == ["2025", "2025M02", "2025K4"]


@pytest.mark.parametrize("period", ["202501", "2025M13", "2025K01", "2025K5", "ABC"])
def test_cli_rejects_invalid_period_before_loading(monkeypatch, capsys, period):
    execute = Mock()
    monkeypatch.setattr(app.last, "last", execute)
    monkeypatch.setattr(sys, "argv", ["statistikkprodukt", "last", "-p", period])

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 2
    assert "Ugyldig periode" in capsys.readouterr().err
    execute.assert_not_called()


def test_cli_rejects_full_relast_with_period(tmp_path, monkeypatch):
    path = _configure(tmp_path, monkeypatch, "21000")
    execute = Mock()
    monkeypatch.setattr(app.last, "last", execute)
    monkeypatch.setattr(
        sys, "argv",
        [
            "statistikkprodukt", "last", "--konfig-fil", str(path),
            "--full-relast", "--period", "2025M01",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 2
    execute.assert_not_called()


@pytest.mark.parametrize("second_id", ["00000", "00001"])
def test_cli_requires_unique_test_ids_with_flag(tmp_path, monkeypatch, caplog, second_id):
    path = _configure(tmp_path, monkeypatch, "00000")
    second_name = "ost_am_befolkning_mnd_annen"
    second = yaml.safe_load(
        (tmp_path / "aggregater" / f"{AGGREGATE_NAME}.yml").read_text(encoding="utf-8")
    )
    second["definisjon"]["id"] = second_id
    (tmp_path / "aggregater" / f"{second_name}.yml").write_text(
        yaml.safe_dump(second), encoding="utf-8"
    )
    (tmp_path / "aggregater" / f"{second_name}.sql").write_text("select 1 from dual")
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["aggregater"].append(second_name)
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    execute = Mock()
    monkeypatch.setattr(app.last, "last", execute)
    monkeypatch.setattr(
        sys, "argv",
        ["statistikkprodukt", "last", "--konfig-fil", str(path), "--ingen-id-validering"],
    )

    if second_id == "00000":
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 2
        assert "id må være unike" in caplog.text
        execute.assert_not_called()
    else:
        cli.main()
        execute.assert_called_once()


@pytest.mark.parametrize("use_environment", [False, True])
def test_cli_config_check_only_validates(tmp_path, monkeypatch, capsys, use_environment):
    from statistikkprodukt.config import loader
    import oracledb

    path = _configure(tmp_path, monkeypatch, "21000")
    monkeypatch.delenv("TEST_PASSWORD", raising=False)
    operations = [
        (loader, "resolve_config"),
        (app.last, "resolve_config"),
        (oracledb, "connect"),
        (app.last, "last"),
        (app.skjema, "create_tables"),
        (app.installer_r_pakker, "installer_r_pakker"),
    ]
    mocks = []
    for module, name in operations:
        operation = Mock(side_effect=AssertionError("Kun validering er tillatt."))
        monkeypatch.setattr(module, name, operation)
        mocks.append(operation)
    if use_environment:
        monkeypatch.setenv("STATISTIKKPRODUKT_AGGREGAT_CONFIG", str(path))
        flags = []
    else:
        flags = ["--konfig-fil", str(path)]
    monkeypatch.setattr(sys, "argv", ["statistikkprodukt", "konfig", "sjekk", *flags])

    cli.main()

    assert "Konfigurasjonen er gyldig. Antall aggregater: 1." in capsys.readouterr().out
    for operation in mocks:
        operation.assert_not_called()


def test_cli_update_runs_python_and_r_packages(monkeypatch):
    update = Mock()
    version_check = Mock()
    monkeypatch.setattr(cli, "update_packages", update)
    monkeypatch.setattr(cli, "require_compatible_python_version_interactive", version_check)
    monkeypatch.setattr(sys, "argv", ["statistikkprodukt", "oppdater"])

    cli.main()

    version_check.assert_called_once_with("oppdater")
    update.assert_called_once_with(update_r_packages=True)


def test_cli_update_can_skip_r_packages(monkeypatch):
    update = Mock()
    monkeypatch.setattr(cli, "update_packages", update)
    monkeypatch.setattr(
        sys, "argv",
        ["statistikkprodukt", "oppdater", "--ikke-oppdater-r-pakker"],
    )

    cli.main()

    update.assert_called_once_with(update_r_packages=False)


@pytest.mark.parametrize(
    ("formula", "warn_start", "missing"),
    [
        ("~ periode * (region + regionkode + periode_aar)", False, []),
        ("~periode/region:regionkode+periode_aar", False, []),
        ("~\tperiode\n+ region + regionkode + periode_aar", False, []),
        ("~ region * periode * regionkode * periode_aar", True, []),
        ("~ (periode + region + regionkode + periode_aar)", True, []),
        ("~ periode", False, ["region", "regionkode", "periode_aar"]),
        ("~ region", True, ["periode", "regionkode", "periode_aar"]),
        ("~ periode_aar + regionkode", True, ["periode", "region"]),
    ],
)
def test_cli_config_check_warns_about_formula(
    tmp_path, monkeypatch, caplog, capsys, formula, warn_start, missing
):
    path = _configure(tmp_path, monkeypatch, "21000")
    aggregate_path = tmp_path / "aggregater" / f"{AGGREGATE_NAME}.yml"
    aggregate = yaml.safe_load(aggregate_path.read_text(encoding="utf-8"))
    aggregate["definisjon"]["formula"] = formula
    aggregate["definisjon"]["variabler"] = [
        {"kolonne": column} for column in ("region", "regionkode", "periode_aar")
    ]
    aggregate_path.write_text(yaml.safe_dump(aggregate), encoding="utf-8")
    monkeypatch.setattr(
        sys, "argv",
        ["statistikkprodukt", "konfig", "sjekk", "--konfig-fil", str(path)],
    )

    cli.main()

    warnings = [record.getMessage() for record in caplog.records if record.levelname == "WARNING"]
    expected = []
    if warn_start:
        expected.append(f"Aggregat {AGGREGATE_NAME}: formula starter ikke med ~ periode.")
    if missing:
        expected.append(
            f"Aggregat {AGGREGATE_NAME}: formula bruker ikke følgende kolonner fra variabler "
            f"og periodisering: {', '.join(missing)}."
        )
    assert warnings == expected
    assert "Konfigurasjonen er gyldig" in capsys.readouterr().out


@pytest.mark.parametrize("command", [["last"], ["skjema", "opprett"]])
def test_cli_formula_warnings_are_only_emitted_by_config_check(
    tmp_path, monkeypatch, caplog, command
):
    path = _configure(tmp_path, monkeypatch, "21000")
    aggregate_path = tmp_path / "aggregater" / f"{AGGREGATE_NAME}.yml"
    aggregate = yaml.safe_load(aggregate_path.read_text(encoding="utf-8"))
    aggregate["definisjon"]["formula"] = "~ region"
    aggregate_path.write_text(yaml.safe_dump(aggregate), encoding="utf-8")
    monkeypatch.setattr(app.last, "last", Mock())
    monkeypatch.setattr(app.skjema, "create_tables", Mock())
    monkeypatch.setattr(sys, "argv", ["statistikkprodukt", *command, "--konfig-fil", str(path)])

    cli.main()

    assert "formula" not in caplog.text


@pytest.mark.parametrize(
    "error",
    ["missing_config", "invalid_yaml", "unknown_field", "missing_aggregate", "unregistered_id"],
)
def test_cli_config_check_reports_configuration_errors(tmp_path, monkeypatch, caplog, error):
    path = _configure(tmp_path, monkeypatch, "21001" if error == "unregistered_id" else "21000")
    if error == "missing_config":
        monkeypatch.delenv("STATISTIKKPRODUKT_AGGREGAT_CONFIG", raising=False)
        flags = []
    else:
        flags = ["--konfig-fil", str(path)]
        if error == "invalid_yaml":
            path.write_text("connection: [", encoding="utf-8")
        elif error == "unknown_field":
            aggregate_path = tmp_path / "aggregater" / f"{AGGREGATE_NAME}.yml"
            aggregate = yaml.safe_load(aggregate_path.read_text(encoding="utf-8"))
            aggregate["definisjon"]["ukjent"] = True
            aggregate_path.write_text(yaml.safe_dump(aggregate), encoding="utf-8")
        elif error == "missing_aggregate":
            (tmp_path / "aggregater" / f"{AGGREGATE_NAME}.yml").unlink()
    monkeypatch.setattr(sys, "argv", ["statistikkprodukt", "konfig", "sjekk", *flags])

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 2
    assert "Konfigurasjonsfeil:" in caplog.text


@pytest.mark.parametrize("allow_development_id", [False, True])
def test_cli_config_check_gates_test_ids(tmp_path, monkeypatch, capsys, allow_development_id):
    path = _configure(tmp_path, monkeypatch, "00001")
    flags = ["--ingen-id-validering"] if allow_development_id else []
    monkeypatch.setattr(
        sys, "argv",
        ["statistikkprodukt", "konfig", "sjekk", "--konfig-fil", str(path), *flags],
    )

    if allow_development_id:
        cli.main()
        assert "Konfigurasjonen er gyldig" in capsys.readouterr().out
    else:
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 2
