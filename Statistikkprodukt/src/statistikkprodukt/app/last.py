from __future__ import annotations

import logging
import json
from pathlib import Path

import oracledb
import pandas as pd
from pydantic import SecretStr
from statistikkprodukt import oracle, csv
from statistikkprodukt.app.models import Kjorelogg, LasteResultat, TableMetadata, Transfer
from statistikkprodukt.config import Configuration, IncrementalMode, Aggregate, is_test_id, resolve_secret, StorageOption
from statistikkprodukt.prikking import prikking

LOGGER = logging.getLogger(__name__)


def last(
        configuration: Configuration,
        config_directory: Path,
        source: StorageOption,
        target: StorageOption,
        input_directory: Path,
        output_directory: Path,
        *,
        selected_tables: list[str] | None = None,
        dry_run: bool = False,
) -> None:
    """Kjører valgte aggregater fra valgt kilde til valgt mål."""
    LOGGER.debug(
        "Starter kjøring: kilde=%s, mål=%s, dry_run=%s, valgte tabeller=%s.",
        source, target, dry_run, selected_tables or "alle",
    )
    aggregates = _select_aggregates(configuration, selected_tables)
    LOGGER.debug("Behandler %d aggregater: %s.", len(aggregates), [a.tabell for a in aggregates])

    transfer = Transfer.from_options(
        configuration,
        source,
        target,
        input_directory,
        output_directory,
        dry_run=dry_run,
    )

    logg = _initialize_kjorelogg(aggregates, configuration, source, target)

    try:
        for aggregate in aggregates:
            resultat = _process_aggregate(
                configuration,
                aggregate,
                config_directory,
                transfer,
                dry_run=dry_run,
            )
            logg.resultater.append(resultat)
    except BaseException as exc:
        LOGGER.debug("Kjøringen feilet: %r.", exc)
        logg.fullfor_feil(exc)
        _write_log(configuration, transfer, logg, dry_run=dry_run)
        raise
    else:
        LOGGER.debug("Kjøringen fullført uten feil.")
        logg.fullfor_suksess()
        _write_log(configuration, transfer, logg, dry_run=dry_run)
    finally:
        if transfer.connection is not None:
            LOGGER.debug("Lukker databasetilkoblingen.")
            transfer.connection.close()


def _initialize_kjorelogg(aggregates: list[Aggregate], configuration: Configuration, source: StorageOption,
                          target: StorageOption) -> Kjorelogg:
    konfigurasjon_json = configuration.model_dump(
        mode="json", by_alias=True, exclude={"connection": {"password"}}
    )
    konfigurasjon_json_str = json.dumps(konfigurasjon_json, ensure_ascii=False)
    if "password" in konfigurasjon_json_str or configuration.connection.password.get_secret_value() in konfigurasjon_json_str:
        raise RuntimeError("Konfigurasjonen inneholder et passord som ikke er maskert.")

    logg = Kjorelogg(
        konfigurasjon=konfigurasjon_json,
        kilde=str(source),
        maal=str(target),
        aggregater=aggregates,
    )
    return logg


def _write_log(
        configuration: Configuration,
        transfer: Transfer,
        logg: Kjorelogg,
        *,
        dry_run: bool,
) -> None:
    """Skriver kjøreloggen til Oracle når kjøringen skriver til databasen."""
    if transfer.target != StorageOption.DB or dry_run or transfer.connection is None:
        LOGGER.debug("Hopper over skriving av kjørelogg (mål=%s, dry_run=%s).", transfer.target, dry_run)
        return
    LOGGER.debug("Skriver kjørelogg med status %s.", logg.status)
    try:
        oracle.write_log(transfer.connection, configuration.connection.schema_name, logg)
    except Exception:
        # En feil i loggingen skal ikke skjule feilen fra selve kjøringen.
        if logg.status == "FEILET":
            LOGGER.exception("Kunne ikke skrive kjøreloggen.")
        else:
            raise


def _select_aggregates(
        configuration: Configuration,
        selected_tables: list[str] | None
) -> list[Aggregate]:
    """Velger alle aggregater eller kontrollerer eksplisitte tabellvalg."""
    if not selected_tables:
        LOGGER.debug("Ingen tabeller valgt; bruker alle aggregater i konfigurasjonen.")
        return configuration.aggregater

    wanted = {name.lower() for name in selected_tables}
    aggregates = [
        aggregate
        for aggregate in configuration.aggregater
        if aggregate.tabell.lower() in wanted
    ]
    unknown = wanted - {aggregate.tabell.lower() for aggregate in aggregates}
    if unknown:
        raise ValueError(f"Fant ikke småaggregatene: {', '.join(sorted(unknown))}.")
    return aggregates


def _connect(configuration: Configuration) -> oracledb.Connection:
    """Oppretter Oracle-tilkobling bare når kilden eller målet trenger den."""
    LOGGER.debug("Kobler til Oracle (skjema=%s).", configuration.connection.schema_name)
    user = resolve_secret(configuration.connection.user)
    password = resolve_secret(
        configuration.connection.password.get_secret_value(), password=True
    )
    resolved_connection = configuration.connection.model_copy(
        update={
            "user": user,
            "password": SecretStr(password),
        }
    )
    return oracle.connect(resolved_connection)


def _process_aggregate(
        configuration: Configuration,
        aggregate: Aggregate,
        config_directory: Path,
        transfer: Transfer,
        *,
        dry_run: bool,
) -> LasteResultat:
    """Leser, prikker og lagrer ett småaggregat, og returnerer hva som ble gjort."""
    LOGGER.info("Leser data for %s.", aggregate.tabell)
    LOGGER.debug(
        "Aggregat %s: id=%s, inkrementell strategi=%s, prikking=%s, særskilt=%s.",
        aggregate.tabell, aggregate.id, aggregate.inkrementell_strategi.value,
        aggregate.prikking, aggregate.saerskilt,
    )
    periods_to_load = _get_periods_to_load(
        configuration,
        aggregate,
        config_directory,
        transfer,
        dry_run=dry_run,
    )

    data = _read_input(
        aggregate,
        config_directory,
        transfer,
        periods_to_load=periods_to_load,
    )

    LOGGER.info("Prikker %d rader for %s.", len(data), aggregate.tabell)
    LOGGER.debug("Kolonner for %s: %s.", aggregate.tabell, list(data.columns))
    prepub, kontroll = prikking(
        data,
        aggregate.periodisering.kolonne,
        aggregate.formula,
        aggregate.tellepunkter[0].kolonne,
        aggregate.prikking,
        aggregate.saerskilt,
    )

    # ensure column order
    prepub = prepub[list(data.columns)]
    kontroll = kontroll[[*data.columns, "primary", "suppressed"]]

    perioder = sorted(map(str, prepub[aggregate.periodisering.kolonne].dropna().unique().tolist()))
    LOGGER.debug("Perioder i resultatet for %s: %s.", aggregate.tabell, perioder)
    resultat = LasteResultat(
        id=aggregate.id,
        tabell=aggregate.tabell,
        perioder=perioder,
        antall_rader=len(prepub),
        antall_primaerprikket=int(kontroll["primary"].sum()),
        antall_prikket=int(kontroll["suppressed"].sum()),
        metadata=None,
    )
    LOGGER.info(
        ("Resultat for tabell %(tabell)s: "
         "%(antall_perioder)d perioder, "
         "%(antall_rader)d rader, "
         "%(antall_primaerprikket)d primaerprikket, "
         "%(antall_prikket)d prikket."),
        {
            "tabell": aggregate.tabell,
            "antall_perioder": len(perioder),
            "antall_rader": resultat.antall_rader,
            "antall_primaerprikket": resultat.antall_primaerprikket,
            "antall_prikket": resultat.antall_prikket,
        }
    )

    metadata = _write_output(
        configuration,
        aggregate,
        prepub,
        kontroll,
        transfer,
        config_directory=config_directory,
        dry_run=dry_run,
    )
    resultat.metadata = metadata

    return resultat


def _get_periods_to_load(
        configuration: Configuration,
        aggregate: Aggregate,
        config_directory: Path,
        transfer: Transfer,
        *,
        dry_run: bool,
) -> list[int | str] | None:
    """Velger perioder før innlesing; None betyr alle, en tom liste betyr ingen."""
    if transfer.target == StorageOption.CSV or dry_run:
        LOGGER.debug("Leser alle perioder for %s (mål=%s, dry_run=%s).", aggregate.tabell, transfer.target, dry_run)
        return None
    if transfer.connection is None:
        raise RuntimeError("Databasetilkobling mangler.")

    available, existing = _fetch_periods(
        configuration,
        aggregate,
        config_directory,
        transfer,
    )
    LOGGER.debug(
        "Perioder for %s: tilgjengelige=%s, eksisterende=%s, strategi=%s.",
        aggregate.tabell, sorted(available, key=str), sorted(existing, key=str),
        aggregate.inkrementell_strategi.value,
    )
    missing = available - existing
    if aggregate.inkrementell_strategi == IncrementalMode.OVERSKRIV_ALT:
        periods = sorted(available)
    elif aggregate.inkrementell_strategi == IncrementalMode.OVERSKRIV_SISTE_PERIODE:
        max_available = {max(existing)} if existing and max(existing) in available else set()
        periods = sorted(missing.union(max_available))
    elif aggregate.inkrementell_strategi == IncrementalMode.OVERSKRIV_IKKE:
        periods = sorted(missing)
    else:
        raise NotImplementedError(
            f"Ukjent inkrementell modus: {aggregate.inkrementell_strategi}"
        )
    LOGGER.debug("Perioder som skal lastes for %s: %s.", aggregate.tabell, periods)
    return periods


def _fetch_periods(
        configuration: Configuration,
        aggregate: Aggregate,
        config_directory: Path,
        transfer: Transfer,
) -> tuple[set[int | str], set[int | str]]:
    """Henter tilgjengelige perioder fra datakilden og måltabellen."""
    assert transfer.connection is not None, "Databasetilkobling mangler."

    if transfer.source == StorageOption.CSV:
        directory = transfer.input_directory
        data = csv.read_csv(directory, aggregate)
        available = set(data[aggregate.periodisering.kolonne].dropna().tolist())
    else:
        available = oracle.read_periods(
            transfer.connection,
            _read_sql(aggregate, config_directory),
            aggregate.periodisering.kolonne,
        )

    existing = oracle.read_target_periods(
        transfer.connection, configuration.connection.schema_name, aggregate
    )
    return available, existing


def _read_sql(aggregate: Aggregate, config_directory: Path) -> str:
    """Leser SQL-filen relativt til konfigurasjonen."""
    sql_path = (config_directory / aggregate.sporring_sql).resolve()
    LOGGER.debug("Leser SQL-fil %s.", sql_path)
    try:
        return sql_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"SQL-filen kan ikke leses: {sql_path}") from exc


def _read_input(
        aggregate: Aggregate,
        config_directory: Path,
        transfer: Transfer,
        *,
        periods_to_load: list[int | str] | None = None,
) -> pd.DataFrame:
    """Leser aggregatdata fra CSV eller Oracle."""
    LOGGER.debug(
        "Leser inndata for %s fra %s; perioder=%s.",
        aggregate.tabell, transfer.source, "alle" if periods_to_load is None else periods_to_load,
    )
    if transfer.source == StorageOption.CSV:
        directory = transfer.input_directory
        data = csv.read_csv(directory, aggregate)
        if periods_to_load is not None:
            data = data[data[aggregate.periodisering.kolonne].isin(periods_to_load)]
        LOGGER.debug("Leste %d rader fra CSV for %s.", len(data), aggregate.tabell)
        return data
    else:
        if transfer.connection is None:
            raise RuntimeError("Databasetilkobling mangler.")

        sql_text = _read_sql(aggregate, config_directory)
        return oracle.read_query(
            transfer.connection, sql_text, aggregate, periods_to_load=periods_to_load
        )


def _write_output(
        configuration: Configuration,
        aggregate: Aggregate,
        prepub: pd.DataFrame,
        kontroll: pd.DataFrame,
        transfer: Transfer,
        *,
        config_directory: Path,
        dry_run: bool,
) -> TableMetadata | None:
    """Sender prikkede data og kontrolldata til CSV eller Oracle."""
    LOGGER.debug(
        "Skriver resultat for %s til %s (prepub=%d rader, kontroll=%d rader).",
        aggregate.tabell, transfer.target, len(prepub), len(kontroll),
    )
    if transfer.target == StorageOption.CSV:
        directory = transfer.output_directory
        csv.write_csv(prepub, directory, aggregate, aggregate.prepub_tabell, dry_run=dry_run)
        csv.write_csv(kontroll, directory, aggregate, aggregate.kontroll_tabell, dry_run=dry_run)
        return None
    else:
        return _write_database(
            configuration,
            aggregate,
            prepub,
            kontroll,
            transfer,
            dry_run=dry_run,
        )


def _write_database(
        configuration: Configuration,
        aggregate: Aggregate,
        prepub: pd.DataFrame,
        kontroll: pd.DataFrame,
        transfer: Transfer,
        *,
        dry_run: bool,
) -> TableMetadata | None:
    """Skriver prepub- og kontrolltabellen til Oracle."""
    schema = configuration.connection.schema_name
    connection = transfer.connection
    results = [
        (aggregate.prepub_tabell, prepub),
        (aggregate.kontroll_tabell, kontroll),
    ]
    if connection is None:
        if not dry_run:
            raise RuntimeError("Databasetilkobling mangler.")

        for table_name, frame in results:
            LOGGER.info(
                "Ville skrevet %d rader til %s.%s.",
                len(frame),
                schema,
                table_name,
            )
        return

    metadata = None
    if not dry_run:
        LOGGER.debug("Leser metadata for %s.", aggregate.tabell)
        if is_test_id(aggregate.id):
            LOGGER.warning(
                "Data for %s legges inn uten gyldig NAV-ID (id='%s'). "
                "Disse dataene må fjernes før publisering.",
                aggregate.tabell,
                aggregate.id,
            )
        metadata = oracle.read_metadata(connection, schema, aggregate)

    for table_name, frame in results:
        oracle.write_result(
            connection,
            schema,
            aggregate,
            table_name,
            frame,
            dry_run=dry_run,
        )
        if not dry_run:
            LOGGER.info("Oppdaterer kommentarer for %s.", table_name)
            oracle.write_comments(connection, schema, table_name, metadata)

    if not dry_run:
        LOGGER.info("Oppdaterer metadatatabellen %s.", aggregate.meta_tabell)
        oracle.write_metadata_table(connection, schema, aggregate, metadata)
    return metadata
