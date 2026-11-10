#' Connect to the Company Data Warehouse (DVH)
#'
#' Establishes an ODBC connection to the data warehouse environments.
#' @param database Character.
#' @param uid Character. Your user identification.
#' @param dwhi_cred Character vector.
#'
#' @return A DBIConnection object.
#' @export
#'
#' @importFrom odbc dbConnect odbc
#' @importFrom rstudioapi askForPassword

db_connect <- function(database = "DWHI", uid = NULL, dwhi_cred = c("x","y","z")) {

  # TODO: Plasser dsn, uid og pwd i keyvault

  if (!requireNamespace("odbc", quietly = TRUE)) {
    stop(
      "Pakken 'odbc' er nødvendig for at denne funksjonen skal kjøre. ",
      "Vennligst installer pakken med å kjøre: install.packages('odbc')",
      call. = FALSE
    )
  }
  

  # DWHI der alle i Nav har tilgang
  if (tolower(database) == "dwhi") {

    con <- odbc::dbConnect(
      odbc::odbc(),
      dsn      = dwhi_cred[1],
      uid      = dwhi_cred[2],
      pwd      = dwhi_cred[3]
    )

    # Tvinger RStudio til å vise connection til databasen og liste opp skjema/tabeller.
    try({
      cl <- match.call()
      odbc:::on_connection_opened(con, paste(deparse(cl), collapse = "\n"))
    }, silent = TRUE)

    return(con)

    # Tilgangsstyrte databaser
  } else {

    con <- odbc::dbConnect(
      odbc::odbc(),
      dsn      = database,
      uid      = uid,
      pwd      = rstudioapi::askForPassword(paste("Skriv inn passord for database ", database, " og bruker ", uid))
    )

    # Tvinger RStudio til å vise connection til databasen og liste opp skjema/tabeller.
    try({
      cl <- match.call()
      odbc:::on_connection_opened(con, paste(deparse(cl), collapse = "\n"))
    }, silent = TRUE)

    return(con)
  }
}
