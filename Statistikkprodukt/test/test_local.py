from pathlib import Path

import numpy as np
import pandas as pd
from statistikkprodukt.app import run
from statistikkprodukt.config import StorageOption
from test.test_config import configuration


def test_csv_masked(tmp_path):
    """Kjører CSV-pipelinen uten database og skriver prikkingmarkør."""
    config = configuration()
    input_directory = tmp_path / "data_inn"
    output_directory = tmp_path / "data_ut"
    input_directory.mkdir()
    pd.DataFrame(
        {"periode": [202501], "region": ["Oslo"], "antall": [3]}
    ).to_csv(input_directory / "tabell.csv", index=False)

    run(
        config,
        tmp_path,
        StorageOption.CSV,
        StorageOption.CSV,
        input_directory,
        output_directory
    )

    expected = [
        {'periode': 'I alt', 'region': 'I alt', 'antall': 3.0},
        {'periode': '202501', 'region': 'I alt', 'antall': 3.0},
        {'periode': '202501', 'region': 'Oslo', 'antall': 3.0}
    ]
    col_order = ["periode", "region", "antall"]

    sort_fn = lambda item: tuple(item[col] for col in col_order)
    expected.sort(key=sort_fn)
    expected_kontroll = [row.copy() | dict(primary=True, suppressed=True) for row in expected]
    expected_prepub = [row.copy() | dict(antall=None) for row in expected]

    prepub = pd.read_csv(output_directory / "tabell_prepub.csv")
    kontroll = pd.read_csv(output_directory / "tabell_kontroll.csv")

    # nan are not comparable, normalize them to None
    prepub = prepub.replace({np.nan: None})

    assert sorted(prepub.to_dict("records"), key=sort_fn) == expected_prepub
    assert sorted(kontroll.to_dict("records"), key=sort_fn) == expected_kontroll


def test_csv_unmasked(tmp_path):
    """Kjører CSV-pipelinen uten database og skriver prikkingmarkør."""
    config = configuration()
    input_directory = tmp_path / "data_inn"
    output_directory = tmp_path / "data_ut"
    input_directory.mkdir()
    pd.DataFrame(
        {"periode": [202501], "region": ["Oslo"], "antall": [12]}
    ).to_csv(input_directory / "tabell.csv", index=False)

    run(
        config,
        tmp_path,
        StorageOption.CSV,
        StorageOption.CSV,
        input_directory,
        output_directory
    )

    expected_prepub = [
        {'periode': 'I alt', 'region': 'I alt', 'antall': 12.0},
        {'periode': '202501', 'region': 'I alt', 'antall': 12.0},
        {'periode': '202501', 'region': 'Oslo', 'antall': 12.0}
    ]
    col_order = ["periode", "region", "antall"]

    sort_fn = lambda item: tuple(item[col] for col in col_order)
    expected_prepub.sort(key=sort_fn)
    expected_kontroll = [dict(**row, primary=False, suppressed=False) for row in expected_prepub]

    prepub = pd.read_csv(output_directory / "tabell_prepub.csv")
    kontroll = pd.read_csv(output_directory / "tabell_kontroll.csv")

    assert sorted(prepub.to_dict("records"), key=sort_fn) == expected_prepub
    assert sorted(kontroll.to_dict("records"), key=sort_fn) == expected_kontroll
