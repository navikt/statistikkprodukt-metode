import sys
from unittest.mock import Mock

import pytest
import yaml
from statistikkprodukt import app, cli
from test.test_config import configuration


def _configure(tmp_path, monkeypatch, agg_id):
    config = configuration().model_dump(mode="json", by_alias=True)
    config["connection"]["password"] = "env://TEST_PASSWORD"
    aggregate = config["aggregater"][0]
    aggregate["id"] = agg_id
    aggregate.pop("tabell")
    aggregate.pop("sporring_sql")
    (tmp_path / "tabell.yml").write_text(
        yaml.safe_dump({"definisjon": aggregate}), encoding="utf-8"
    )
    (tmp_path / "konfigurasjon.yml").write_text(
        yaml.safe_dump({
            "connection": config["connection"],
            "mapper": ["*.yml"],
            "aggregater": ["tabell"],
        }),
        encoding="utf-8",
    )
    register = tmp_path / "statistikkprodukt_register.txt"
    register.write_text(
        "| Løpenr | Team | Statistikkprodukt |\n"
        "|--------+------+-------------------|\n"
        "| 21000 | Team A | tabell |\n", encoding="utf-8"
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
    monkeypatch.setattr(sys, "argv", ["statistikkprodukt", *command, "--config", str(path), *flags])

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
        ["statistikkprodukt", "last", "--config", str(path), "--ingen-id-validering"],
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
    monkeypatch.setattr(sys, "argv", ["statistikkprodukt", *command, "--config", str(path)])

    cli.main()

    execute.assert_called_once()


def test_cli_accepts_repeated_period_filters(tmp_path, monkeypatch):
    path = _configure(tmp_path, monkeypatch, "21000")
    execute = Mock()
    monkeypatch.setattr(app.last, "last", execute)
    monkeypatch.setattr(
        sys, "argv",
        [
            "statistikkprodukt", "last", "--config", str(path),
            "-p", "202501", "--period", "202502",
        ],
    )

    cli.main()

    assert execute.call_args.kwargs["selected_periods"] == ["202501", "202502"]


def test_cli_rejects_full_relast_with_period(tmp_path, monkeypatch):
    path = _configure(tmp_path, monkeypatch, "21000")
    execute = Mock()
    monkeypatch.setattr(app.last, "last", execute)
    monkeypatch.setattr(
        sys, "argv",
        [
            "statistikkprodukt", "last", "--config", str(path),
            "--full-relast", "--period", "202501",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 2
    execute.assert_not_called()


@pytest.mark.parametrize("second_id", ["00000", "00001"])
def test_cli_requires_unique_test_ids_with_flag(tmp_path, monkeypatch, caplog, second_id):
    path = _configure(tmp_path, monkeypatch, "00000")
    second = yaml.safe_load((tmp_path / "tabell.yml").read_text(encoding="utf-8"))
    second["definisjon"]["id"] = second_id
    (tmp_path / "annen.yml").write_text(yaml.safe_dump(second), encoding="utf-8")
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["aggregater"].append("annen")
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    execute = Mock()
    monkeypatch.setattr(app.last, "last", execute)
    monkeypatch.setattr(
        sys, "argv",
        ["statistikkprodukt", "last", "--config", str(path), "--ingen-id-validering"],
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
        flags = ["--config", str(path)]
    monkeypatch.setattr(sys, "argv", ["statistikkprodukt", "konfig", "sjekk", *flags])

    cli.main()

    assert "Konfigurasjonen er gyldig. Antall aggregater: 1." in capsys.readouterr().out
    for operation in mocks:
        operation.assert_not_called()


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
        flags = ["--config", str(path)]
        if error == "invalid_yaml":
            path.write_text("connection: [", encoding="utf-8")
        elif error == "unknown_field":
            aggregate_path = tmp_path / "tabell.yml"
            aggregate = yaml.safe_load(aggregate_path.read_text(encoding="utf-8"))
            aggregate["definisjon"]["ukjent"] = True
            aggregate_path.write_text(yaml.safe_dump(aggregate), encoding="utf-8")
        elif error == "missing_aggregate":
            (tmp_path / "tabell.yml").unlink()
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
        ["statistikkprodukt", "konfig", "sjekk", "--config", str(path), *flags],
    )

    if allow_development_id:
        cli.main()
        assert "Konfigurasjonen er gyldig" in capsys.readouterr().out
    else:
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 2
