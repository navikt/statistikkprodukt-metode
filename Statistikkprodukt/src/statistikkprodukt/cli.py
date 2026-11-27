from __future__ import annotations

import argparse
import logging
import os
from argparse import Namespace
from pathlib import Path
from typing import Any

from statistikkprodukt.app import installer_r_pakker, konfig, last, skjema
from statistikkprodukt.version import (
    require_compatible_python_version_interactive,
    update_packages,
)
from statistikkprodukt.config import (
    ConfigError,
    StorageOption,
    StatistikkproduktRegister,
    load_configuration,
    validate_period,
)

LOG_LEVELS = ("DEBUG", "INFO", "WARN", "ERROR")
LOGGER = logging.getLogger(__name__)


class HideInfoFormatter(logging.Formatter):
    def __init__(self):
        super().__init__(fmt="%(levelname)s: %(message)s")

    def format(self, record):
        if record.levelno == logging.INFO:
            self._style._fmt = "%(message)s"
        else:
            self._style._fmt = "%(levelname)s: %(message)s"
        return super().format(record)


def setup_logging(args: Namespace) -> Any | str:
    level = getattr(args, "log_level", "INFO")
    if level == "INFO":
        handler = logging.StreamHandler()
        handler.setFormatter(HideInfoFormatter())
        logging.basicConfig(level=level, handlers=[handler])
    else:
        logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")
    return level


def _add_logging_options(parser: argparse.ArgumentParser) -> None:
    """Legger til flagg for loggnivå og detaljert visning."""
    parser.add_argument(
        "--log-level",
        choices=LOG_LEVELS,
        default="INFO",
        type=str.upper,
        help="Velg hvor detaljert meldingsloggen skal være (standard: INFO).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Vis også verdier fra metadata og mer detaljerte meldinger.",
    )


def _parse_period(value: str) -> str:
    try:
        return validate_period(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _add_aggregate_options(parser: argparse.ArgumentParser) -> None:
    """Legger til valg for konfigurasjon, filer og aggregater."""
    parser.add_argument(
        "aggregater",
        nargs="*",
        metavar="TABELL",
        help="Tabellnavn for småaggregatene som skal behandles (standard: alle).",
    )
    parser.add_argument(
        "--konfig-fil",
        default=os.environ.get("STATISTIKKPRODUKT_AGGREGAT_CONFIG"),
        help="YAML-konfigurasjonsfil (standard: STATISTIKKPRODUKT_AGGREGAT_CONFIG eller konfigurasjon.yml).",
    )
    period_mode = parser.add_mutually_exclusive_group()
    period_mode.add_argument(
        "-p",
        "--period",
        action="append",
        dest="periods",
        type=_parse_period,
        metavar="PERIODE",
        help=(
            "Begrens innlesingen til denne perioden (YYYY, YYYYM01–YYYYM12 "
            "eller YYYYK1–YYYYK4). Kan angis flere ganger. "
            "Lastes uansett inkrementell strategi."
        ),
    )
    period_mode.add_argument(
        "--full-relast",
        dest="full_relast",
        action="store_true",
        help="Slett alle data i måltabellene og last alle rader fra kilden på nytt.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Vis hva som ville blitt gjort uten å skrive data.")
    parser.add_argument(
        "--kilde",
        type=StorageOption,
        choices=list(StorageOption),
        default=StorageOption.DB,
        help="Velg datakilde: db eller csv (standard: db).",
    )
    parser.add_argument(
        "--maal",
        type=StorageOption,
        choices=list(StorageOption),
        default=StorageOption.DB,
        help="Velg mål for resultatet: db eller csv (standard: db).",
    )
    parser.add_argument(
        "--kilde-mappe",
        type=Path,
        default=Path("data_inn"),
        metavar="MAPPE",
        help="Mappe med CSV-filer når --kilde=csv (standard: data_inn).",
    )
    parser.add_argument(
        "--maal-mappe",
        type=Path,
        default=Path("data_ut"),
        metavar="MAPPE",
        help="Mappe for CSV-filer når --maal=csv (standard: data_ut).",
    )
    _add_id_validation_option(parser)
    _add_logging_options(parser)


def _add_id_validation_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--ingen-id-validering",
        action="store_true",
        help="Tillat test-ID-er fra '00000' til '09999'. Dataene må fjernes før publisering.",
    )


def _run_testenv(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    """Utfører valgt testmiljøkommando."""
    from statistikkprodukt.app import testenv

    if args.testenv_command == "setup":
        testenv.setup()
    elif args.testenv_command == "teardown":
        testenv.teardown()
    else:
        arguments = list(args.pytest_args)
        if arguments:
            if arguments[0] == "--":
                arguments.pop(0)
            else:
                parser.error("Argumenter til pytest må starte med --.")

        pytest_exit_code = testenv.run_pytest(arguments)
        raise SystemExit(pytest_exit_code)


def _report_error(message: str, *, debug: bool) -> None:
    """Viser en norsk feilmelding og eventuelle feilsøkingsdetaljer."""
    LOGGER.error(f"{message}")
    if debug:
        LOGGER.exception("Detaljer for feilen:")


def build_parser() -> argparse.ArgumentParser:
    """Bygger kommandolinjens underkommandoer og argumenter."""
    parser = argparse.ArgumentParser(
        prog="statistikkprodukt",
        description="Lag og vedlikehold småaggregater for statistikkprodukten.",
        epilog="Bruk 'statistikkprodukt last --help' for flere valg.",
    )

    commands = parser.add_subparsers(dest="command", required=True)
    last = commands.add_parser(
        "last", help="Opprett eller oppdater småaggregater i databasen."
    )
    _add_aggregate_options(last)

    config = commands.add_parser("konfig", help="Kontroller konfigurasjonen.")
    config_commands = config.add_subparsers(dest="config_command", required=True)
    check = config_commands.add_parser(
        "sjekk",
        help="Valider YAML-konfigurasjonen og aggregat-ID-er uten å kjøre lasten.",
        description=(
            "Valider hovedkonfigurasjonen, aggregatdefinisjonene og aggregat-ID-er. "
            "Henter ikke hemmeligheter, kobler ikke til databasen og skriver ingen data."
        ),
    )
    check.add_argument(
        "--konfig-fil",
        default=os.environ.get("STATISTIKKPRODUKT_AGGREGAT_CONFIG"),
        help="YAML-konfigurasjonsfil (standard: STATISTIKKPRODUKT_AGGREGAT_CONFIG).",
    )
    _add_id_validation_option(check)
    _add_logging_options(check)

    schema = commands.add_parser("skjema", help="Vis eller opprett småaggregattabeller.")
    schema_commands = schema.add_subparsers(dest="schema_command", required=True)
    create = schema_commands.add_parser(
        "opprett", help="Vis DDL eller opprett tomme småaggregattabeller."
    )
    create.add_argument(
        "aggregater",
        nargs="*",
        metavar="TABELL",
        help="Tabellnavn for aggregatene (standard: alle).",
    )
    create.add_argument(
        "--konfig-fil",
        default=os.environ.get("STATISTIKKPRODUKT_AGGREGAT_CONFIG"),
        help="YAML-konfigurasjonsfil (standard: STATISTIKKPRODUKT_AGGREGAT_CONFIG).",
    )
    create.add_argument(
        "--db",
        action="store_true",
        help="Opprett tabellene i Oracle i stedet for å skrive DDL til standard output.",
    )
    _add_id_validation_option(create)
    _add_logging_options(create)

    installer_r_pakker = commands.add_parser(
        "installer-r-pakker", help="Installer R-pakker som kreves for å kjøre prikking og andre statistikkprodukter."
    )
    installer_r_pakker.add_argument(
        "--dev-lokal-r-pakke",
        action="store_true",
        help="Installer R-pakken fra lokal repository i stedet for fra GitHub. Fungerer kun for utvikling og testing.",
    )
    _add_logging_options(installer_r_pakker)

    update = commands.add_parser(
        "oppdater",
        help="Oppdater Python-verktøyet og R-pakkene.",
        description="Oppdaterer statistikkprodukt med uv og installerer deretter nødvendige R-pakker.",
    )
    update.add_argument(
        "--ikke-oppdater-r-pakker",
        action="store_true",
        help="Oppdater bare Python-verktøyet, uten å oppdatere R-pakkene.",
    )
    _add_logging_options(update)

    testenv = commands.add_parser(
        "testenv", help="Administrer Oracle-miljøet som brukes av tester."
    )

    testenv_commands = testenv.add_subparsers(dest="testenv_command", required=True)
    testenv_setup = testenv_commands.add_parser("setup", help="Start det eksterne Oracle-testmiljøet.")
    _add_logging_options(testenv_setup)

    teardown_parser = testenv_commands.add_parser("teardown", help="Stopp og fjern det administrerte testmiljøet.")
    _add_logging_options(teardown_parser)

    pytest_parser = testenv_commands.add_parser(
        "pytest", help="Kjør pytest mot Oracle-testmiljøet som allerede kjører."
    )

    pytest_parser.add_argument(
        "pytest_args",
        nargs=argparse.REMAINDER,
        help="Argumenter som sendes videre til pytest etter --.",
    )
    return parser


def main() -> None:
    """Tolker argumenter, kjører valgt kommando og håndterer feil."""
    parser = build_parser()
    args = parser.parse_args()

    level = setup_logging(args)

    LOGGER.debug("Kommando=%s, loggnivå=%s.", args.command, level)

    try:
        require_compatible_python_version_interactive(args.command)

        if args.command == "testenv":
            _run_testenv(args, parser)

        elif args.command == "konfig":
            konfig.sjekk(
                args.config, ingen_id_validering=args.ingen_id_validering
            )

        elif args.command == "last":
            config_path = args.config
            LOGGER.debug("Kjører 'last' med konfigurasjon %s.", config_path)
            configuration, config_directory = load_configuration(
                config_path, ingen_id_validering=args.ingen_id_validering
            )
            StatistikkproduktRegister().valider(
                configuration.aggregater, ingen_id_validering=args.ingen_id_validering
            )
            last.last(
                configuration,
                config_directory,
                args.kilde,
                args.maal,
                args.kilde_mappe,
                args.maal_mappe,
                selected_tables=args.aggregater,
                selected_periods=args.periods,
                full_relast=args.full_relast,
                dry_run=args.dry_run,
            )
        elif args.command == "skjema":
            LOGGER.debug("Kjører 'skjema opprett' med konfigurasjon %s (db=%s).", args.config, args.db)
            configuration, config_directory = load_configuration(
                args.config, ingen_id_validering=args.ingen_id_validering
            )
            StatistikkproduktRegister().valider(
                configuration.aggregater, ingen_id_validering=args.ingen_id_validering
            )
            skjema.create_tables(
                configuration,
                config_directory,
                selected_tables=args.aggregater,
                create_in_database=args.db,
            )
        elif args.command == "installer-r-pakker":
            LOGGER.debug("Kjører 'installer-r-pakker'.")
            installer_r_pakker.installer_r_pakker(args.dev_lokal_r_pakke)
        elif args.command == "oppdater":
            LOGGER.debug("Kjører 'oppdater'.")
            update_packages(update_r_packages=not args.ikke_oppdater_r_pakker)
        else:
            raise NotImplementedError("Ukjent kommando: " + args.command)
    except ConfigError as exc:
        _report_error(
            f"Konfigurasjonsfeil: {exc}",
            debug=args.log_level == "DEBUG",
        )
        raise SystemExit(2) from None
    except Exception as exc:
        _report_error(
            f"{exc}. Bruk --log-level=DEBUG for å se detaljer.",
            debug=(args.command == "testenv" or args.log_level == "DEBUG"),
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
