from __future__ import annotations

import logging
import re
from pathlib import Path

from statistikkprodukt.config import (
    Aggregate,
    StatistikkproduktRegister,
    load_configuration,
)

LOGGER = logging.getLogger(__name__)


def sjekk(config_path: str | Path | None, *, ingen_id_validering: bool = False) -> None:
    """Validerer konfigurasjonen og varsler om ufullstendige formler."""
    configuration, _ = load_configuration(
        config_path, ingen_id_validering=ingen_id_validering
    )
    StatistikkproduktRegister().valider(
        configuration.aggregater, ingen_id_validering=ingen_id_validering
    )
    for aggregate in configuration.aggregater:
        _warn_formula(aggregate)
    print(f"Konfigurasjonen er gyldig. Antall aggregater: {len(configuration.aggregater)}.")


def _warn_formula(aggregate: Aggregate) -> None:
    """Varsler om ufullstendige formler uten å avvise konfigurasjonen."""
    tokens = re.findall(r"[A-Za-z0-9_]+|[*+/:()]", aggregate.formula[1:])
    period = aggregate.periodisering.kolonne
    if tokens[0] != period:
        LOGGER.warning(
            "Aggregat %s: formula starter ikke med ~ %s.",
            aggregate.tabell, period,
        )
    columns = [period, *(variable.kolonne for variable in aggregate.variabler)]
    used_columns = set(tokens)
    missing = [column for column in columns if column not in used_columns]
    if missing:
        LOGGER.warning(
            "Aggregat %s: formula bruker ikke følgende kolonner fra variabler "
            "og periodisering: %s.",
            aggregate.tabell, ", ".join(missing),
        )
