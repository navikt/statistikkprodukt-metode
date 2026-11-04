from pathlib import Path
from unittest.mock import MagicMock

import oracledb
import pandas as pd
import pytest
from statistikkprodukt import app, oracle
from statistikkprodukt.config import InkrementalMode, StorageOption
from test.test_config import configuration


@pytest.mark.parametrize(
    ("kilde", "maal"),
    [("db", "db"), ("csv", "db"), ("csv", "csv"), ("db", "csv")],
)
def test_run_passes_transfer_with_selected_modes(monkeypatch, tmp_path, kilde, maal):
    config = configuration()
    connection = MagicMock()
    connect = MagicMock(return_value=connection)
    transfers = []
    monkeypatch.setattr(app, "_connect", connect)
    monkeypatch.setattr(
        app,
        "_process_aggregate",
        lambda _config, _aggregate, _directory, transfer, **_kwargs: transfers.append(
            transfer
        ),
    )

    app.run(config, tmp_path, StorageOption(kilde), StorageOption(maal), Path("data_inn"), Path("data_ut"), dry_run=False)

    assert len(transfers) == 1
    transfer = transfers[0]
    assert transfer.source == kilde
    assert transfer.target == maal
    assert transfer.connection is (connection if kilde == "db" or maal == "db" else None)
    if transfer.connection is not None:
        connection.close.assert_called_once()
    else:
        connect.assert_not_called()


@pytest.mark.parametrize(
    ("mode", "available", "existing", "expected"),
    [
        ("overskriv_ikke", {202501, 202502, 202503}, {202502}, [202501, 202503]),
        ("overskriv_ikke", {202501}, {202501}, []),
        ("overskriv_ikke", {202501}, set(), [202501]),
        ("overskriv_ikke", set(), {202501}, []),
        ("overskriv_alt", {202501, 202502}, {202502, 202503}, [202501, 202502]),
        ("overskriv_alt", set(), {202501}, []),
        ("overskriv_siste_periode", {202500, 202501, 202502}, {202500, 202501}, [202501, 202502]),
        ("overskriv_siste_periode", {202500, 202501, 202502}, set(), [202500, 202501, 202502]),
        ("overskriv_siste_periode", set(), {202501}, []),
        ("overskriv_ikke", {"2025-01", "2025-02"}, {"2025-01"}, ["2025-02"]),
        ("overskriv_siste_periode", {"2025-01", "2025-02"}, set(), ["2025-01", "2025-02"]),
    ],
)
def test_get_periods_to_load(monkeypatch, tmp_path, mode, available, existing, expected):
    config = configuration()
    aggregate = config.smale_aggregater[0]
    aggregate.inkrementell = InkrementalMode(mode)
    fetch = MagicMock(return_value=(available, existing))
    monkeypatch.setattr(app, "_fetch_periods", fetch)

    assert app._get_periods_to_load(
        config,
        aggregate,
        tmp_path,
        app.Transfer(
            MagicMock(),
            StorageOption.DB,
            StorageOption.DB,
            MagicMock(),
            MagicMock()
        ),
        dry_run=False,
    ) == expected
    fetch.assert_called_once()


@pytest.mark.parametrize(
    ("target_storage", "dry_run"),
    [(StorageOption.CSV, False), (StorageOption.DB, True), (StorageOption.CSV, True)],
)
def test_all_periods_without_database_writes(monkeypatch, tmp_path, target_storage, dry_run):
    config = configuration()
    fetch = MagicMock(side_effect=AssertionError("Uventet periodeoppslag"))
    monkeypatch.setattr(app, "_fetch_periods", fetch)

    assert app._get_periods_to_load(
        config,
        config.smale_aggregater[0],
        tmp_path,
        app.Transfer(
            MagicMock(),
            StorageOption.DB,
            target_storage,
            Path("input"),
            Path("output")
        ),
        dry_run=dry_run,
    ) is None
    fetch.assert_not_called()


@pytest.mark.parametrize("csv_input", [False, True])
def test_fetch_periods_from_source_and_target(monkeypatch, tmp_path, csv_input):
    config = configuration()
    aggregate = config.smale_aggregater[0]
    connection = MagicMock()
    sql = "select periode, region, antall from kilde;"
    (tmp_path / aggregate.sporring_sql).write_text(sql)
    read_source = MagicMock(return_value={202501, 202502})
    read_target = MagicMock(return_value={202501})
    monkeypatch.setattr(oracle, "read_periods", read_source)
    monkeypatch.setattr(oracle, "read_target_periods", read_target)
    input_directory = tmp_path / "input"
    if csv_input:
        input_directory.mkdir()
        pd.DataFrame(
            {"periode": [202501, 202502, 202502], "region": ["Oslo"] * 3, "antall": [1, 2, 3]}
        ).to_csv(input_directory / "tabell.csv", index=False)

    assert app._fetch_periods(
        config,
        aggregate,
        tmp_path,
        app.Transfer(
            connection,
            StorageOption.CSV if csv_input else StorageOption.DB,
            StorageOption.DB,
            input_directory,
            Path("output")
        ),
    ) == ({202501, 202502}, {202501})
    read_target.assert_called_once_with(connection, "test", aggregate)
    if csv_input:
        read_source.assert_not_called()
    else:
        read_source.assert_called_once_with(connection, sql, "periode")


@pytest.mark.parametrize("periods", [None, [], [202502]])
def test_read_csv_period_selection(tmp_path, periods):
    aggregate = configuration().smale_aggregater[0]
    pd.DataFrame(
        {"periode": [202501, 202502], "region": ["Oslo", "Oslo"], "antall": [1, 2]}
    ).to_csv(tmp_path / "tabell.csv", index=False)

    data = app._read_input(
        aggregate,
        tmp_path,
        app.Transfer(
            None,
            StorageOption.CSV,
            StorageOption.CSV,
            tmp_path,
            Path("dummy")
        ),
        periods_to_load=periods,
    )
    assert data["periode"].tolist() == ([202501, 202502] if periods is None else periods)


@pytest.mark.parametrize("periods", [None, [], [202502]])
def test_process_selects_periods_before_reading(monkeypatch, tmp_path, periods):
    config = configuration()
    aggregate = config.smale_aggregater[0]
    calls = MagicMock()
    data = pd.DataFrame({"periode": [202502], "region": ["Oslo"], "antall": [2]})
    calls.select.return_value = periods
    calls.read.return_value = data
    monkeypatch.setattr(app, "_get_periods_to_load", calls.select)
    monkeypatch.setattr(app, "_read_input", calls.read)
    monkeypatch.setattr(app, "_write_output", calls.write)
    monkeypatch.setattr(oracle, "read_metadata", MagicMock())
    mask = MagicMock(return_value=(data, data.assign(primary=True, suppressed=True)))
    monkeypatch.setattr(app, "prikking", mask)

    app._process_aggregate(
        config,
        aggregate,
        tmp_path,
        app.Transfer(
            MagicMock(),
            StorageOption.DB,
            StorageOption.DB,
            Path("dummy"),
            Path("dummy")
        ),
        dry_run=False,
    )

    assert [call[0] for call in calls.mock_calls] == ["select", "read", "write"]
    assert calls.read.call_args.kwargs["periods_to_load"] == periods
    pd.testing.assert_frame_equal(mask.call_args.args[0], data)


def query_connection():
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.description = [
        ("PERIODE", oracledb.DB_TYPE_NUMBER, None, None, 6, 0, True),
        ("REGION", oracledb.DB_TYPE_VARCHAR, 100, None, None, None, True),
        ("ANTALL", oracledb.DB_TYPE_NUMBER, None, None, 10, 0, True),
    ]
    cursor.fetchall.return_value = []
    return connection, cursor


@pytest.mark.parametrize("periods", [None, [], [202501], ["x') OR 1=1 --"]])
def test_read_query_binds_period_values(periods):
    connection, cursor = query_connection()
    sql = "select periode, region, antall from kilde;"
    frame = oracle.read_query(
        connection, sql, configuration().smale_aggregater[0], periods_to_load=periods
    )

    query = cursor.execute.call_args.args[0]
    parameters = cursor.execute.call_args.args[1]
    if periods is None:
        assert query == sql.removesuffix(";")
        assert parameters == []
    elif not periods:
        assert query.endswith("WHERE 1 = 0")
        assert parameters == []
    else:
        assert query.endswith("WHERE periode IN (:1)")
        assert parameters == [periods[0]]
        assert str(periods[0]) not in query
    assert list(frame.columns) == ["periode", "region", "antall"]


def test_read_query_splits_large_period_lists():
    connection, cursor = query_connection()
    oracle.read_query(
        connection,
        "select periode, region, antall from kilde",
        configuration().smale_aggregater[0],
        periods_to_load=list(range(1001)),
    )
    query = cursor.execute.call_args.args[0]
    assert ":1000) OR periode IN (:1001)" in query
    assert len(cursor.execute.call_args.args[1]) == 1001


def test_read_periods_only_selects_distinct_periods():
    connection, cursor = query_connection()
    cursor.fetchall.return_value = [(202501,), (202502,)]

    assert oracle.read_periods(connection, "select * from kilde;", "periode") == {202501, 202502}
    cursor.execute.assert_called_once_with(
        "SELECT DISTINCT periode FROM (\nselect * from kilde\n) WHERE periode IS NOT NULL"
    )


@pytest.mark.parametrize("identifier", ["periode) OR 1=1 --", "test.tabell", "'periode'"])
def test_period_identifiers_are_validated(identifier):
    connection, cursor = query_connection()
    with pytest.raises(ValueError, match="Oracle-navn"):
        oracle.read_periods(connection, "select * from kilde", identifier)
    with pytest.raises(ValueError, match="Oracle-navn"):
        oracle.read_target_periods(connection, identifier, configuration().smale_aggregater[0])
    aggregate = configuration().smale_aggregater[0].model_copy(update={"periodisering": identifier})
    with pytest.raises(ValueError, match="Oracle-navn"):
        oracle.read_query(connection, "select * from kilde", aggregate, periods_to_load=[202501])
    cursor.execute.assert_not_called()
