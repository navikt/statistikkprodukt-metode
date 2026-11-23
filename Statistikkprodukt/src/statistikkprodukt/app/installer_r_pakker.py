from __future__ import annotations

import logging
import platform
import sys
from pathlib import Path

LOGGER = logging.getLogger(__name__)


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


def installer_r_pakker() -> None:
    """Installerer R-pakker som kreves for prikking."""
    import rpy2.robjects
    import rpy2.robjects.packages
    import rpy2.rinterface
    from rpy2.robjects.vectors import StrVector

    pkg_dir = Path(__file__).parents[4] / "NavDataprodukt"

    LOGGER.debug("Bruker R-pakkekatalog %s.", pkg_dir)
    utils = rpy2.robjects.packages.importr("utils")
    repository = _cran_repository()
    LOGGER.info("Installerer R-avhengigheter fra %s.", repository)
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
    utils.install_packages(
        StrVector(["SSBtools", "GaussSuppression", "DBI", "odbc", "rstudioapi", "pak"]),
        repos=repository,
        dependencies=True,
    )
    LOGGER.info("Installerer 'NavDataprodukt'...")
    # utils.install_packages(
    #     StrVector([str(pkg_dir)]),
    #     repos=rpy2.rinterface.NULL,
    #     type="source",
    #     upgrade=True
    # )
    pak = rpy2.robjects.packages.importr("pak")
    pak.pkg_install("navikt/statistikkprodukt-metode/NavDataprodukt@meta-felter", upgrade=True)
