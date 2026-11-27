from __future__ import annotations

import logging
import platform
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pandas as pd

from statistikkprodukt.prikking import prikking
from statistikkprodukt.version import GITHUB_BRANCH, require_compatible_r_package_interactive

LOGGER = logging.getLogger(__name__)

_PROXY_URLS = {
    "packagemanager.posit.co/*": "https://packagemanager.posit.co/cran/__linux__/noble/latest/src/contrib/PACKAGES",
    # SSBtools, mulig at den må oppdateres en gang når den ikke lengre er tilgjengelig
    "rspm-sync.rstudio.com/bin/*": "https://rspm-sync.rstudio.com/bin/4.6-noble/bde8c393c22d39782ea6858fc9975b38edb5fc53bbb7729bf9d00b2aee99ed57.tar.gz",
    "codeload.github.com/navikt/statistikkprodukt-metode/*": (
        "https://codeload.github.com/navikt/statistikkprodukt-metode/"
        f"legacy.tar.gz/refs/heads/{GITHUB_BRANCH}"
    ),
    "raw.githubusercontent.com/navikt/statistikkprodukt-metode/*": (
        "https://raw.githubusercontent.com/navikt/"
        f"statistikkprodukt-metode/{GITHUB_BRANCH}/pyproject.toml"
    ),
}


def _cran_repository() -> str:
    if sys.platform == "linux":
        try:
            os_release = platform.freedesktop_os_release()
        except OSError:
            os_release = {}
        codename = os_release.get("VERSION_CODENAME")
        if os_release.get("ID") == "ubuntu" and codename in {
            "jammy", "noble", "resolute"
        }:
            return f"https://packagemanager.posit.co/cran/__linux__/{codename}/latest"
    return "https://cloud.r-project.org"


def _sjekk_proxytilgang() -> None:
    """Kontrollerer om brannmuren er konfigurert riktig før R-pakker lastes ned."""
    missing_allowlist = []
    timeout_sec = 15
    error = False
    for allowlist_pattern, test_url in _PROXY_URLS.items():
        request = Request(test_url, headers={"Range": "bytes=0-0"})
        try:
            with urlopen(request, timeout=timeout_sec) as response:
                status = response.status

            if status >= 400:
                LOGGER.error("Kan ikke nå %s (HTTP %s).", test_url, status)
                missing_allowlist.append(allowlist_pattern)
                error = True
        except HTTPError as exc:
            if exc.code >= 400:
                LOGGER.error("Kan ikke nå %s (HTTP %s).", test_url, exc.code)
                missing_allowlist.append(allowlist_pattern)
            else:
                LOGGER.error("Feil under sjekk av brannmuråpninger: %s (HTTP %s).", test_url, exc.code)
                LOGGER.debug("Detaljer: %s", exc_info=True)
            error = True
        except TimeoutError:
            LOGGER.error("Kan ikke nå %s (timeout etter %ds).", test_url, timeout_sec)
            missing_allowlist.append(allowlist_pattern)
            error = True
        except (URLError, TimeoutError, OSError) as exc:
            LOGGER.error("Feil under sjekk av brannmuråpninger: %s (%s).", test_url, exc)
            LOGGER.debug("Detaljer: %s", exc_info=True)
            error = True

    if error:
        if missing_allowlist:
            items = "\n".join(f"- {item}" for item in missing_allowlist)
            LOGGER.error(
                "Kontroller at følgende adresser er åpnet i brannmuren:\n"
                f"{items}"
            )
        else:
            LOGGER.error(
                "Kan ikke nå nødvendige adresser for installasjon av R-pakker. "
                "Kontroller at du har tilgang til internett og at ingen brannmur blokkerer tilgangen."
            )

        raise SystemExit(1)


def _sjekk_r_miljo() -> None:
    data = pd.DataFrame({
        "periode": ["202501"] * 4,
        "gruppe": ["A", "B", "C", "D"],
        "antall": [2, 10, 20, 30],
    })
    try:
        prikking(
            data,
            periode="periode",
            formula="~ periode * gruppe",
            tall_kolonne="antall",
            prikking=True,
            saerskilt=False,
        )
    except Exception as exc:
        raise RuntimeError(
            "Funksjonstesten av R-miljøet feilet. Kjør med --log-level=DEBUG for mer informasjon."
        ) from exc


def installer_r_pakker(dev_lokal_r_pakke: bool) -> None:
    """Installerer R-pakker som kreves for prikking.
    
    Args:
        dev_lokal_r_pakke (bool): Hvis True, installerer R-pakken fra lokal kilde i stedet for fra GitHub. 
            Dette er nyttig for utvikling og testing.
    """
    LOGGER.info("Installerer R-avhengigheter.")
    _sjekk_proxytilgang()

    import rpy2.robjects
    import rpy2.robjects.packages
    import rpy2.rinterface
    from rpy2.robjects.vectors import StrVector

    pkg_dir = Path(__file__).parents[4] / "NavDataprodukt"

    if logging.root.level > logging.DEBUG:
        logging.getLogger("rpy2").setLevel(logging.ERROR)

    LOGGER.debug("Bruker R-pakkekatalog %s.", pkg_dir)
    utils = rpy2.robjects.packages.importr("utils")
    repository = _cran_repository()
    LOGGER.debug("Bruker CRAN-repository %s.", repository)
    # pak leser repos fra R-innstillingene, ikke fra install.packages().
    rpy2.robjects.r["options"](repos=rpy2.robjects.r["c"](CRAN=repository))
    rpy2.robjects.r("""
        options(
            download.file.method = "libcurl",
            HTTPUserAgent = sprintf(
                "R/%s R (%s)",
                getRversion(),
                paste(getRversion(), R.version$platform, R.version$arch, R.version$os)
            )
        )
    """)
    try:
        LOGGER.info("Installerer R avhengighet SSBtools...")
        utils.install_packages(StrVector(["SSBtools"]), repos=repository)
        LOGGER.info("Installerer R avhengighet GaussSuppression...")
        utils.install_packages(StrVector(["GaussSuppression"]), repos=repository)
        if not dev_lokal_r_pakke:
            LOGGER.info("Installerer R avhengighet pak...")
            utils.install_packages(StrVector(["pak"]), repos=repository)

        if dev_lokal_r_pakke:
            LOGGER.info("Installerer 'NavDataprodukt' fra lokal repository...")
            utils.install_packages(
                StrVector([str(pkg_dir)]),
                repos=rpy2.rinterface.NULL,
                type="source",
                upgrade=True
            )
        else:
            LOGGER.info(
                "Installerer 'NavDataprodukt' fra GitHub (branch %s)...",
                GITHUB_BRANCH,
            )
            pak = rpy2.robjects.packages.importr("pak")
            pak.pkg_install(
                "navikt/statistikkprodukt-metode/"
                f"NavDataprodukt@{GITHUB_BRANCH}",
                upgrade=True,
            )

        LOGGER.info("Tester R-miljøet...")
        _sjekk_r_miljo()
        require_compatible_r_package_interactive(offer_installation=False)
        LOGGER.info("R-pakker installert og funksjonstest fullført.")

    except Exception as exc:
        LOGGER.error("Feil under installasjon eller funksjonstest av R-pakker: %s", exc)
        raise SystemExit(1)
