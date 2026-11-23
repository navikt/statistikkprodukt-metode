from __future__ import annotations

import logging
import platform
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

LOGGER = logging.getLogger(__name__)

_PROXY_URLS = {
    "packagemanager.posit.co/*": "https://packagemanager.posit.co/cran/__linux__/noble/latest/src/contrib/PACKAGES",
    # SSBtools, mulig at den må oppdateres en gang når den ikke lengre er tilgjengelig
    "rspm-sync.rstudio.com/bin/*": "https://rspm-sync.rstudio.com/bin/4.6-noble/bde8c393c22d39782ea6858fc9975b38edb5fc53bbb7729bf9d00b2aee99ed57.tar.gz",
    "codeload.github.com/navikt/statistikkprodukt-metode/*": (
        "https://codeload.github.com/navikt/statistikkprodukt-metode/"
        "legacy.tar.gz/refs/heads/meta-felter"
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
    error = False
    for allowlist_pattern, test_url in _PROXY_URLS.items():
        request = Request(test_url, headers={"Range": "bytes=0-0"})
        try:
            with urlopen(request, timeout=15) as response:
                status = response.status
            if status >= 400:
                LOGGER.error("Kan ikke nå %s (HTTP %s).", test_url, status)
                missing_allowlist.append(allowlist_pattern)
                error = True
        except HTTPError as exc:
            LOGGER.exception("")
            if exc.code >= 400:
                LOGGER.error("Kan ikke nå %s (HTTP %s).", test_url, exc.code)
                missing_allowlist.append(allowlist_pattern)
            else:
                LOGGER.error("Feil under sjekk av brannmuråpninger: %s (HTTP %s).", test_url, exc.code)
            error = True
        except (URLError, TimeoutError, OSError) as exc:
            LOGGER.error("Feil under sjekk av brannmuråpninger: %s (%s).", test_url, exc)
            error = True

    if error:
        if missing_allowlist:
            items = "\n".join(f"- {item}" for item in missing_allowlist)
            LOGGER.error(
                "Kontroller at følgende adresser er åpnet i bannmuren:\n"
                f"{items}"
            )
        else:
            LOGGER.error(
                "Kan ikke nå nødvendige adresser for installasjon av R-pakker. "
                "Kontroller at du har tilgang til internett og at ingen brannmur blokkerer tilgangen."
            )

        raise SystemExit(1)


def installer_r_pakker() -> None:
    """Installerer R-pakker som kreves for prikking."""
    LOGGER.info("Installerer R-avhengigheter. R skriver ut mange warnings, men de kan ignoreres.")
    _sjekk_proxytilgang()

    import rpy2.robjects
    import rpy2.robjects.packages
    import rpy2.rinterface
    from rpy2.robjects.vectors import StrVector

    pkg_dir = Path(__file__).parents[4] / "NavDataprodukt"

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
        utils.install_packages(
            StrVector(["SSBtools", "GaussSuppression", "pak"]),
            repos=repository,
        )
        LOGGER.info("Installerer 'NavDataprodukt'...")
        # utils.install_packages(
        #     StrVector([str(pkg_dir)]),
        #     repos=rpy2.rinterface.NULL,
        #     type="source",
        #     upgrade=True
        # )
        pak = rpy2.robjects.packages.importr("pak")
        pak.pkg_install("navikt/statistikkprodukt-metode/NavDataprodukt@medta-felter", upgrade=True)

        LOGGER.info("R-pakker installert.")

    except Exception as exc:
        LOGGER.error("Feil under installasjon av R-pakker: %s", exc)
        raise SystemExit(1)