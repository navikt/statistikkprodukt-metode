import json
from datetime import datetime
from unittest.mock import Mock

import pytest
import pytz
from statistikkprodukt import app_models
from statistikkprodukt.app_models import Kjorelogg, LasteResultat
from test.test_config import configuration


@pytest.mark.parametrize(("month", "expected_hour"), [(1, 13), (7, 14)])
def test_timestamps_are_naive_oslo_time_independent_of_host(monkeypatch, month, expected_hour):
    fixed = datetime(2026, month, 15, 12, tzinfo=pytz.UTC)
    clock = Mock()
    clock.now.side_effect = lambda zone: fixed.astimezone(zone)
    monkeypatch.setattr(app_models, "datetime", clock)

    logg = Kjorelogg({}, "db", "db", [])
    logg.fullfor_suksess()
    assert logg.starttid == datetime(2026, month, 15, expected_hour)
    assert logg.sluttid == logg.starttid
    assert logg.starttid.tzinfo is None


def test_log_preserves_json_and_long_error_and_handles_unfinished_aggregates():
    config = configuration()
    first = config.aggregater[0]
    second = first.model_copy(update={"tabell": "annen"})
    long_text = "Æøå" * 5000
    logg = Kjorelogg({"beskrivelse": long_text}, "db", "db", [first, second])
    logg.resultater.append(LasteResultat(first.id, first.tabell, ["2025,01", "2025-02"], 8, 1, 2))
    logg.fullfor_feil(ValueError(long_text))

    first_row, second_row = logg.som_rader()
    assert json.loads(first_row["konfigurasjon"]) == {"beskrivelse": long_text}
    assert first_row["feilmelding"] == long_text
    assert first_row["status"] == "FEILET"
    assert json.loads(first_row["perioder"]) == ["2025,01", "2025-02"]
    assert first_row["antall_rader"] == 8
    assert second_row["antall_rader"] is None
    assert second_row["perioder"] is None


def test_log_matches_development_results_by_table():
    aggregate = configuration().aggregater[0].model_copy(update={"id": "00000"})
    other = aggregate.model_copy(update={"tabell": "annen"})
    logg = Kjorelogg({}, "db", "db", [aggregate, other])
    logg.resultater = [
        LasteResultat("00000", aggregate.tabell, ["202501"], 10, 0, 0),
        LasteResultat("00000", other.tabell, ["202502"], 20, 1, 2),
    ]
    logg.fullfor_suksess()

    assert [row["antall_rader"] for row in logg.som_rader()] == [10, 20]


def test_log_records_commit_hash_from_environment_when_checkout_unavailable(monkeypatch):
    commit = "abc12345" * 5
    monkeypatch.setenv("STATISTIKKPRODUKT_COMMIT_SHA", commit)
    monkeypatch.setattr(
        app_models.subprocess, "run", Mock(side_effect=FileNotFoundError)
    )

    assert Kjorelogg({}, "db", "db", []).commit_hash == commit


def test_log_records_commit_hash_from_local_checkout(monkeypatch):
    commit = "fedcba98" * 5
    monkeypatch.setenv("STATISTIKKPRODUKT_COMMIT_SHA", "abc12345" * 5)
    monkeypatch.setattr(
        app_models.subprocess,
        "run",
        Mock(return_value=Mock(stdout=f"{commit}\n")),
    )

    assert app_models._program_commit() == commit


def test_log_records_commit_hash_from_vcs_install_metadata(monkeypatch):
    commit = "1234abcd" * 5
    monkeypatch.delenv("STATISTIKKPRODUKT_COMMIT_SHA", raising=False)
    monkeypatch.setattr(
        app_models.subprocess, "run", Mock(side_effect=FileNotFoundError)
    )
    package = Mock()
    package.read_text.return_value = json.dumps({"vcs_info": {"commit_id": commit}})
    monkeypatch.setattr(app_models, "distribution", Mock(return_value=package))

    assert Kjorelogg({}, "db", "db", []).commit_hash == commit
