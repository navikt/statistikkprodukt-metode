# Plan: CLI for prikking av aggregater

## Goal

Create a Python CLI to perform an ETL task that reads table contents, runs an R script (using rpy2) to perform Gaussian suppression (masks small numbers, also referred to as "prikking" in Norwegian), and writes the result back to the database.

The CLI is intended to be used both manually on Linux (a sandboxed development environment called KNAST) and as part of a DAG in a production pipeline. In production, it shall run as a self-contained Docker container. Configuration is provided as a YAML file. Relative paths, including paths to SQL files, are resolved from the directory containing the configuration file.

Important domain terms are "bredde aggregater" (lots of variables) and "smale aggregater" (few variables). "Bredde aggregater" are the source from which a number of "smale aggregater" are generated. Those are masked to avoid reidentification through small numbers.

Use oracledb, ppy2 and pandas. All orchestration and interaction with the database happens in Python. R only does masking. Mock out the R invocation for now; the real implementation will be added later.

## Implementation decisions

1. **Program and package naming:** The Python distribution, import package, and executable are all named `statistikkprodukt`.
2. **Configuration location:** `--config` accepts a YAML file containing the database connection, `mapper` glob patterns for finding aggregate files, and an `aggregater` list selecting aggregate names. Each selected YAML file contains one aggregate under `definisjon`; derive `tabell` from the YAML filename and derive `sporring_sql` by replacing its suffix with `.sql`. Resolve glob patterns and SQL paths from the configuration file's directory.
3. **Oracle connection profiles:** Allowed values for `connection.database` are `DWHP`, `DWHQ`, `DWHU`, and `DWHR`. Use placeholder mappings for host, port, and service name for now.
4. **Secret references:** Support the URI forms shown in the configuration example. Google Secret Manager payloads are UTF-8 strings and may contain a flat JSON object. Allow `terminal://` only when stdin is a TTY. Fail if a referenced value is missing.
5. **SQL contract:** Pass SQL files to Oracle without pre-validating that they contain a `SELECT` statement or have no bind parameters. Do not prevalidate result columns or target column types; Oracle reports incompatibilities when inserting.
6. **Database writes:** `tabell` is the base name; write the masked data to `{tabell}_prepub` and the debug data to `{tabell}_kontroll`. Schema creation creates both tables with table and column comments. Use the configured `kolonne` values as output column names. Do not create uniqueness constraints. Write each "smalt aggregat" in one transaction. If the result has no rows, do not change data rows; synchronize column comments for database output.
7. **Incremental modes:** The modes operate on the configured period column:
   - `overskriv_ikke` appends periods that do not already exist and logs a warning for periods that already exist.
   - `overskriv_alt` replaces all periods that are returned by the query. Other periods in the target are not removed.
   - `overskriv_siste_periode` replaces only the maximum period present in the result.
8. **Period and variable types:** The period column and all dimension-variable columns must be strings in both query results and created tables. Periods are therefore sorted as strings.
9. **Schema compatibility:** Do not inspect the target schema before writing. Let Oracle report incompatible or missing target columns when inserting. Never create, alter, remove, or narrow columns.
10. **Metadata:** Tags may appear in any order and values may span lines until the next tag. Escaping is not supported. Duplicate tags are invalid. Metadata is not required in local CSV mode.
11. **Local CSV mode:** `--kilde` selects `db` or `csv` input and defaults to `db`; `--maal` selects `db` or `csv` output and defaults to `db`. CSV files are named `{tabell}.csv` for input; output files are `{tabell}_prepub.csv` (masked data) and `{tabell}_kontroll.csv` (debug data) and are distinguished by their input and output directories. `--kilde-mappe` and `--maal-mappe` select those directories and default to `data_inn` and `data_ut`. OBT source data are queried only through the SQL query; `obt-tabell` identifies the source table for database metadata. Existing output files are overwritten. `--dry-run` performs no writes and prints what would have happened.
12. **R boundary:** `prikking` calls the R script and returns two dataframes, one masked and one with added metadata for debugging.
13. **Language and errors:** CLI help and output, configuration validation, logs, documentation, and other user-facing errors must be in Norwegian. Exit with `0` for success, `2` for CLI or configuration errors, and `1` for operational failures.
14. **Container:** Use an Ubuntu base image and install Python and R. Defer a Chainguard-based image.
15. **Integration tests:** Tests may require Docker and use a fixed host port. CI will not run these tests in parallel.
16. **Sandboxed test environment:** Copilot runs commands in a sandbox and cannot start the Oracle container itself. Provide `testenv setup` and `testenv teardown` commands that the user runs outside the sandbox. Provide a `testenv pytest -- PYTEST_ARGS` command for the agent; it sets `STATISTIKKPRODUKT_TESTENV_EXTERNAL=1` and forwards every argument after `--` to pytest. `test/conftest.py` detects this variable and connects to the externally managed container instead of creating an `OracleDbContainer`.

## Workflow

Outline of the program's tasks:

1. Read the configuration and validate it with Pydantic.
2. Check whether the database is reachable unless the relevant input and output are local or this is a dry run.
3. For each "smalt aggregat":
   1. For database writes, fetch available source and target periods and select
      periods according to the incremental mode before reading rows. Filter the
      SQL query using bound period values (or filter CSV input in Python).
      `None` means all periods, as used for CSV output and dry runs; an empty
      period list means no rows.
   2. Run SQL to retrieve the selected dataset as pandas dataframe.
   3. Run the R script, pass a dataframe, and receive a dataframe.
   4. Store the resulting dataframe in the selected database or CSV destination.

## User experience

Keep in mind that the audience is not technical. Don't expose exceptions, unless --log-level=DEBUG is specified. Show informative error messages, utilize pydantic. No unknown keys. All in Norwegian.

Do not change the database schema. If an insert is incompatible with the existing schema, let Oracle fail the insert and roll back the transaction.

## Configuration

The shared configuration file contains connection settings and patterns for finding aggregate definitions:
```yaml
connection:
  # Select either a predefined profile ...
  database: DWHP
  # ... or specify all connection values directly instead:
  # host: oracle.example.no
  # port: 1521
  # service: myservice
  schema: my_schema # Set this as session default when connecting
  # Values may be literal strings or references using one of these forms:
  # user: my_username
  # user: env://MY_USER_ENV_NAME
  # user: terminal://
  # user: gsm://projects/my_project/secrets/my_secret/versions/latest
  # A final path segment extracts a key from a flat JSON object:
  user: gsm://projects/my_project/secrets/my_secret/versions/latest/username
  # Passwords must use a reference, not a literal value.
  password: gsm://projects/my_project/secrets/my_secret/versions/latest/password
  
  timeout: 30 # optional, default

mapper:
  - aggregater/**
aggregater:
  - ost_befolkning_alder
  - ost_befolkning_kjonn
```

Each selected `.yml` or `.yaml` file contains exactly one aggregate definition, named after its target table. The names listed in `aggregater` must be globally unique:
```yaml
definisjon:
  navn: Befolkning etter alder
  navn-en: Population by age
  innhold: Befolkning fordelt etter periode og alder.
  innhold-en: Population distributed by period and age.
  id: 21000
  obt-tabell: obt_befolkning
  periodisering:
    navn: Period
    navn-en: Period
    kolonne: periode
    innhold: Månedsperiode
    innhold-en: Month period
  inkrementell-strategi: overskriv_alt
  tellepunkter:
    - kolonne: antall
  formula: "~ periode * alder"
  prikking: true
  saerskilt: false
  variabler:
    - kolonne: alder
```

`obt-tabell` names the OBT source table whose table and column comments supply missing metadata. It may be schema-qualified, for example `OBT.BEFOLKNING_ALDER`; an unqualified name uses the configured connection schema. `navn`, `navn-en`, `innhold` and `innhold-en` are optional. If they are not specified, read the database comments (`dba_tab_comments` and `dba_col_comments`) for `obt-tabell` and parse out tags. For example, a comment could be `#NAVN Min variabel #NAVN_EN My variable #INNHOLD Min innhold #INNHOLD_EN English description`. Fail if a field is missing in both the YAML file and the database. Use the logging library, log (INFO) which source is taken. If --verbose is specified, log the values as well.

## CLI

Example usage of the API. Use the argparse library. Keep the help text informative and in Norwegian. Keep in mind that the users are not technical.

```shell
# Laste ned den nyeste versjon fra github og gjør den tilgjengelig som et program
uv tool install statistikkprodukt@https://github.com/navikt/NavDataprodukt.git

# Vis hjelpetekst
statistikkprodukt --help
statistikkprodukt last --help

# Kjør prikking for hele konfigurasjon i konfigurasjon.yml
# Kan også specifieseres med env STATISTIKKPRODUKT_AGGREGAT_CONFIG=...
statistikkprodukt last --config=konfigurasjon.yml
export STATISTIKKPRODUKT_AGGREGAT_CONFIG=konfigurasjon.yml

# Bare vis hva som ville skjedd
statistikkprodukt last --dry-run

# kontroller logging
statistikkprodukt last --log-level=WARN
statistikkprodukt last --verbose

# Skriv til CSV i stedet for database (for eksempel til data_ut/ost_tabellnavn_beskrivende.csv)
statistikkprodukt last --maal=csv

# Les fra CSV i stedet for database. Filnavnet følger verdien i `tabell`,
# for eksempel `tabell: ost_tabellnavn_beskrivende`
# -> data_inn/ost_tabellnavn_beskrivende.csv
statistikkprodukt last --kilde=csv

# Lokal innlesing og utskriving kan kombineres, valgfritt med mappe (vist er default)
statistikkprodukt last \
  --kilde=csv --maal=csv \
  --kilde-mappe=data_inn --maal-mappe=data_ut

# Ikke les hemmeligheter, hvis ikke det er behov for det

# Bare kjør noen småaggregater
statistikkprodukt last ost_tabellnavn_beskrivende ost_en_annen_tabellnavn_beskrivende

# Kjør disse utenfor Copilot-sandkassen for å starte og stoppe Oracle-testmiljøet
statistikkprodukt testenv setup
statistikkprodukt testenv teardown

# Kjør pytest fra sandkassen mot det eksterne testmiljøet.
# Alle argumenter etter `--` sendes uendret til pytest.
statistikkprodukt testenv pytest -- test/test_oracle.py -k test_skriving
```

## Project layout

Project layout:
```text
pyproject.toml # specification av python prosjektet 
uv.lock # lock-fil for avhengigheter
src/
  statistikkprodukt/
    __init__.py
    cli.py # CLI argumenter
    oracle/
      operations.py # connect / write_result
      metadata.py # lese metadata (e.g. kommentarer) fra DB
      validation.py # kompatibilitet av kolonnetyper
    prikking.py # kjør R skriptet
    config/
      __init__.py # offentlig konfigurasjonsgrensesnitt
      loader.py # leser og validerer YAML-konfigurasjon
      app_schema.py # skjema for hovedkonfigurasjonen
      aggregate_schema.py # Aggregate og IncrementalMode
    lokal.py # lesing/skriving av csv filer
    testenv.py # administrasjon av eksternt Oracle-testmiljø
    app.py # syr sammen applikasjonen
test/
  conftest.py # start testmiljø før testene starter
  # pytest tester her
R/
  dvh_prikking.R # entrypoint when python calls R
```

## R integration

The application calls `prikking`, which returns a marked dataframe before starting R. `_run_r_prikking` prepares the disabled R invocation and sources the script directly:
```python
# Initialize R bridge
pandas2ri.activate()
robjects.r["source"](r_script_path)
r_prikking = robjects.globalenv["dvh_prikking"]
    
all_results = []

for sub in df[periode].unique():
    sub_df = df[df[periode] == sub]

    r_result = r_prikking(
        data = sub_df, 
        formula = config["formula"], 
        tallKol = config['aggregater']['tellepunkter'][0]['kolonne'],
        prikking = config["prikking"], 
        saerskilt = config["saerskilt"]
    )

    # The R function returns both the aggregate and quality-check data frames.
    df_result = pandas2ri.rpy2py(r_result.rx2("smalt_aggregat"))

    # TODO: Clarify whether run information should be stored separately.
    # TODO: Clarify how to store metadata in the database.
    # df_result["run_target"] = config["tabell"]
    # df_result["applied_formula"] = config["formula"]

    all_results.append(df_result)

# 5. Union all the outputs together into one final DataFrame
final_df = pd.concat(all_results, ignore_index=True)
```

## Testing

Write pytests using Oracle testcontainers. The default behavior in `test/conftest.py` is to create and manage an `OracleDbContainer` for the test session:
```python
from testcontainers.core import testcontainers_config
from testcontainers.oracle import OracleDbContainer

oracle = OracleDbContainer(oracle_password="testpassword")
oracle.with_env("TZ", "Europe/Oslo")
oracle.with_env("ORA_SDTZ", "Europe/Oslo")

# testcontainer removed the option to specify the hostname, uses docker-engine default
# we want a fixed container such that IDE/CLI tools can connect to the db
oracle.with_bind_ports(1521, connection_config.port)

# fixed name for reuse and error messages should a container be running from before
oracle.with_name(CONTAINER_NAME)
```

### External test environment for sandboxed agents

The Oracle container must be started by the user outside the Copilot sandbox before the agent runs integration tests:

```shell
statistikkprodukt testenv setup
```

`testenv setup` must:

1. Use the Docker Python SDK (`docker.from_env()`) to verify that the Docker daemon is reachable.
2. Use a fixed container name, host port, credentials, timezone, and Oracle image shared with `test/conftest.py`.
3. Label containers managed by this command so teardown never removes an unrelated container with the same name.
4. If the correctly labelled container is already running, report that clearly and succeed.
5. If the correctly labelled container exists but is stopped, start it.
6. Fail with an informative Norwegian error if Docker is unavailable, the fixed port is occupied, or the name belongs to an unmanaged or incompatible container.
7. Wait until Oracle is ready to accept connections before succeeding. The container must remain running after the command exits.

The user removes the external environment with:

```shell
statistikkprodukt testenv teardown
```

`testenv teardown` must use the Docker SDK, stop and remove only the correctly labelled managed container, and succeed with an informative message if that container does not exist. It must fail rather than modify an unmanaged container with the same name.

The agent runs tests through:

```shell
statistikkprodukt testenv pytest -- PYTEST_ARGS
```

This command must:

1. Set `STATISTIKKPRODUKT_TESTENV_EXTERNAL=1` only for the pytest subprocess.
2. Forward all arguments after `--` unchanged to pytest and return pytest's exit code.
3. Check that Oracle is reachable at the configured fixed host and port before starting pytest, without using the Docker Python SDK to support running in an agent sandbox. If Oracle is unavailable, fail with a Norwegian message instructing the user to run `statistikkprodukt testenv setup` outside the sandbox.

When `test/conftest.py` sees `STATISTIKKPRODUKT_TESTENV_EXTERNAL=1`, it must not instantiate or start `OracleDbContainer`. It must instead use the fixed external connection settings and fail clearly if the database cannot be reached. Without the variable, tests retain the normal self-managed `OracleDbContainer` behavior for local development and CI.

Document this workflow in `.github/copilot-instructions.md` and `README_DEV.md`. Agents running inside the sandbox must use `statistikkprodukt testenv pytest -- ...`, never plain pytest for integration tests.

## Container and dependency updates

Write a Dockerfile using an Ubuntu base image with Python and R installed. A Chainguard-based image is deferred.

Set up dependabot, for github actions and uv. Create separate groups for security and version updates. Use a 14 day dependency cooldown.

## Documentation and example

Write three sets of documentation.
- .github/copilot-instructions.md
- a README.md for the non-technical audience
- a README_DEV.md for developers.

Create an `eksempel` folder containing `inputs/ost_befolkning_kjonn.csv`, `inputs/ost_befolkning_alder.csv`, `outputs/ost_befolkning_kjonn.csv`, `outputs/ost_befolkning_alder.csv`, a configuration file for the transformations, SQL files that refer to the OBT source tables, and a README explaining how to run the example locally.

TODO: