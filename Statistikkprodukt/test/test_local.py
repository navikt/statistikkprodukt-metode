from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest
from statistikkprodukt import oracle
from statistikkprodukt.app.last import last, _write_output
from statistikkprodukt.app.models import TableMetadata, Transfer
from statistikkprodukt.config import StorageOption
from statistikkprodukt.csv import validate_columns
from test.test_config import configuration
from test.test_meta_table import aggregate_with_metadata, resolved_metadata


def test_csv_columns_include_periodisering_separately():
    """Godtar periodekolonnen selv om den ikke står i variabler."""
    frame = pd.DataFrame(columns=["periode", "region", "antall"])

    validate_columns(frame, configuration().aggregater[0])


def test_validate_columns_normalizes_names_without_pandas_rename():
    frame = pd.DataFrame(columns=["PERIODE", "Region", "ANTALL"])

    validate_columns(frame, configuration().aggregater[0])

    assert list(frame.columns) == ["periode", "region", "antall"]


@pytest.mark.parametrize("source", [StorageOption.CSV, StorageOption.DB])
@pytest.mark.parametrize("empty_data", [False, True])
def test_csv_output_replaces_metadata_even_without_data(tmp_path, monkeypatch, source, empty_data):
    loaded_at = datetime(2026, 11, 23, 0, 29, 8, 123456)
    monkeypatch.setattr("statistikkprodukt.app.last.oslo_now", lambda: loaded_at)
    aggregate = aggregate_with_metadata()
    expected = resolved_metadata(aggregate) if source == StorageOption.DB else (
        TableMetadata.from_aggregate(aggregate)
    )
    if source == StorageOption.DB:
        expected.columns["region"].navn = "Navn fra database"
    read_metadata = MagicMock(return_value=expected)
    monkeypatch.setattr(oracle, "read_metadata", read_metadata)
    connection = MagicMock() if source == StorageOption.DB else None
    last_updated = None
    if connection is not None:
        last_updated = datetime(2026, 11, 22, 14, 30)
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.side_effect = [(1,), (last_updated,)]
    transfer = Transfer(connection, source, StorageOption.CSV, tmp_path, tmp_path)
    path = tmp_path / f"{aggregate.meta_tabell}.csv"
    path.write_text("utdatert metadata", encoding="utf-8")
    data = pd.DataFrame() if empty_data else pd.DataFrame({"antall": [12]})

    metadata = _write_output(
        configuration(), aggregate, data, data, transfer,
        config_directory=tmp_path, dry_run=False,
    )

    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    assert list(frame.columns) == [*oracle.META_COLUMNS, "dvh_lastet_tid"]
    assert frame.drop(columns="dvh_lastet_tid").to_dict("records") == [
        {key: value if value is not None else "" for key, value in row.items()}
        for row in oracle.metadata_rows(aggregate, expected, last_updated)
    ]
    assert metadata == expected
    assert frame["dvh_lastet_tid"].nunique() == 1
    loaded_at = pd.to_datetime(frame["dvh_lastet_tid"])
    assert loaded_at.notna().all()
    assert (loaded_at.dt.microsecond == 0).all()
    assert frame["dvh_lastet_tid"].unique().tolist() == ["2026-11-23 00:29:08"]
    for name in (aggregate.prepub_tabell, aggregate.kontroll_tabell):
        data_path = tmp_path / f"{name}.csv"
        if empty_data:
            assert not data_path.exists()
        else:
            output = pd.read_csv(data_path)
            assert output["dvh_lastet_tid"].tolist() == ["2026-11-23 00:29:08"]
            pd.testing.assert_frame_equal(output.drop(columns="dvh_lastet_tid"), data)
    assert "dvh_lastet_tid" not in data.columns
    if source == StorageOption.DB:
        read_metadata.assert_called_once_with(
            connection, configuration().connection.schema_name, aggregate
        )
        connection.commit.assert_not_called()
    else:
        read_metadata.assert_not_called()


@pytest.mark.parametrize("existing_file", [False, True])
def test_csv_metadata_dry_run_does_not_write(tmp_path, capsys, existing_file):
    aggregate = aggregate_with_metadata()
    output = tmp_path / "data_ut"
    path = output / f"{aggregate.meta_tabell}.csv"
    if existing_file:
        output.mkdir()
        path.write_text("Behold eksisterende metadata", encoding="utf-8")
    transfer = Transfer(None, StorageOption.CSV, StorageOption.CSV, tmp_path, output)
    _write_output(
        configuration(), aggregate, pd.DataFrame(), pd.DataFrame(), transfer,
        config_directory=tmp_path, dry_run=True,
    )
    assert f"{aggregate.meta_tabell}.csv" in capsys.readouterr().out
    if existing_file:
        assert path.read_text(encoding="utf-8") == "Behold eksisterende metadata"
    else:
        assert not output.exists()


def test_csv_pipeline_writes_metadata_without_database_or_r(tmp_path, monkeypatch):
    import statistikkprodukt.app.last as app

    config = configuration()
    aggregate = config.aggregater[0]
    input_directory = tmp_path / "data_inn"
    output_directory = tmp_path / "data_ut"
    input_directory.mkdir()
    pd.DataFrame(
        {"periode": ['2025M01'], "region": ["Oslo"], "antall": [12]}
    ).to_csv(input_directory / f"{aggregate.tabell}.csv", index=False)

    def mask(data, *args):
        control = data.copy()
        control["primary"] = False
        control["suppressed"] = False
        return data.copy(), control

    monkeypatch.setattr(app, "prikking", mask)
    connect = MagicMock(side_effect=AssertionError("CSV skal ikke koble til databasen"))
    monkeypatch.setattr(app, "_connect", connect)
    last(config, tmp_path, StorageOption.CSV, StorageOption.CSV,
         input_directory, output_directory)
    assert {path.name for path in output_directory.iterdir()} == {
        f"{aggregate.prepub_tabell}.csv",
        f"{aggregate.kontroll_tabell}.csv",
        f"{aggregate.meta_tabell}.csv",
    }
    frame = pd.read_csv(
        output_directory / f"{aggregate.meta_tabell}.csv",
        dtype=str,
        keep_default_na=False,
    )
    assert frame.loc[frame["keyword"] == "MATRIX", "value"].tolist() == ["21000"]
    connect.assert_not_called()


@pytest.mark.r_dependent
def test_csv_masked(tmp_path):
    """Kjører CSV-pipelinen uten database og skriver prikkingmarkør."""
    config = configuration()
    aggregate = config.aggregater[0]
    input_directory = tmp_path / "data_inn"
    output_directory = tmp_path / "data_ut"
    input_directory.mkdir()
    pd.DataFrame(
        {"periode": ['2025M01'], "region": ["Oslo"], "antall": [3]}
    ).to_csv(input_directory / f"{aggregate.tabell}.csv", index=False)

    last(
        config,
        tmp_path,
        StorageOption.CSV,
        StorageOption.CSV,
        input_directory,
        output_directory
    )

    expected = [
        {'periode': '2025M01', 'region': 'I alt', 'antall': 3.0},
        {'periode': '2025M01', 'region': 'Oslo', 'antall': 3.0}
    ]
    col_order = ["periode", "region", "antall"]

    sort_fn = lambda item: tuple(item[col] for col in col_order)
    expected.sort(key=sort_fn)
    expected_kontroll = [
        {
            "periode": row["periode"],
            "region": row["region"],
            "tellepunkt": "antall",
            "verdi": row["antall"],
            "primary": True,
            "suppressed": True,
        }
        for row in expected
    ]
    expected_prepub = [row.copy() | dict(antall=None) for row in expected]

    prepub = pd.read_csv(
        output_directory / f"{aggregate.prepub_tabell}.csv", dtype={"periode": str}
    )
    kontroll = pd.read_csv(
        output_directory / f"{aggregate.kontroll_tabell}.csv", dtype={"periode": str}
    )
    prepub = prepub.drop(columns="dvh_lastet_tid")
    kontroll = kontroll.drop(columns="dvh_lastet_tid")

    # nan are not comparable, normalize them to None
    prepub = prepub.replace({np.nan: None})

    assert sorted(prepub.to_dict("records"), key=sort_fn) == expected_prepub
    kontroll_sort_fn = lambda item: tuple(
        item[col] for col in ("periode", "region", "tellepunkt")
    )
    assert sorted(kontroll.to_dict("records"), key=kontroll_sort_fn) == expected_kontroll


@pytest.mark.r_dependent
def test_csv_unmasked(tmp_path):
    """Kjører CSV-pipelinen uten database og skriver prikkingmarkør."""
    config = configuration()
    aggregate = config.aggregater[0]
    input_directory = tmp_path / "data_inn"
    output_directory = tmp_path / "data_ut"
    input_directory.mkdir()
    pd.DataFrame(
        {"periode": ['2025M01'], "region": ["Oslo"], "antall": [12]}
    ).to_csv(input_directory / f"{aggregate.tabell}.csv", index=False)

    last(
        config,
        tmp_path,
        StorageOption.CSV,
        StorageOption.CSV,
        input_directory,
        output_directory
    )

    expected_prepub = [
        {'periode': '2025M01', 'region': 'I alt', 'antall': 12.0},
        {'periode': '2025M01', 'region': 'Oslo', 'antall': 12.0}
    ]
    col_order = ["periode", "region", "antall"]

    sort_fn = lambda item: tuple(item[col] for col in col_order)
    expected_prepub.sort(key=sort_fn)
    expected_kontroll = [
        {
            "periode": row["periode"],
            "region": row["region"],
            "tellepunkt": "antall",
            "verdi": row["antall"],
            "primary": False,
            "suppressed": False,
        }
        for row in expected_prepub
    ]

    prepub = pd.read_csv(
        output_directory / f"{aggregate.prepub_tabell}.csv", dtype={"periode": str}
    )
    kontroll = pd.read_csv(
        output_directory / f"{aggregate.kontroll_tabell}.csv", dtype={"periode": str}
    )
    prepub = prepub.drop(columns="dvh_lastet_tid")
    kontroll = kontroll.drop(columns="dvh_lastet_tid")

    assert sorted(prepub.to_dict("records"), key=sort_fn) == expected_prepub
    kontroll_sort_fn = lambda item: tuple(
        item[col] for col in ("periode", "region", "tellepunkt")
    )
    assert sorted(kontroll.to_dict("records"), key=kontroll_sort_fn) == expected_kontroll
