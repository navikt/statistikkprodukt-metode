import pandas as pd
import logging

LOGGER = logging.getLogger(__name__)


def prikking(
        data: pd.DataFrame,
        periode: str,
        formula: str,
        tall_kolonne: str,
        prikking: bool,
        saerskilt: bool,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Kjører og samler R-resultater per periode når integrasjonen aktiveres."""
    if logging.root.level > logging.DEBUG:
        logging.getLogger("rpy2").setLevel(logging.ERROR)

    import rpy2.robjects as robjects
    import rpy2.robjects.packages as rpackages
    from rpy2.robjects import pandas2ri
    from rpy2.robjects.conversion import localconverter

    LOGGER.debug(
        "Starter prikking: %d rader, periodekolonne=%s, formel=%s, tallkolonne=%s, prikking=%s, særskilt=%s.",
        len(data), periode, formula, tall_kolonne, prikking, saerskilt,
    )
    if periode not in data.columns:
        raise ValueError(f"Periodekolonnen {periode} finnes ikke i datasettet.")

    importr = rpackages.importr
    try:
        importr('NavDataprodukt')
    except rpackages.LibraryError as exc:
        raise RuntimeError(
            "R-pakken 'NavDataprodukt' er ikke installert. Kjør 'statistikkprodukt installer-r-pakker' for å installere den."
        ) from exc

    LOGGER.debug("R-pakken 'NavDataprodukt' er lastet.")
    r_prikking = robjects.r('dvh_prikking')

    all_results_smalt = []
    all_results_kontroll = []

    for period in data[periode].drop_duplicates():
        if pd.isna(period):
            subset = data[data[periode].isna()]
        else:
            subset = data[data[periode] == period]
        LOGGER.debug("Kjører R-prikking for periode %s (%d rader).", period, len(subset))

        # localconverter konverterer automatisk fra R til pandas data frames
        with localconverter(robjects.default_converter + pandas2ri.converter):
            df_smalt, df_kontroll = r_prikking(
                data=subset,
                formula=formula,
                tallKol=tall_kolonne,
                prikking=prikking,
                saerskilt=saerskilt,
            )
        if not isinstance(df_smalt, pd.DataFrame):
            raise TypeError("R-funksjonen returnerte ikke et pandas-datasett for 'smalt_aggregat'.")
        all_results_smalt.append(df_smalt)

        if not isinstance(df_kontroll, pd.DataFrame):
            raise TypeError(f"R-funksjonen returnerte ikke et pandas-datasett for 'kvalitetssikring'.")
        all_results_kontroll.append(df_kontroll)

    if not all_results_smalt:
        LOGGER.debug("Ingen perioder å prikke; returnerer tomme resultater.")
        empty_df = data.iloc[0:0].copy()
        return empty_df, empty_df.copy()

    final_smalt = pd.concat(all_results_smalt, ignore_index=True)
    final_kontroll = pd.concat(all_results_kontroll, ignore_index=True)

    LOGGER.debug("Prikking ferdig: %d rader i resultatet, %d i kontrollen.", len(final_smalt), len(final_kontroll))
    # Returner DataFrames som tuple
    return final_smalt, final_kontroll
