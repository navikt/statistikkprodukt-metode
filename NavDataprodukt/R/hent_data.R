#' Fetch Data from DVH
#'
#' Executes a query against the established database connection.
#'
#' @param connection A DBIConnection object created by `db_connect()`.
#' @param query Character string containing the SQL query.
#'
#' @return A data frame.
#' @export
#'
#' @examples
#' \dontrun{
#' # State the query to collect data from database connection
#' sporring = "SELECT * FROM schema.table"
#' data <- hent_data(connection = con, query = sporring)
#' }
#'
#'
#' @importFrom DBI dbGetQuery

hent_data <- function(connection, query = "") {

  if (!requireNamespace("DBI", quietly = TRUE)) {
    stop(
      "Pakken 'DBI' er nødvendig for at denne funksjonen skal kjøre. ",
      "Vennligst installer pakken med å kjøre: install.packages('DBI')",
      call. = FALSE
    )
  }
  
  if (!DBI::dbIsValid(connection)) {
    stop("Feil i databaseconnection.")
  }

  if (missing(query) || !nzchar(trimws(query))) {
    stop("Du må oppgi en gyldig SQL-spørring i 'query'.", call. = FALSE)
  }

  data <- DBI::dbGetQuery(connection, query)

  # Sett locale to handle Norwegian special characters
  Sys.setlocale("LC_ALL", "nb-NO.UTF-8")
  Sys.setenv(LANG = "nb-NO.UTF-8")
  
  return(data)
}
