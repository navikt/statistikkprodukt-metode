#' Statistical Data Suppression to use from python (dvh_prikking)
#'
#' A wrapper function to perform data suppression using `GaussSuppressionFromData` from the data warehouse, using python to call this function.
#'
#' @param data A data frame containing only the relevant columns for the result, that is the dimension columns and one value.
#' @param formula A formula object or string expression defining the model structure.
#' @param tallKol Character string indicating the numeric column to count/suppress.
#' @param prikking Character ("ja"/"nei"). If "nei", suppression is bypassed.
#' @param saerskilt Character ("ja"/"nei"). If "nei", normal threshold is used. If "ja", a higher threshold is used.
#'
#' @return The functions return two data frame with primary/secondary suppression applied, one that is cleaned, and one that contains original data with suppression flags.
#' @export
#'
#' @importFrom GaussSuppression GaussSuppressionFromData
#' @importFrom stats as.formula
dvh_prikking <- function(data, formula, tallKol, prikking,saerskilt) {

  colnames(data) <- tolower(colnames(data))

  freqVar <- tolower(tallKol)
  data[[freqVar]][is.na(data[[freqVar]])] <- 0 # Maa legges paa hvis verdikolonner kan inneholde NULL

  if(tolower(prikking) == 'false'){
    maxN_val     <- -1
    protectZeros <- FALSE
  }else if(tolower(saerskilt) == 'true'){
    maxN_val     <- 20
    protectZeros <- TRUE
  }else{maxN_val     <- 3
  protectZeros <- TRUE
  }

  formula <- as.formula(tolower(format(formula)))

  # Formula validering
  if (inherits(formula, "formula")) {
    # formula er ok
  } else if (is.character(formula) && length(formula) == 1) {
    # tekst oversettes til formula
    formula <- tryCatch({
      as.formula(formula)
    }, error = function(e) {
      stop("Formelen ble oppgitt som tekst, men er ikke i et gyldig R-formelformat (f.eks. '~ var1 * var2').")
    })
  } else {
    stop("Argumentet 'formula' maa vaere et formelobjekt eller en tekststreng (f.eks. '~ var1 * var2').")
  }


  prikk <- GaussSuppressionFromData(data = data,
                                    formula = formula,
                                    freqVar = freqVar,
                                    maxN = maxN_val,
                                    protectZeros = protectZeros,
                                    singletonMethod = "none",
                                    auto_anySumNOTprimary = FALSE, # Prevents the forced change that suppresses all sums
                                    extend0 = FALSE,
                                    avoidHierarchical = TRUE
  )


  # Erstatt "Total" med "I alt"
  prikk[prikk == "Total"] <- "I alt"

  smal <- prikk

  smal[[freqVar]][smal$suppressed == TRUE] <- NA

  drops <-c("primary", "suppressed")
  smal <- smal[ , !(names(smal) %in% drops), drop = FALSE]

  return(list(smalt_aggregat = smal, kvalitetssikring = prikk))

}
