#' Statistical Data Suppression (Prikking)
#'
#' A wrapper function to perform data suppression using `GaussSuppressionFromData`.
#'
#' @param data A data frame containing the source columns.
#' @param varKol Character vector specifying the variable columns to group/analyze.
#' @param formula A formula object or string expression defining the model structure.
#' @param tallKol Character string indicating the numeric column to count/suppress.
#' @param prikking Character ("ja"/"nei"). If "nei", suppression is bypassed.
#' @param saerskilt Character ("ja"/"nei"). If "nei", normal threshold is used. If "ja", a higher threshold is used.
#' @param se_prikk Character ("ja"/"nei"). If "ja", returns flag columns; otherwise, blanks out data.
#'
#' @return A data frame with primary/secondary suppression applied.
#' @export
#'
#' @examples
#' \dontrun{
#' # Create dummy dataset
#' data <- data.frame(
#'   AAR_KVARTAL_BESK_KORT = c("2024K1", "2024K1", "2024K2", "2024K2"),
#'   NAVARENDE_FYLKE_NR_NAVN = c("Oslo", "Viken", "Oslo", "Viken"),
#'   KJONN_FLERTALL_BESK = c("Mann", "Kvinne", "Mann", "Kvinne"),
#'   KONTANTSTOTTE_ANTALL_MOTTAKERE = c(2, 15, 10, 4)
#' )
#' varKol = c("AAR_KVARTAL_BESK_KORT","NAVARENDE_FYLKE_NR_NAVN","KJONN_FLERTALL_BESK") 
#' tallKol = "KONTANTSTOTTE_ANTALL_MOTTAKERE"
#' formula = ~ AAR_KVARTAL_BESK_KORT : (NAVARENDE_FYLKE_NR_NAVN+ KJONN_FLERTALL_BESK)
#' prikking = "ja"
#' saerskilt = "nei"
#' se_prikk = "ja"
#' prikket <-Prikking(data = data, varKol = varKol,formula = formula, tallKol = tallKol, 
#'                   prikking = prikking,saerskilt = saerskilt, se_prikk = se_prikk)
#' }
#'
#' @importFrom GaussSuppression GaussSuppressionFromData
#' @importFrom stats as.formula


Prikking <- function(data, varKol,formula, tallKol, prikking,saerskilt,se_prikk) {
  utvalgte_kolonner <- c(varKol, tallKol)
  if (!all(utvalgte_kolonner %in% names(data))) {
    stop("En eller flere kolonner eksisterer ikke i kildetabellen.")
  }

  sub_data <- data[utvalgte_kolonner]

  sub_data[[tallKol]][is.na(sub_data[[tallKol]])] <- 0 # Må legges på hvis verdikolonner kan inneholde NULL

  # Valider ja/nei argumenter
  if (!tolower(prikking) %in% c("ja", "nei") ||
      !tolower(saerskilt) %in% c("ja", "nei") ||
      !tolower(se_prikk) %in% c("ja", "nei")) {
    stop("Argumentene 'prikking', 'saerskilt' og 'se_prikk' må være 'ja' eller 'nei'.", call. = FALSE)
  }

  if(tolower(prikking) == 'nei'){
    maxN_val     <- -1
    protectZeros <- FALSE
  }else if(tolower(saerskilt) == 'ja'){
    maxN_val     <- 20
    protectZeros <- TRUE
  }else{maxN_val     <- 3
  protectZeros <- TRUE
  }

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
    stop("Argumentet 'formula' må vaere et formelobjekt eller en tekststreng (f.eks. '~ var1 * var2').")
  }


  prikk <- GaussSuppressionFromData(data = sub_data,
                                    formula = formula,
                                    freqVar = tallKol,
                                    maxN = maxN_val,
                                    protectZeros = protectZeros,
                                    singletonMethod = "none",
                                    auto_anySumNOTprimary = FALSE, # Prevents the forced change that suppresses all sums
                                    extend0 = FALSE,
                                    avoidHierarchical = TRUE
  )

  # Fjern rader hvor alle variabler, inkludert periode er "Total"
  prikk <- prikk[rowSums(prikk[varKol] == "Total", na.rm = TRUE) < length(varKol), ]

  # Erstatt "Total" med "I alt"
  prikk[prikk == "Total"] <- "I alt"

  if(tolower(se_prikk) == 'ja'){
    return(prikk)
  }else{  prikk[[tallKol]][prikk$suppressed == TRUE] <- NA

  drops <-c("primary", "suppressed")
  prikk <- prikk[ , !(names(prikk) %in% drops), drop = FALSE]

  return(prikk)
  }
}
