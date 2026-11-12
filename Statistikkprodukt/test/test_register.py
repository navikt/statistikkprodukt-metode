import pytest
from statistikkprodukt.config import ConfigError, StatistikkproduktRegister
from statistikkprodukt.config.register import read_register_file
from test.test_config import configuration

HEADER = (
    "| Løpenr | Team   | Statistikkprodukt  |\n"
    "|--------+--------+--------------------|\n"
)


def _write(tmp_path, text):
    path = tmp_path / "statistikkprodukt_register.txt"
    path.write_text(text, encoding="utf-8")
    return path


def test_read_register(tmp_path):
    path = _write(
        tmp_path, HEADER
        + "| 21000 | Team A | ost_befolkning_alder |\n"
        + "| 21001 | Team B | ost_befolkning_kjonn |\n",
    )

    assert read_register_file(path) == {
        "21000": "ost_befolkning_alder",
        "21001": "ost_befolkning_kjonn",
    }


@pytest.mark.parametrize("delimiter", ["\t", ";", ","])
def test_read_register_rejects_other_delimiters(tmp_path, delimiter):
    path = _write(tmp_path, delimiter.join(["Løpenr", "Team", "Statistikkprodukt"]) + "\n")
    with pytest.raises(ConfigError, match="pipe-tabell"):
        read_register_file(path)


@pytest.mark.parametrize("agg_id", ["NAV21000", "nav21000", "00000", "00001", "09999", "20000", "21 000", "23000"])
def test_read_register_rejects_noncanonical_ids(tmp_path, agg_id):
    path = _write(tmp_path, HEADER + f"| {agg_id} | Team A | ost_befolkning_alder |\n")
    with pytest.raises(ConfigError, match="uten NAV-prefiks"):
        read_register_file(path)


def test_register_accepts_matching_id_and_table(tmp_path):
    aggregate = configuration().aggregater[0]
    path = _write(tmp_path, HEADER + f"| {aggregate.id} | Team A | {aggregate.tabell} |\n")
    StatistikkproduktRegister(path).valider([aggregate])


@pytest.mark.parametrize("allow_development_id", [False, True])
def test_register_rejects_unknown_id_even_with_flag(tmp_path, allow_development_id):
    aggregate = configuration().aggregater[0]
    path = _write(tmp_path, HEADER + "| 21001 | Team A | annen |\n")
    with pytest.raises(ConfigError, match=f"id {aggregate.id} finnes ikke"):
        StatistikkproduktRegister(path).valider(
            [aggregate], ingen_id_validering=allow_development_id
        )


def test_register_rejects_different_table_name(tmp_path):
    aggregate = configuration().aggregater[0]
    path = _write(tmp_path, HEADER + f"| {aggregate.id} | Team A | annen_tabell |\n")
    with pytest.raises(ConfigError, match="registrert på tabellen 'annen_tabell'"):
        StatistikkproduktRegister(path).valider([aggregate])


@pytest.mark.parametrize("aggregat_id", ["00000", "00001", "09999"])
def test_register_only_skips_development_id_with_flag(tmp_path, aggregat_id):
    aggregate = configuration().aggregater[0].model_copy(update={"id": aggregat_id})
    register = StatistikkproduktRegister(tmp_path / "finnes_ikke.txt")
    register.valider([aggregate], ingen_id_validering=True)
    with pytest.raises(ConfigError, match="uten NAV-prefiks"):
        register.valider([aggregate])


def test_read_register_rejects_duplicate_id(tmp_path):
    path = _write(tmp_path, HEADER + "| 21000 | A | x |\n| 21000 | B | y |\n")
    with pytest.raises(ConfigError, match="flere ganger"):
        read_register_file(path)


@pytest.mark.parametrize("header", ["\n", "| Nummer | Tabell |\n", "| Løpenummer | Team | Tabellnavn |\n"])
def test_read_register_requires_exact_header(tmp_path, header):
    path = _write(tmp_path, header + "|---+---+---|\n| 21000 | A | x |\n")
    with pytest.raises(ConfigError, match="må ha overskriftene"):
        read_register_file(path)


@pytest.mark.parametrize("separator", ["", "|---|---|---|\n", "--------+--------+--------------------\n"])
def test_read_register_requires_separator(tmp_path, separator):
    header = HEADER.splitlines()[0] + "\n"
    path = _write(tmp_path, header + separator + "| 21000 | A | x |\n")
    with pytest.raises(ConfigError, match="skillelinje"):
        read_register_file(path)


@pytest.mark.parametrize("row", ["| 21000 | A |\n", "| 21000 | A | x | ekstra |\n", "21000 | A | x\n"])
def test_read_register_requires_three_fields(tmp_path, row):
    with pytest.raises(ConfigError, match="nøyaktig tre felter"):
        read_register_file(_write(tmp_path, HEADER + row))


def test_missing_register_names_environment_variable(tmp_path):
    with pytest.raises(ConfigError, match="STATISTIKKPRODUKT_REGISTER_FIL"):
        read_register_file(tmp_path / "finnes_ikke.txt")
