"""Kontroll av aggregat-ID og tabellnavn mot statistikkproduktregisteret."""
from __future__ import annotations

import os
import re
from collections.abc import Iterable
from pathlib import Path

from .aggregate_schema import Aggregate, ConfigError, is_aggregate_id, is_test_id, validate_identifier

STATISTIKKREGISTER_FIL_DEFAULT = "statistikkprodukt_register.txt"
STATISTIKKREGISTER_FIL = "STATISTIKKPRODUKT_REGISTER_FIL"

_COLUMN_ID = "Løpenr"
_COLUMN_TEAM = "Team"
_COLUMN_TABLE = "Statistikkprodukt"


def find_register_file() -> Path:
    """Finner statistikkproduktregisteret via miljøvariabel eller repository-roten."""
    if configured := os.environ.get(STATISTIKKREGISTER_FIL):
        return Path(configured).expanduser().resolve()
    # config/register.py -> config -> statistikkprodukt -> src -> Statistikkprodukt -> repo
    return Path(__file__).resolve().parents[4] / STATISTIKKREGISTER_FIL_DEFAULT


def read_register_file(path: Path) -> dict[str, str]:
    """Leser statistikkproduktregisterets pipe-tabell uten NAV-prefiks."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError as exc:
        raise ConfigError(
            f"Fant ikke statistikkproduktregisteret {path}. Angi stien med {STATISTIKKREGISTER_FIL}."
        ) from exc
    except UnicodeDecodeError as exc:
        raise ConfigError(f"Statistikkproduktregisteret må være kodet som UTF-8: {path}") from exc
    except OSError as exc:
        raise ConfigError(f"Statistikkproduktregisteret kan ikke leses: {path}") from exc

    lines = text.splitlines()
    if not lines:
        raise ConfigError(f"Statistikkproduktregisteret er tomt: {path}")
    required = (_COLUMN_ID, _COLUMN_TEAM, _COLUMN_TABLE)
    header = lines[0].strip().split("|")
    if (
        len(header) != 5 or header[0] or header[-1]
        or tuple(value.strip() for value in header[1:-1]) != required
    ):
        raise ConfigError(
            f"Statistikkproduktregisteret {path} må ha overskriftene "
            f"{', '.join(required)} i en pipe-tabell; fant {lines[0]!r}."
        )
    if len(lines) < 2 or not re.fullmatch(r"\|-+\+-+\+-+\|", lines[1].strip()):
        raise ConfigError(
            f"Statistikkproduktregisteret {path} må ha en skillelinje "
            "på formen |---+---+---| etter overskriftene."
        )

    tables: dict[str, str] = {}
    for line_number, line in enumerate(lines[2:], start=3):
        if not line.strip():
            continue
        parts = line.strip().split("|")
        if len(parts) != 5 or parts[0] or parts[-1]:
            raise ConfigError(f"Linje {line_number} i {path} må ha nøyaktig tre felter.")
        lopenummer, _team, table_name = (value.strip() for value in parts[1:-1])
        try:
            is_aggregate_id(lopenummer)
            table = validate_identifier(table_name)
        except ValueError as exc:
            raise ConfigError(f"Linje {line_number} i {path}: {exc}") from exc
        if table != table_name:
            raise ConfigError(f"Linje {line_number} i {path}: tabellnavnet må ha små bokstaver.")
        if lopenummer in tables:
            raise ConfigError(
                f"Løpenummer {lopenummer} står flere ganger i {path} (linje {line_number})."
            )
        tables[lopenummer] = table
    return tables


class StatistikkproduktRegister:
    """Skjuler oppslag og kontroll av registrerte statistikkprodukter."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or find_register_file()

    def valider(
        self, aggregates: Iterable[Aggregate], *, ingen_id_validering: bool = False
    ) -> None:
        """Krever registrert ID og tabellnavn, unntatt eksplisitt valgt utviklings-ID."""
        selected = []
        for aggregate in aggregates:
            if is_test_id(aggregate.id) and ingen_id_validering:
                continue
            is_aggregate_id(aggregate.id)
            selected.append(aggregate)
        if not selected:
            return

        tables = read_register_file(self.path)
        errors = []
        for aggregate in selected:
            expected = tables.get(aggregate.id)
            if expected is None:
                errors.append(
                    f"{aggregate.tabell}: id {aggregate.id} finnes ikke i statistikkproduktregisteret."
                )
            elif expected != aggregate.tabell:
                errors.append(
                    f"{aggregate.tabell}: id {aggregate.id} er registrert på tabellen "
                    f"{expected!r} i statistikkproduktregisteret, ikke {aggregate.tabell!r}."
                )
        if errors:
            raise ConfigError(
                f"Aggregatene stemmer ikke med statistikkproduktregisteret {self.path}:\n"
                + "\n".join(f"  - {error}" for error in errors)
            )
