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
