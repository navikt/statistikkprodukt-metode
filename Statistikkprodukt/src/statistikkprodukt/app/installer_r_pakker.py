from __future__ import annotations

import logging
from pathlib import Path

LOGGER = logging.getLogger(__name__)


def installer_r_pakker() -> None:
    """Installerer R-pakker som kreves for prikking."""
    import rpy2.robjects
    import rpy2.robjects.packages
    import rpy2.rinterface
    from rpy2.robjects.vectors import StrVector

    pkg_dir = Path(__file__).parents[4] / "NavDataprodukt"

    LOGGER.info("Installere 'NavDataprodukt'...")
    utils = rpy2.robjects.packages.importr("utils")
    utils.install_packages(
        StrVector(["SSBtools", "GaussSuppression", "openxlsx", "readxl", "DBI", "odbc", "rstudioapi"]),
        dependencies=True,
    )
    utils.install_packages(
        StrVector([str(pkg_dir)]),
        repos=rpy2.rinterface.NULL,
        type="source",
    )
