from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytz
from statistikkprodukt.config import Aggregate, Metadata


def oslo_now() -> datetime:
    """Returnerer lokal tid i Europe/Oslo uten tidssoneinformasjon til Oracle TIMESTAMP."""
    return datetime.now(pytz.timezone("Europe/Oslo")).replace(tzinfo=None)


def _program_commit() -> str | None:
    repository = Path(__file__).resolve().parents[3]
    try:
        result = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True, timeout=5,
        )
        commit = result.stdout.strip()
        if re.fullmatch(r"[0-9a-f]{40,64}", commit):
            return commit
    except (OSError, subprocess.SubprocessError):
        pass

    commit = os.environ.get("STATISTIKKPRODUKT_COMMIT_SHA")
    if commit and re.fullmatch(r"[0-9a-f]{40,64}", commit):
        return commit
    try:
        direct_url = distribution("statistikkprodukt").read_text("direct_url.json")
        if direct_url:
            commit = json.loads(direct_url).get("vcs_info", {}).get("commit_id")
            if commit and re.fullmatch(r"[0-9a-f]{40,64}", commit):
                return commit
    except (PackageNotFoundError, ValueError):
        pass
    return None


@dataclass
class TableMetadata:
    table: Metadata
    columns: dict[str, Metadata]


@dataclass
class LasteResultat:
    """Hva som ble gjort med ett aggregat i en kjøring."""
    id: str
    tabell: str
    perioder: list[str]
    antall_rader: int
    antall_primaerprikket: int
    antall_prikket: int
    metadata: TableMetadata | None = None


@dataclass
class Kjorelogg:
    """Samler informasjon om én kjøring frem til den skrives til loggtabellen."""
    konfigurasjon: dict[str, Any]
    kilde: str
    maal: str
    aggregater: list[Aggregate]
    kjoring_id: str = field(default_factory=lambda: str(uuid4()))
    starttid: datetime = field(default_factory=oslo_now)
    sluttid: datetime | None = None
    status: str = "PÅGÅR"
    feilmelding: str | None = None
    resultater: list[LasteResultat] = field(default_factory=list)
    commit_hash: str | None = field(default_factory=_program_commit)

    def fullfor_feil(self, feil: BaseException) -> None:
        self.sluttid = oslo_now()
        self.status = "FEILET"
        self.feilmelding = str(feil)

    def fullfor_suksess(self) -> None:
        self.sluttid = oslo_now()
        self.status = "OK"
        self.feilmelding = None

    def som_rader(self) -> list[dict[str, Any]]:
        """Lager bindverdier, én rad per aggregat med fullstendige JSON-verdier."""
        kjoring = {
            "kjoring_id": self.kjoring_id,
            "starttid": self.starttid,
            "sluttid": self.sluttid or oslo_now(),
            "status": self.status,
            "commit_hash": self.commit_hash,
            "konfigurasjon": json.dumps(self.konfigurasjon, ensure_ascii=False),
            "kilde": self.kilde,
            "maal": self.maal,
            "antall_aggregater": len(self.aggregater),
            "feilmelding": self.feilmelding,
        }
        resultater = {resultat.tabell: resultat for resultat in self.resultater}
        rows = []
        for aggregate in self.aggregater:
            resultat = resultater.get(aggregate.tabell)
            resolved = resultat.metadata.table if resultat and resultat.metadata else aggregate
            rows.append({
                **kjoring,
                "aggregat_id": aggregate.id,
                "tabell": aggregate.tabell,
                **{name: getattr(resolved, name) for name in Metadata.model_fields},
                "obt_tabell": aggregate.obt_tabell,
                "periodisering": aggregate.periodisering.kolonne,
                "inkrementell": str(aggregate.inkrementell_strategi),
                "formula": aggregate.formula,
                "prikking": int(aggregate.prikking),
                "saerskilt": int(aggregate.saerskilt),
                "sporring_sql": aggregate.sporring_sql,
                "perioder": (
                    json.dumps(resultat.perioder, ensure_ascii=False)
                    if resultat else None
                ),
                "antall_rader": resultat.antall_rader if resultat else None,
                "antall_primaerprikket": resultat.antall_primaerprikket if resultat else None,
                "antall_prikket": resultat.antall_prikket if resultat else None,
            })
        return rows
