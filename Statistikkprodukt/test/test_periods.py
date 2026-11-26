from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import oracledb
import pandas as pd
import pytest
import statistikkprodukt.app.last as app
from statistikkprodukt import csv, oracle
from statistikkprodukt.app.models import LasteResultat
from statistikkprodukt.config import IncrementalMode, StorageOption
from test.test_config import configuration

VALID_PERIODS = ["0001", "2025", "2025M01", "2025M12", "2025K1", "2025K4"]
INVALID_PERIODS = [
    "202501", "2025-01", "2025M00", "2025M13", "2025M1", "2025m01",
    "2025K0", "2025K5", "2025K01", "202", "20255", " 2025", "2025\n",
    "２０２５", "", None,
]


@pytest.mark.parametrize("period", VALID_PERIODS)
@pytest.mark.parametrize("column", ["periode", "PERIODE", "PeRiOdE"])
def test_csv_accepts_supported_period_formats(tmp_path, period, column):
    aggregate = configuration().aggregater[0]
    pd.DataFrame(
        {column: [period], "region": ["Oslo"], "antall": [10]}
    ).to_csv(tmp_path / f"{aggregate.tabell}.csv", index=False)

    frame = csv.read_csv(tmp_path, aggregate)

    assert frame["periode"].tolist() == [period]


@pytest.mark.parametrize("period", INVALID_PERIODS)
def test_csv_rejects_invalid_period_formats(tmp_path, period):
    aggregate = configuration().aggregater[0]
    pd.DataFrame(
        {"periode": [period], "region": ["Oslo"], "antall": [10]}
    ).to_csv(tmp_path / f"{aggregate.tabell}.csv", index=False)

    with pytest.raises(ValueError, match="Ugyldig periode"):
        csv.read_csv(tmp_path, aggregate)


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
    monkeypatch.setattr(oracle, "write_log", MagicMock())
    monkeypatch.setattr(
        app,
        "_process_aggregate",
        lambda _config, _aggregate, _directory, transfer, **_kwargs: transfers.append(
            transfer
        ),
    )

    app.last(config, tmp_path, StorageOption(kilde), StorageOption(maal), Path("data_inn"), Path("data_ut"), dry_run=False)

    assert len(transfers) == 1
    transfer = transfers[0]
    assert transfer.source == kilde
    assert transfer.target == maal
    assert transfer.connection is (connection if kilde == "db" or maal == "db" else None)
    if transfer.connection is not None:
        connection.close.assert_called_once()
    else:
        connect.assert_not_called()


@pytest.mark.parametrize("fails", [False, True])
def test_run_logs_outcome_and_configuration_without_password(monkeypatch, tmp_path, fails):
    config = configuration()
    aggregate = config.aggregater[0]
    connection = MagicMock()
    monkeypatch.setattr(app, "_connect", lambda _: connection)
    failure = ValueError("Lasting feilet")
    process = MagicMock(
        side_effect=failure if fails else None,
        return_value=LasteResultat(aggregate.id, aggregate.tabell, ["2025M01"], 10, 1, 2),
    )
    captured = []
    monkeypatch.setattr(app, "_process_aggregate", process)
    monkeypatch.setattr(oracle, "write_log", lambda _connection, _schema, logg: captured.append(logg))

    if fails:
        with pytest.raises(ValueError, match="Lasting feilet"):
            app.last(config, tmp_path, StorageOption.DB, StorageOption.DB, tmp_path, tmp_path)
    else:
        app.last(config, tmp_path, StorageOption.DB, StorageOption.DB, tmp_path, tmp_path)

    logg, = captured
    assert logg.status == ("FEILET" if fails else "OK")
    assert logg.feilmelding == ("Lasting feilet" if fails else None)
    assert "password" not in logg.konfigurasjon["connection"]
    assert logg.konfigurasjon["aggregater"][0]["id"] == aggregate.id
    assert logg.sluttid is not None
    connection.close.assert_called_once()


def test_log_failure_does_not_hide_original_load_failure(monkeypatch, tmp_path):
    config = configuration()
    connection = MagicMock()
    monkeypatch.setattr(app, "_connect", lambda _: connection)
    monkeypatch.setattr(app, "_process_aggregate", MagicMock(side_effect=ValueError("Lasting feilet")))
    monkeypatch.setattr(oracle, "write_log", MagicMock(side_effect=RuntimeError("Logging feilet")))

    with pytest.raises(ValueError, match="Lasting feilet"):
        app.last(config, tmp_path, StorageOption.DB, StorageOption.DB, tmp_path, tmp_path)
    connection.close.assert_called_once()


@pytest.mark.parametrize(
    ("mode", "available", "existing", "expected"),
    [
        ("legg_til_nye_perioder", {"2025M01", "2025M02", "2025M03"}, {"2025M02"}, ["2025M01", "2025M03"]),
        ("legg_til_nye_perioder", {"2025M01"}, {"2025M01"}, []),
        ("legg_til_nye_perioder", {"2025M01"}, set(), ["2025M01"]),
        ("legg_til_nye_perioder", set(), {"2025M01"}, []),
        ("overskriv_eksisterende_perioder", {"2025M01", "2025M02"}, {"2025M02", "2025M03"}, ["2025M01", "2025M02"]),
        ("overskriv_eksisterende_perioder", set(), {"2025M01"}, []),
        ("legg_til_nye_perioder", {"2025K1", "2025K2"}, {"2025K1"}, ["2025K2"]),
    ],
)
def test_get_periods_to_load(monkeypatch, tmp_path, mode, available, existing, expected):
    config = configuration()
    aggregate = config.aggregater[0]
    aggregate.inkrementell_strategi = IncrementalMode(mode)
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


@pytest.mark.parametrize("mode", list(IncrementalMode))
def test_explicit_periods_override_incremental_mode(monkeypatch, tmp_path, mode):
    config = configuration()
    aggregate = config.aggregater[0]
    aggregate.inkrementell_strategi = mode
    fetch = MagicMock(side_effect=AssertionError("Uventet periodeoppslag"))
    monkeypatch.setattr(app, "_fetch_periods", fetch)
    periods = ["2025M01", "2025M03"]

    assert app._get_periods_to_load(
        config,
        aggregate,
        tmp_path,
        app.Transfer(
            MagicMock(),
            StorageOption.DB,
            StorageOption.DB,
            MagicMock(),
            MagicMock(),
        ),
        selected_periods=periods,
        dry_run=False,
    ) == periods
    fetch.assert_not_called()


def test_full_relast_reads_all_periods_without_target_lookup(monkeypatch, tmp_path):
    config = configuration()
    fetch = MagicMock(side_effect=AssertionError("Uventet periodeoppslag"))
    monkeypatch.setattr(app, "_fetch_periods", fetch)

    assert app._get_periods_to_load(
        config,
        config.aggregater[0],
        tmp_path,
        app.Transfer(
            MagicMock(),
            StorageOption.DB,
            StorageOption.DB,
            Path("input"),
            Path("output"),
        ),
        full_relast=True,
        dry_run=False,
    ) is None
    fetch.assert_not_called()


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
        config.aggregater[0],
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
    aggregate = config.aggregater[0]
    connection = MagicMock()
    sql = "select periode, region, antall from kilde;"
    (tmp_path / aggregate.sporring_sql).write_text(sql)
    read_source = MagicMock(return_value={"2025M01", "2025M02"})
    read_target = MagicMock(return_value={"2025M01"})
    monkeypatch.setattr(oracle, "read_periods", read_source)
    monkeypatch.setattr(oracle, "read_target_periods", read_target)
    input_directory = tmp_path / "input"
    if csv_input:
        input_directory.mkdir()
        pd.DataFrame(
            {"periode": ['2025M01', '2025M02', '2025M02'], "region": ["Oslo"] * 3, "antall": [1, 2, 3]}
        ).to_csv(input_directory / f"{aggregate.tabell}.csv", index=False)

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
    ) == ({"2025M01", "2025M02"}, {"2025M01"})
    read_target.assert_called_once_with(connection, "test", aggregate)
    if csv_input:
        read_source.assert_not_called()
    else:
        read_source.assert_called_once_with(connection, sql, "periode")


@pytest.mark.parametrize("periods", [None, [], ["2025M02"]])
def test_read_csv_period_selection(tmp_path, periods):
    aggregate = configuration().aggregater[0]
    pd.DataFrame(
        {"periode": ["2025M01", "2025M02"], "region": ["Oslo", "Oslo"], "antall": [1, 2]}
    ).to_csv(tmp_path / f"{aggregate.tabell}.csv", index=False)

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
    assert data["periode"].tolist() == (
        ["2025M01", "2025M02"] if periods is None else periods
    )


def test_read_csv_filter_returns_string_period_from_numeric_csv(tmp_path):
    aggregate = configuration().aggregater[0]
    pd.DataFrame(
        {"periode": [2025, 2026], "region": ["Oslo", "Oslo"], "antall": [1, 2]}
    ).to_csv(tmp_path / f"{aggregate.tabell}.csv", index=False)

    data = app._read_input(
        aggregate,
        tmp_path,
        app.Transfer(
            None,
            StorageOption.CSV,
            StorageOption.CSV,
            tmp_path,
            Path("dummy"),
        ),
        periods_to_load=["2026"],
    )

    assert data["periode"].tolist() == ["2026"]


@pytest.mark.parametrize("periods", [None, [], ["2025M02"]])
def test_process_selects_periods_before_reading(monkeypatch, tmp_path, periods):
    config = configuration()
    aggregate = config.aggregater[0]
    calls = MagicMock()
    data = pd.DataFrame({"periode": ['2025M02'], "region": ["Oslo"], "antall": [2]})
    calls.select.return_value = periods
    calls.read.return_value = data
    monkeypatch.setattr(app, "_get_periods_to_load", calls.select)
    monkeypatch.setattr(app, "_read_input", calls.read)
    monkeypatch.setattr(app, "_write_output", calls.write)
    monkeypatch.setattr(oracle, "read_metadata", MagicMock())
    mask = MagicMock(return_value=(data.copy(), data.assign(primary=True, suppressed=True)))
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


def test_process_passes_full_relast_to_read_and_write(monkeypatch, tmp_path):
    config = configuration()
    aggregate = config.aggregater[0]
    data = pd.DataFrame({"periode": ["2025M02"], "region": ["Oslo"], "antall": [2]})
    select = MagicMock(return_value=None)
    read = MagicMock(return_value=data)
    write = MagicMock()
    monkeypatch.setattr(app, "_get_periods_to_load", select)
    monkeypatch.setattr(app, "_read_input", read)
    monkeypatch.setattr(app, "_write_output", write)
    monkeypatch.setattr(
        app,
        "prikking",
        lambda frame, *_args: (
            frame.copy(),
            frame.assign(primary=False, suppressed=False),
        ),
    )

    app._process_aggregate(
        config,
        aggregate,
        tmp_path,
        app.Transfer(
            MagicMock(),
            StorageOption.DB,
            StorageOption.DB,
            Path("dummy"),
            Path("dummy"),
        ),
        full_relast=True,
        dry_run=False,
    )

    assert select.call_args.kwargs["full_relast"] is True
    assert read.call_args.kwargs["periods_to_load"] is None
    assert write.call_args.kwargs["full_relast"] is True


def test_process_passes_only_selected_tellepunkt_to_r(monkeypatch, tmp_path):
    config = configuration()
    aggregate = config.aggregater[0]
    aggregate.tellepunkter.append(
        aggregate.tellepunkter[0].model_copy(update={"kolonne": "antall_kvinner"})
    )
    data = pd.DataFrame({
        "periode": ["2025M01"],
        "region": ["Oslo"],
        "antall": [10],
        "antall_kvinner": [7],
    })
    monkeypatch.setattr(app, "_get_periods_to_load", lambda *args, **kwargs: None)
    monkeypatch.setattr(app, "_read_input", lambda *args, **kwargs: data)
    write = MagicMock()
    monkeypatch.setattr(app, "_write_output", write)

    def mask(selected, periode, formula, tall_kolonne, prikking, saerskilt):
        assert list(selected.columns) == ["periode", "region", tall_kolonne]
        return selected.copy(), selected.assign(primary=False, suppressed=False)

    mask_mock = MagicMock(side_effect=mask)
    monkeypatch.setattr(app, "prikking", mask_mock)

    app._process_aggregate(
        config, aggregate, tmp_path,
        app.Transfer(None, StorageOption.CSV, StorageOption.CSV, tmp_path, tmp_path),
        dry_run=False,
    )

    assert mask_mock.call_count == 2
    prepub, kontroll = write.call_args.args[2:4]
    pd.testing.assert_frame_equal(prepub, data.astype({
        "antall": object, "antall_kvinner": object,
    }))
    assert kontroll["tellepunkt"].tolist() == ["antall", "antall_kvinner"]
    assert kontroll["verdi"].tolist() == [10, 7]


def query_connection():
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.description = [
        ("PERIODE", oracledb.DB_TYPE_VARCHAR, 6, None, None, None, True),
        ("REGION", oracledb.DB_TYPE_VARCHAR, 100, None, None, None, True),
        ("ANTALL", oracledb.DB_TYPE_NUMBER, None, None, 10, 0, True),
    ]
    cursor.fetchall.return_value = []
    return connection, cursor


@pytest.mark.parametrize("read_distinct", [False, True])
@pytest.mark.parametrize("period", VALID_PERIODS)
def test_oracle_accepts_supported_period_formats(period, read_distinct):
    connection, cursor = query_connection()
    if read_distinct:
        cursor.fetchall.return_value = [(period,)]
        assert oracle.read_periods(connection, "select periode from kilde", "periode") == {period}
    else:
        cursor.fetchall.return_value = [(period, "Oslo", 10)]
        frame = oracle.read_query(
            connection, "select * from kilde", configuration().aggregater[0]
        )
        assert frame["periode"].tolist() == [period]


@pytest.mark.parametrize("read_distinct", [False, True])
@pytest.mark.parametrize("period", INVALID_PERIODS)
def test_oracle_rejects_invalid_period_formats(period, read_distinct):
    connection, cursor = query_connection()
    cursor.fetchall.return_value = [(period,)] if read_distinct else [(period, "Oslo", 10)]

    with pytest.raises(ValueError, match="Ugyldig periode"):
        if read_distinct:
            oracle.read_periods(connection, "select periode from kilde", "periode")
        else:
            oracle.read_query(connection, "select * from kilde", configuration().aggregater[0])


@pytest.mark.parametrize("periods", [None, [], ["2025M01"], ["x') OR 1=1 --"]])
def test_read_query_binds_period_values(periods):
    connection, cursor = query_connection()
    sql = "select periode, region, antall from kilde;"
    frame = oracle.read_query(
        connection, sql, configuration().aggregater[0], periods_to_load=periods
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
        configuration().aggregater[0],
        periods_to_load=[str(period) for period in range(1001)],
    )
    query = cursor.execute.call_args.args[0]
    assert ":1000) OR periode IN (:1001)" in query
    assert len(cursor.execute.call_args.args[1]) == 1001


def test_read_periods_only_selects_distinct_periods():
    connection, cursor = query_connection()
    cursor.fetchall.return_value = [("2025M01",), ("2025M02",)]

    assert oracle.read_periods(connection, "select * from kilde;", "periode") == {"2025M01", "2025M02"}
    cursor.execute.assert_called_once_with(
        "SELECT DISTINCT periode FROM (\nselect * from kilde\n)"
    )


def test_read_query_reports_friendly_missing_source_table(oracle_connection):
    missing_table = f"missing_{uuid4().hex}"
    sql = (
        f"select '202501' as periode, 'Oslo' as region, 1 as antall "
        f"from {missing_table}"
    )

    with pytest.raises(ValueError, match="tabell eller visning.*finnes ikke"):
        oracle.read_query(
            oracle_connection, sql, configuration().aggregater[0]
        )


def test_read_periods_reports_friendly_with_subquery_error(oracle_connection):
    sql = (
        "WITH data AS (SELECT '2025M01' AS periode FROM dual) "
        "SELECT periode FROM data "
        "UNION ALL "
        "WITH other_data AS (SELECT '2025M02' AS periode FROM dual) "
        "SELECT periode FROM other_data"
    )
    with pytest.raises(ValueError, match="WITH-spørring"):
        oracle.read_periods(oracle_connection, sql, "periode")


def test_read_target_periods_preserves_database_errors(oracle_connection):
    aggregate = configuration().aggregater[0].model_copy(
        update={"tabell": f"missing_{uuid4().hex}"}
    )

    with pytest.raises(ValueError, match="tabellen finnes ikke") as exc_info:
        oracle.read_target_periods(
            oracle_connection, "SYSTEM", aggregate
        )


@pytest.mark.parametrize("identifier", ["periode) OR 1=1 --", "test.tabell", "'periode'"])
def test_period_identifiers_are_validated(identifier):
    connection, cursor = query_connection()
    with pytest.raises(ValueError, match="Oracle-navn"):
        oracle.read_periods(connection, "select * from kilde", identifier)
    with pytest.raises(ValueError, match="Oracle-navn"):
        oracle.read_target_periods(connection, identifier, configuration().aggregater[0])
    periodisering = configuration().aggregater[0].periodisering.model_copy(
        update={"kolonne": identifier}
    )
    aggregate = configuration().aggregater[0].model_copy(
        update={"periodisering": periodisering}
    )
    with pytest.raises(ValueError, match="Oracle-navn"):
        oracle.read_query(connection, "select * from kilde", aggregate, periods_to_load=["2025M01"])
    cursor.execute.assert_not_called()
