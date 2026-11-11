import pytest
from statistikkprodukt.config import ConfigError, check_lopenummer
from statistikkprodukt.config.lopenummer import read_lopenummer
from test.test_config import configuration


def _write(tmp_path, text):
    path = tmp_path / "Lopenr.txt"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.parametrize("delimiter", ["\t", ";", ",", "|"])
def test_read_lopenummer_detects_delimiter_and_strips_nav_prefix(tmp_path, delimiter):
    """Skilletegnet gjenkjennes, og NAV-prefiks og store bokstaver normaliseres."""
    path = _write(
        tmp_path,
        delimiter.join(["Løpenummer", "Team", "Tabellnavn"]) + "\n"
        + delimiter.join(["NAV21000", "Team A", "OST_BEFOLKNING_ALDER"]) + "\n"
        + delimiter.join(["21001", "Team B", "ost_befolkning_kjonn"]) + "\n",
    )

    assert read_lopenummer(path) == {
        "21000": "ost_befolkning_alder",
        "21001": "ost_befolkning_kjonn",
    }


def test_check_lopenummer_accepts_matching_id_and_table(tmp_path):
    aggregate = configuration().aggregater[0]
    path = _write(
        tmp_path,
        f"Løpenummer\tTeam\tTabellnavn\n{aggregate.id}\tTeam A\t{aggregate.tabell}\n",
    )

    check_lopenummer([aggregate], path)


def test_check_lopenummer_rejects_unknown_id(tmp_path):
    aggregate = configuration().aggregater[0]
    path = _write(tmp_path, "Løpenummer\tTeam\tTabellnavn\n29999\tTeam A\tannen\n")

    with pytest.raises(ConfigError, match=f"id {aggregate.id} finnes ikke"):
        check_lopenummer([aggregate], path)


def test_check_lopenummer_rejects_different_table_name(tmp_path):
    aggregate = configuration().aggregater[0]
    path = _write(
        tmp_path, f"Løpenummer\tTeam\tTabellnavn\n{aggregate.id}\tTeam A\tannen_tabell\n"
    )

    with pytest.raises(ConfigError, match="registrert på tabellen 'annen_tabell'"):
        check_lopenummer([aggregate], path)


def test_read_lopenummer_rejects_duplicate_id(tmp_path):
    path = _write(
        tmp_path, "Løpenummer\tTeam\tTabellnavn\n21000\tA\tx\nNAV21000\tB\ty\n"
    )

    with pytest.raises(ConfigError, match="flere ganger"):
        read_lopenummer(path)


def test_read_lopenummer_requires_columns(tmp_path):
    path = _write(tmp_path, "Nummer\tTabell\n21000\tx\n")

    with pytest.raises(ConfigError, match="må ha overskriftene"):
        read_lopenummer(path)


def test_missing_lopenummer_file_names_environment_variable(tmp_path):
    with pytest.raises(ConfigError, match="STATISTIKKPRODUKT_LOPENUMMER"):
        read_lopenummer(tmp_path / "finnes_ikke.txt")
