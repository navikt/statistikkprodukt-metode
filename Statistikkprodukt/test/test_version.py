from __future__ import annotations

import logging
from unittest.mock import MagicMock, Mock, call

import pytest
from statistikkprodukt import version
from statistikkprodukt.app import installer_r_pakker
from statistikkprodukt.app.models import Kjorelogg
from test.test_config import configuration


def _python_info(
    *,
    latest: str = "1.5.0",
    minimum: str | None = "1.4.0",
    message: str | None = "Kritisk feil er rettet.",
) -> version.PythonVersionInfo:
    return version.PythonVersionInfo(latest, minimum, message)


def test_python_version_check_skips_when_disabled(monkeypatch):
    monkeypatch.setenv("STATISTIKKPRODUKT_CHECK_VERSION", "0")
    read = Mock()
    monkeypatch.setattr(version, "_read_python_version_info", read)

    version.require_compatible_python_version_interactive("last")

    read.assert_not_called()


def test_python_version_check_skips_editable_installation(monkeypatch):
    monkeypatch.setenv("STATISTIKKPRODUKT_CHECK_VERSION", "1")
    monkeypatch.setattr(version, "is_editable_installation", lambda: True)
    read = Mock()
    monkeypatch.setattr(version, "_read_python_version_info", read)

    version.require_compatible_python_version_interactive("last")

    read.assert_not_called()


@pytest.mark.parametrize("command", ["installer-r-pakker", "testenv"])
def test_python_version_check_skips_commands(monkeypatch, command):
    monkeypatch.setenv("STATISTIKKPRODUKT_CHECK_VERSION", "1")
    monkeypatch.setattr(version, "is_editable_installation", lambda: False)
    read = Mock()
    monkeypatch.setattr(version, "_read_python_version_info", read)

    version.require_compatible_python_version_interactive(command)

    read.assert_not_called()


def test_python_version_below_minimum_blocks_noninteractive(monkeypatch, caplog):
    monkeypatch.setenv("STATISTIKKPRODUKT_CHECK_VERSION", "1")
    monkeypatch.setattr(version, "is_editable_installation", lambda: False)
    monkeypatch.setattr(version, "_read_python_version_info", lambda: _python_info())
    monkeypatch.setattr(version, "get_python_version", lambda: "1.3.9")
    monkeypatch.setattr(version, "_is_interactive_terminal", lambda: False)

    with pytest.raises(SystemExit) as exc:
        version.require_compatible_python_version_interactive("last")

    assert exc.value.code == 1
    assert "Versjon 1.4.0 er påkrevd" in caplog.text
    assert "Kritisk feil er rettet" in caplog.text
    assert version.UPDATE_COMMAND in caplog.text


def test_python_version_below_latest_warns_without_blocking(monkeypatch, caplog):
    monkeypatch.setattr(version, "_read_python_version_info", lambda: _python_info())
    monkeypatch.setattr(version, "get_python_version", lambda: "1.4.0")
    monkeypatch.setattr(version, "is_editable_installation", lambda: False)
    monkeypatch.setattr(version, "should_skip_version_check", lambda: False)

    version.require_compatible_python_version_interactive("last")

    assert "En nyere versjon" in caplog.text
    assert version.UPDATE_COMMAND in caplog.text


@pytest.mark.parametrize("installed", ["1.5.0", "1.6.0"])
def test_python_current_or_newer_version_does_nothing(monkeypatch, caplog, installed):
    monkeypatch.setattr(version, "_read_python_version_info", lambda: _python_info())
    monkeypatch.setattr(version, "get_python_version", lambda: installed)
    monkeypatch.setattr(version, "is_editable_installation", lambda: False)
    monkeypatch.setattr(version, "should_skip_version_check", lambda: False)

    version.require_compatible_python_version_interactive("last")

    assert not caplog.records


def test_python_missing_minimum_still_warns(monkeypatch, caplog):
    monkeypatch.setattr(version, "_read_python_version_info", lambda: _python_info(minimum=None, message=None))
    monkeypatch.setattr(version, "get_python_version", lambda: "1.4.0")
    monkeypatch.setattr(version, "is_editable_installation", lambda: False)
    monkeypatch.setattr(version, "should_skip_version_check", lambda: False)

    version.require_compatible_python_version_interactive("last")

    assert "En nyere versjon" in caplog.text


def test_invalid_remote_structure_warns_and_continues(monkeypatch, caplog):
    monkeypatch.setattr(version, "urlopen", MagicMock())
    response = version.urlopen.return_value.__enter__.return_value
    response.read.return_value = b'tool = []\n[project]\nversion = "1.0.0"\n'
    monkeypatch.setattr(version, "is_editable_installation", lambda: False)
    monkeypatch.setattr(version, "should_skip_version_check", lambda: False)

    version.require_compatible_python_version_interactive("last")

    assert "Kunne ikke kontrollere" in caplog.text


@pytest.mark.parametrize("error", [TimeoutError(), OSError("nettverk")])
def test_python_version_network_failure_warns_and_continues(
    monkeypatch, caplog, error
):
    monkeypatch.setattr(version, "_read_python_version_info", Mock(side_effect=error))
    monkeypatch.setattr(version, "is_editable_installation", lambda: False)
    monkeypatch.setattr(version, "should_skip_version_check", lambda: False)

    version.require_compatible_python_version_interactive("last")

    assert "Kunne ikke kontrollere" in caplog.text
    assert "raw.githubusercontent.com/navikt/statistikkprodukt-metode/*" in caplog.text


def test_python_interactive_update_runs_python_and_r_updates(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="statistikkprodukt.version")
    monkeypatch.setattr(version, "_read_python_version_info", lambda: _python_info())
    monkeypatch.setattr(version, "get_python_version", lambda: "1.3.9")
    monkeypatch.setattr(version, "is_editable_installation", lambda: False)
    monkeypatch.setattr(version, "should_skip_version_check", lambda: False)
    monkeypatch.setattr(version, "_is_interactive_terminal", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "j")
    run = Mock()
    monkeypatch.setattr(version.subprocess, "run", run)

    with pytest.raises(SystemExit) as exc:
        version.require_compatible_python_version_interactive("last")

    assert exc.value.code == 0
    assert run.call_args_list == [
        call(["uv", "tool", "upgrade", "statistikkprodukt"], check=True),
        call(["statistikkprodukt", "installer-r-pakker"], check=True),
    ]
    assert "Kjør kommandoen på nytt" in caplog.text


def test_update_packages_runs_python_then_r_installer():
    run = Mock()

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(version.subprocess, "run", run)
        version.update_packages()

    assert run.call_args_list == [
        call(["uv", "tool", "upgrade", "statistikkprodukt"], check=True),
        call(["statistikkprodukt", "installer-r-pakker"], check=True),
    ]


def test_update_packages_can_skip_r_installer():
    run = Mock()

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(version.subprocess, "run", run)
        version.update_packages(update_r_packages=False)

    run.assert_called_once_with(
        ["uv", "tool", "upgrade", "statistikkprodukt"], check=True
    )


def test_failed_python_update_does_not_run_r_installer(monkeypatch):
    monkeypatch.setattr(version, "_read_python_version_info", lambda: _python_info())
    monkeypatch.setattr(version, "get_python_version", lambda: "1.3.9")
    monkeypatch.setattr(version, "is_editable_installation", lambda: False)
    monkeypatch.setattr(version, "should_skip_version_check", lambda: False)
    monkeypatch.setattr(version, "_is_interactive_terminal", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "j")
    run = Mock(side_effect=version.subprocess.CalledProcessError(1, "uv"))
    monkeypatch.setattr(version.subprocess, "run", run)

    with pytest.raises(SystemExit) as exc:
        version.require_compatible_python_version_interactive("last")

    assert exc.value.code == 1
    run.assert_called_once_with(["uv", "tool", "upgrade", "statistikkprodukt"], check=True)


def test_python_interactive_decline_does_not_run_uv(monkeypatch):
    monkeypatch.setattr(version, "_read_python_version_info", lambda: _python_info())
    monkeypatch.setattr(version, "get_python_version", lambda: "1.3.9")
    monkeypatch.setattr(version, "is_editable_installation", lambda: False)
    monkeypatch.setattr(version, "should_skip_version_check", lambda: False)
    monkeypatch.setattr(version, "_is_interactive_terminal", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    run = Mock()
    monkeypatch.setattr(version.subprocess, "run", run)

    with pytest.raises(SystemExit) as exc:
        version.require_compatible_python_version_interactive("last")

    assert exc.value.code == 1
    run.assert_not_called()


@pytest.mark.parametrize(
    ("package_version", "valid"),
    [
        ("0.1.1", True),
        ("0.1.9", True),
        ("0.1.0", False),
        ("0.2.0", False),
    ],
)
def test_r_package_requirement(monkeypatch, package_version, valid):
    info = version.RPackageInfo(package_version, "abc1234")
    monkeypatch.setattr(version, "get_r_package_info", lambda: info)
    monkeypatch.setattr(version, "is_editable_installation", lambda: False)

    if valid:
        assert version.require_compatible_r_package_interactive() == info
    else:
        with pytest.raises(RuntimeError) as exc:
            version.require_compatible_r_package_interactive(offer_installation=False)
        assert str(version.R_PACKAGE_REQUIREMENT) in str(exc.value)


def test_missing_r_package_reports_requirement(monkeypatch):
    monkeypatch.setattr(version, "get_r_package_info", lambda: None)
    monkeypatch.setattr(version, "is_editable_installation", lambda: False)

    with pytest.raises(RuntimeError) as exc:
        version.require_compatible_r_package_interactive(offer_installation=False)

    assert "ikke installert" in str(exc.value)
    assert "installer-r-pakker" in str(exc.value)


def test_invalid_r_package_only_warns_for_editable_installation(monkeypatch, caplog):
    info = version.RPackageInfo("0.1.0", "lokal")
    monkeypatch.setattr(version, "get_r_package_info", lambda: info)
    monkeypatch.setattr(version, "is_editable_installation", lambda: True)

    assert version.require_compatible_r_package_interactive() == info
    assert "Fortsetter fordi installasjonen er editable" in caplog.text


def test_interactive_r_check_installs_and_checks_again(monkeypatch):
    old = version.RPackageInfo("0.1.0", "lokal")
    new = version.RPackageInfo("0.1.1", "abc1234")
    monkeypatch.setattr(version, "get_r_package_info", Mock(side_effect=[old, new]))
    monkeypatch.setattr(version, "is_editable_installation", lambda: False)
    monkeypatch.setattr(version, "_is_interactive_terminal", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "j")
    install = Mock()
    monkeypatch.setattr(installer_r_pakker, "installer_r_pakker", install)

    assert version.require_compatible_r_package_interactive() == new
    install.assert_called_once_with(False)


def test_run_log_contains_python_and_r_versions(monkeypatch):
    config = configuration()
    info = version.RPackageInfo("0.1.1", "abc1234")
    monkeypatch.setattr("statistikkprodukt.app.models.get_python_version", lambda: "1.5.0")
    monkeypatch.setattr("statistikkprodukt.app.models.get_r_package_info", lambda: info)

    logg = Kjorelogg.from_configuration(config, config.aggregater, "db", "db")

    assert logg.som_rader()[0]["python_pakke_versjon"] == "1.5.0"
    assert logg.som_rader()[0]["r_pakke_versjon"] == "0.1.1 (abc1234)"
