import logging
from pathlib import Path

import pandas as pd
from statistikkprodukt.config import SmallAggregate, InkrementalMode

LOGGER = logging.getLogger(__name__)


def read_csv(directory: Path, aggregate: SmallAggregate) -> pd.DataFrame:
    """Leser og validerer aggregatets CSV-inndata."""
    path = directory / f"{aggregate.tabell}.csv"
    try:
        frame = pd.read_csv(path)
    except OSError as exc:
        raise ValueError(f"Kan ikke lese CSV-filen {path}.") from exc
    except (pd.errors.ParserError, pd.errors.EmptyDataError, UnicodeDecodeError) as exc:
        raise ValueError(f"CSV-filen {path} har et ugyldig format.") from exc

    validate_columns(frame, aggregate)

    return frame


def validate_columns(frame: pd.DataFrame, aggregate: SmallAggregate) -> None:
    """Kontrollerer at CSV-kolonnene stemmer med aggregatkonfigurasjonen."""
    actual_by_lower = {str(column).lower(): column for column in frame.columns}
    if len(actual_by_lower) != len(frame.columns):
        raise ValueError(f"CSV-filen for {aggregate.tabell} har kolonner med like navn.")

    expected = [
                   variable.kolonne
                   for variable in aggregate.variabler
               ] + [aggregate.tallkolonne.kolonne]
    missing = [column for column in expected if column.lower() not in actual_by_lower]
    unexpected = [
        column for column in frame.columns
        if str(column).lower() not in {name.lower() for name in expected}
    ]

    if missing or unexpected:
        details = []
        if missing:
            details.append(f"mangler: {', '.join(missing)}")
        if unexpected:
            details.append(f"ukjente: {', '.join(map(str, unexpected))}")
        raise ValueError(f"Kolonnene i {aggregate.tabell}.csv stemmer ikke ({'; '.join(details)}).")

    frame.rename(
        columns={actual_by_lower[column.lower()]: column for column in expected},
        inplace=True,
    )


def write_csv(
        frame: pd.DataFrame,
        directory: Path,
        aggregate: SmallAggregate,
        name: str,
        *,
        dry_run: bool,
) -> None:
    """Skriver aggregatresultatet til {name}.csv med konfigurert inkrementell oppførsel."""
    path = directory / f"{name}.csv"
    if frame.empty:
        print(f"Ingen rader for {name}; CSV-filen endres ikke.")
        return

    if aggregate.inkrementell != InkrementalMode.OVERSKRIV_ALT:
        LOGGER.warning("CSV filer overskrives også når inkrementell modus er satt til %s.",
                       aggregate.inkrementell.value)

    if dry_run:
        print(f"Ville skrevet {len(frame)} rader til {path}.")
        return

    directory.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
