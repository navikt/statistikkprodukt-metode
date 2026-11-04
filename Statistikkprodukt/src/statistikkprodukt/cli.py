from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from statistikkprodukt import app
from statistikkprodukt.config import load_configuration, ConfigError, StorageOption

LOG_LEVELS = ("DEBUG", "INFO", "WARN", "ERROR")
LOGGER = logging.getLogger(__name__)


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


def _add_aggregate_options(parser: argparse.ArgumentParser) -> None:
    """Legger til valg for konfigurasjon, filer og aggregater."""
    parser.add_argument(
        "aggregater",
        nargs="*",
        metavar="TABELL",
        help="Tabellnavn for småaggregatene som skal behandles (standard: alle).",
    )
    parser.add_argument(
        "--config",
        default=os.environ.get("STATISTIKKPRODUKT_AGGREGAT_CONFIG"),
        help="YAML-konfigurasjonsfil (standard: STATISTIKKPRODUKT_AGGREGAT_CONFIG eller konfigurasjon.yml).",
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
    _add_logging_options(parser)


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
        "--config",
        default=os.environ.get("STATISTIKKPRODUKT_AGGREGAT_CONFIG"),
        help="YAML-konfigurasjonsfil (standard: STATISTIKKPRODUKT_AGGREGAT_CONFIG).",
    )
    create.add_argument(
        "--db",
        action="store_true",
        help="Opprett tabellene i Oracle i stedet for å skrive DDL til standard output.",
    )
    _add_logging_options(create)

    installer_r_pakker = commands.add_parser(
        "installer-r-pakker", help="Installer R-pakker som kreves for å kjøre prikking og andre statistikkprodukter."
    )
    _add_logging_options(installer_r_pakker)

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

    logging.basicConfig(level=getattr(args, "log_level", "INFO"), format="%(levelname)s %(name)s: %(message)s")

    try:
        if args.command == "testenv":
            _run_testenv(args, parser)

        elif args.command == "last":
            config_path = args.config
            configuration, config_directory = load_configuration(config_path)

            app.run(
                configuration,
                config_directory,
                args.kilde,
                args.maal,
                args.kilde_mappe,
                args.maal_mappe,
                selected_tables=args.aggregater,
                dry_run=args.dry_run,
            )
        elif args.command == "skjema":
            configuration, config_directory = load_configuration(args.config)
            app.create_tables(
                configuration,
                config_directory,
                selected_tables=args.aggregater,
                create_in_database=args.db,
            )
        elif args.command == "installer-r-pakker":
            app.installer_r_pakker()
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


def _run_testenv(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    """Utfører valgt testmiljøkommando."""
    from statistikkprodukt import testenv

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


if __name__ == "__main__":
    main()
