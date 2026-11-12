# Utviklerhåndbok

## Prosjektlayout

```text
pyproject.toml                 # Python-prosjekt og avhengigheter
uv.lock                        # Låste Python-avhengigheter
NavDataprodukt/                # R-pakken som inneholder prikkingsfunksjonene
  DESCRIPTION
  R/
Statistikkprodukt/
  Dockerfile                   # Bygges med repository-roten som build context
  README.md
  README_DEV.md
  eksempel/                    # Eksempelkonfigurasjon og data
  src/
    statistikkprodukt/
      __init__.py
      app.py                   # Applikasjonsflyt for lasting og skjemahåndtering
      app_models.py            # Metadata, lasteresultat og kjørelogg
      cli.py                   # CLI-konfigurasjon og argumentbehandling
      config/
        aggregate_schema.py    # Validering av småaggregater
        app_schema.py          # Validering av hovedkonfigurasjonen
        loader.py              # Innlasting av YAML-konfigurasjon
        register.py            # StatistikkproduktRegister og lokal registerkontroll
      lokal.py                 # CSV-lesing og -skriving
      oracle/                  # Oracle-tilkobling og metadata
        aggregate_table.py     # Aggregatmetadata og Oracle-kommentarer
        meta_table.py          # Metadatatabeller i Oracle
        logg_table.py          # Kjøreloggtabellen i Oracle
        operations.py          # Operasjoner på Oracle-databasen
      prikking.py              # Kall til R
      testenv.py               # Administrasjon av Oracle-testmiljø
  test/                        # Pytest-tester
```

## Commit-hash i kjøreloggen

`commit_hash` i `ost_logg` inneholder først commit-hashen fra Git i lokal checkout. Hvis Git ikke er tilgjengelig, brukes `STATISTIKKPRODUKT_COMMIT_SHA`, og deretter commit-hashen fra VCS-installasjonens `direct_url.json`. For containerbygg uten `.git`, bruk `docker build --build-arg COMMIT_SHA="$(git rev-parse HEAD)" -f Statistikkprodukt/Dockerfile .`. Byggargumentet settes som `STATISTIKKPRODUKT_COMMIT_SHA` i bildet. Hvis ingen commit-hash er tilgjengelig, lagres `NULL`.

Eksisterende metadatatabeller og loggtabeller oppgraderes ikke automatisk. Endring fra tidligere VARCHAR2-kolonner til LOB/JSON og fra per-subject-logg til `ost_logg` krever en eksplisitt migrering før lasting.

## Utviklingsmiljø

I tillegg til det som skal installeres i brukerdokuentasjon trenger man docker, colima eller podman for å kunne kjøre integrasjonstester med Oracle. 

## Integrasjonstester med Oracle

Testene starter er en Oracle-testcontainer som kjører i bakgrunnen. Det skjer automatisk som del av testmiljø oppsett, men det kan også gjøres manuelt. Det kan være en fordel når man kjører en agent i en sandbox, som ikke skal ha tilgang til docker styring.

```sh
uv run statistikkprodukt testenv setup
uv run statistikkprodukt testenv teardown
```

Containeren blir værende mellom testkjøringer som ytelsesoptimering.

Start testene som følger:

```sh
uv run statistikkprodukt testenv pytest
# eller med argumenter til pytest, som må følge '--':
uv run statistikkprodukt testenv pytest -- -q
```
