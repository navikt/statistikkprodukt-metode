"""Kontroll av aggregat-ID og tabellnavn mot løpenummerlisten (Lopenr.txt)."""
from __future__ import annotations

import csv
import os
from collections.abc import Iterable
from pathlib import Path

from .aggregate_schema import Aggregate, ConfigError

LOPENUMMER_FILE = "Lopenr.txt"
LOPENUMMER_ENV = "STATISTIKKPRODUKT_LOPENUMMER"

_COLUMN_ID = "Løpenr"
_COLUMN_TEAM = "Team"
_COLUMN_TABLE = "Statistikkprodukt"
_DELIMITERS = "\t;,|"


def find_lopenummer_file() -> Path:
    """Finner lopenummer.txt: STATISTIKKPRODUKT_LOPENUMMER, ellers rotmappa i repoet."""
    if configured := os.environ.get(LOPENUMMER_ENV):
        return Path(configured).expanduser().resolve()
    # config/lopenummer.py -> config -> statistikkprodukt -> src -> Statistikkprodukt -> repo
    return Path(__file__).resolve().parents[4] / LOPENUMMER_FILE


def read_lopenummer(path: Path) -> dict[str, str]:
    """Leser løpenummerlisten og returnerer {løpenummer: statistikkprodukt}.

    Skilletegnet (tabulator, semikolon, komma eller |) gjenkjennes automatisk.
    Løpenumre kan stå med eller uten NAV-prefiks; tabellnavn sammenlignes uten
    hensyn til store og små bokstaver.
    """
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError as exc:
        raise ConfigError(
            f"Fant ikke løpenummerlisten {path}. Angi stien med {LOPENUMMER_ENV}."
        ) from exc
    except UnicodeDecodeError as exc:
        raise ConfigError(f"Løpenummerlisten må være kodet som UTF-8: {path}") from exc
    except OSError as exc:
        raise ConfigError(f"Løpenummerlisten kan ikke leses: {path}") from exc

    lines = text.splitlines()
    if not lines or not lines[0].strip():
        raise ConfigError(f"Løpenummerlisten er tom: {path}")
    required = (_COLUMN_ID, _COLUMN_TEAM, _COLUMN_TABLE)
    delimiter = next(
        (
            d for d in _DELIMITERS
            if set(required) <= {name.strip() for name in lines[0].split(d)}
        ),
        None,
    )
    if delimiter is None:
        raise ConfigError(
            f"Løpenummerlisten {path} må ha overskriftene {', '.join(required)}, "
            f"skilt med tabulator, semikolon, komma eller |; fant {lines[0]!r}."
        )

    reader = csv.DictReader(lines, delimiter=delimiter)
    reader.fieldnames = [name.strip() for name in reader.fieldnames or []]

    tables: dict[str, str] = {}
    for row in reader:  # DictReader hopper over tomme linjer
        line_number = reader.line_num
        if not any((value or "").strip() for value in row.values()):
            continue
        lopenummer = normalize_lopenummer(row[_COLUMN_ID] or "")
        table = (row[_COLUMN_TABLE] or "").strip().lower()
        if not lopenummer:
            raise ConfigError(f"Linje {line_number} i {path} mangler {_COLUMN_ID}.")
        if lopenummer in tables:
            raise ConfigError(
                f"Løpenummer {lopenummer} står flere ganger i {path} (linje {line_number})."
            )
        tables[lopenummer] = table
    return tables


def normalize_lopenummer(value: str) -> str:
    """Fjerner mellomrom og et eventuelt NAV-prefiks, f.eks. 'NAV21000' -> '21000'."""
    value = value.strip()
    return value[3:].strip() if value.upper().startswith("NAV") else value


def check_lopenummer(aggregates: Iterable[Aggregate], path: Path | None = None) -> None:
    """Kontrollerer at aggregat-ID finnes i løpenummerlisten med samme tabellnavn.

    Samler alle avvik og melder dem samlet.
    """
    path = path or find_lopenummer_file()
    tables = read_lopenummer(path)
    errors = []
    for aggregate in aggregates:
        expected = tables.get(aggregate.id)
        if expected is None:
            errors.append(
                f"{aggregate.tabell}: id {aggregate.id} finnes ikke i løpenummerlisten."
            )
        elif expected != aggregate.tabell.lower():
            errors.append(
                f"{aggregate.tabell}: id {aggregate.id} er registrert på tabellen "
                f"{expected!r} i løpenummerlisten, ikke {aggregate.tabell!r}."
            )
    if errors:
        raise ConfigError(
            f"Aggregatene stemmer ikke med løpenummerlisten {path}:\n"
            + "\n".join(f"  - {error}" for error in errors)
        )
