from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, distribution, version
from importlib.resources import files
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion, Version

GITHUB_BRANCH = "meta-felter"
VERSION_URL = (
    "https://raw.githubusercontent.com/navikt/"
    f"statistikkprodukt-metode/{GITHUB_BRANCH}/pyproject.toml"
)
UPDATE_COMMAND = "uv tool upgrade statistikkprodukt"
VERSION_CHECK_TIMEOUT_SECONDS = 2

LOGGER = logging.getLogger(__name__)


def update_packages(*, update_r_packages: bool = True) -> None:
    """Oppdaterer Python-verktøyet og installerer deretter R-avhengighetene."""
    LOGGER.info("Oppdaterer Python-pakken.")
    subprocess.run(["uv", "tool", "upgrade", "statistikkprodukt"], check=True)
    if not update_r_packages:
        LOGGER.info("Oppdatering av R-pakker er hoppet over.")
        return
    LOGGER.info("Oppdaterer R-pakker.")
    subprocess.run(["statistikkprodukt", "installer-r-pakker"], check=True)


def _read_r_package_requirement() -> SpecifierSet:
    packaged_config = files("statistikkprodukt").joinpath("_version_requirements.toml")
    try:
        content = packaged_config.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        repository_config = Path(__file__).parents[3] / "pyproject.toml"
        content = repository_config.read_text(encoding="utf-8")

    config = tomllib.loads(content)
    return SpecifierSet(config["tool"]["statistikkprodukt"]["r_package_requirement"])


R_PACKAGE_REQUIREMENT = _read_r_package_requirement()


@dataclass(frozen=True)
class RPackageInfo:
    version: str
    remote_sha: str

    def __str__(self) -> str:
        return f"{self.version} ({self.remote_sha})"


@dataclass(frozen=True)
class PythonVersionInfo:
    latest: str
    minimum: str | None
    update_message: str | None


def get_python_version() -> str:
    return version("statistikkprodukt")


def is_editable_installation() -> bool:
    try:
        direct_url = distribution("statistikkprodukt").read_text("direct_url.json")
        if not direct_url:
            return False
        data = json.loads(direct_url)
    except (PackageNotFoundError, ValueError, json.JSONDecodeError):
        return False
    return data.get("dir_info", {}).get("editable") is True


def _is_interactive_terminal() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _read_python_version_info() -> PythonVersionInfo:
    with urlopen(VERSION_URL, timeout=VERSION_CHECK_TIMEOUT_SECONDS) as response:
        data = tomllib.loads(response.read().decode("utf-8"))

    project = data.get("project")
    tool = data.get("tool", {})
    if not isinstance(tool, dict):
        raise ValueError("pyproject.toml har ugyldig tool-oppsett.")
    tool_config = tool.get("statistikkprodukt", {})
    if not isinstance(project, dict) or not isinstance(project.get("version"), str):
        raise ValueError("pyproject.toml mangler project.version.")
    if not isinstance(tool_config, dict):
        raise ValueError("pyproject.toml har ugyldig tool.statistikkprodukt.")

    latest = Version(project["version"])
    minimum = tool_config.get("minimum_version")
    update_message = tool_config.get("update_message")
    if minimum is not None:
        if not isinstance(minimum, str):
            raise ValueError("pyproject.toml har ugyldig tool.statistikkprodukt.minimum_version.")
        Version(minimum)
    if update_message is not None and not isinstance(update_message, str):
        raise ValueError("pyproject.toml har ugyldig tool.statistikkprodukt.update_message.")
    return PythonVersionInfo(str(latest), minimum, update_message)


def _exit_with_manual_update_required_message(version_info: PythonVersionInfo) -> None:
    message = f"Versjon {version_info.minimum} er påkrevd. Oppdater med: {UPDATE_COMMAND}"
    if version_info.update_message:
        message += f". Oppdateringsinformasjon: {version_info.update_message}"
    LOGGER.error(message)
    raise SystemExit(1)


def _update_or_stop(version_info: PythonVersionInfo) -> None:
    message = f"Versjon {version_info.minimum} er påkrevd."
    if version_info.update_message:
        message += f" Oppdateringsinformasjon: {version_info.update_message}"

    if not _is_interactive_terminal():
        LOGGER.debug("Ikke interaktivt terminal; kan ikke tilby automatisk oppdatering.")
        _exit_with_manual_update_required_message(version_info)

    try:
        answer = input(f"{message} Oppdatere nå? [J/n] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = ""
    if answer in {"", "j"}:
        try:
            update_packages()
        except (OSError, subprocess.CalledProcessError):
            LOGGER.error(
                "Automatisk oppdatering av Python- og R-pakkene feilet. "
                "Kjør manuelt: %s og deretter "
                "'statistikkprodukt installer-r-pakker'",
                UPDATE_COMMAND,
            )
            raise SystemExit(1) from None
        LOGGER.info("Python- og R-pakkene er oppdatert. Kjør kommandoen på nytt.")
        raise SystemExit(0)

    _exit_with_manual_update_required_message(version_info)


def require_compatible_python_version_interactive(command: str) -> None:
    if should_skip_version_check():
        LOGGER.debug("Hopper over sjekk av Python-versjon på grunn av miljøvariabel.")
        return
    if is_editable_installation():
        LOGGER.debug("Hopper over sjekk av Python-versjon for editable installasjon.")
        return
    if command in {"installer-r-pakker", "oppdater", "testenv"}:
        LOGGER.debug("Hopper over sjekk av Python-versjon for kommando %s.", command)
        return

    try:
        version_info = _read_python_version_info()
        installed = Version(get_python_version())
        LOGGER.debug("Sjekker Python-versjon: installert %s, tilgjengelig %s.", installed, version_info.latest)
    except (
        OSError,
        URLError,
        TimeoutError,
        UnicodeDecodeError,
        tomllib.TOMLDecodeError,
        ValueError,
        InvalidVersion,
        PackageNotFoundError,
    ) as exc:
        LOGGER.warning(
            "Kunne ikke kontrollere om statistikkprodukt er oppdatert (%s). "
            "Kjøringen fortsetter. Kontroller at "
            "raw.githubusercontent.com/navikt/statistikkprodukt-metode/* "
            "er åpnet i brannmuren.",
            exc,
        )
        return

    if version_info.minimum is not None and installed < Version(version_info.minimum):
        LOGGER.debug("Installerte versjon %s er eldre enn minimumskravet %s.", installed, version_info.minimum)
        _update_or_stop(version_info)
        return

    if installed < Version(version_info.latest):
        LOGGER.warning(
            "En nyere versjon av statistikkprodukt er tilgjengelig "
            "(installert %s, tilgjengelig %s). Oppdater med: %s",
            installed,
            version_info.latest,
            UPDATE_COMMAND,
        )


def should_skip_version_check() -> bool:
    return os.environ.get("STATISTIKKPRODUKT_CHECK_VERSION", "1") == "0"


def get_r_package_info() -> RPackageInfo | None:
    from rpy2.robjects import packages, r

    try:
        packages.importr("NavDataprodukt")
    except packages.LibraryError:
        return None

    version_text = str(r('as.character(utils::packageVersion("NavDataprodukt"))')[0])
    remote_sha = str(r("""
        description <- utils::packageDescription("NavDataprodukt")
        sha <- description[["RemoteSha"]]
        if (is.null(sha) || is.na(sha) || !nzchar(sha)) "lokal" else sha
    """)[0])
    return RPackageInfo(version_text, remote_sha)


def format_r_package_version(package_info: RPackageInfo | None = None) -> str | None:
    package_info = package_info if package_info is not None else get_r_package_info()
    return str(package_info) if package_info is not None else None


def _is_r_version_valid(package_info: RPackageInfo | None) -> bool:
    if package_info is None:
        return False
    try:
        return Version(package_info.version) in R_PACKAGE_REQUIREMENT
    except InvalidVersion:
        return False


def require_compatible_r_package_interactive(*, offer_installation: bool = True) -> RPackageInfo | None:
    package_info = get_r_package_info()
    if _is_r_version_valid(package_info):
        return package_info

    if package_info is None:
        LOGGER.debug("R-pakken NavDataprodukt er ikke installert.")
    else:
        LOGGER.debug(
            "R-pakken NavDataprodukt har versjon %s, som ikke oppfyller kravet %s.",
            package_info.version, R_PACKAGE_REQUIREMENT,
        )

    installed = str(package_info.version) if package_info else "ikke installert"
    message = f"R-pakken NavDataprodukt har versjon {installed}, men versjonskravet er {R_PACKAGE_REQUIREMENT}."
    if is_editable_installation():
        LOGGER.warning("%s Fortsetter fordi installasjonen er editable.", message)
        return package_info

    if offer_installation:
        if not _is_interactive_terminal():
            LOGGER.debug("Ikke interaktivt terminal; kan ikke tilby automatisk installasjon av R-pakken.")
        else:
            try:
                answer = input(
                    f"{message} Installere R-pakken nå med 'statistikkprodukt installer-r-pakker'? [J/n] "
                ).strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = ""
            if answer in {"", "j"}:
                from statistikkprodukt.app.installer_r_pakker import installer_r_pakker

                installer_r_pakker(False)
                package_info = get_r_package_info()
                if _is_r_version_valid(package_info):
                    return package_info
                raise RuntimeError(
                    f"R-pakken oppfyller fortsatt ikke kravet {R_PACKAGE_REQUIREMENT} etter installasjon."
                )

    raise RuntimeError(
        f"{message} Kjør 'statistikkprodukt installer-r-pakker' for å installere en kompatibel versjon."
    )
