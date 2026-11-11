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
      app.py                   # Implementering av CLI modulene 
      cli.py                   # CLI konfigurasjon og parsing av argumenter
      config/
        aggregate_schema.py    # Validering av konfigurasjonsfil til små-aggregater
        app_schema.py          # Validering av konfigurasjonsfil til applikasjon
        loader.py              # Innlasting av YAML-konfigurasjon
      lokal.py                 # CSV-lesing og -skriving
      oracle/                  # Oracle-tilkobling og metadata
        metadata.py            # Lesing og skriving av kolonnekommentarer og tags
        operations.py          # Operasjoner på Oracle-databasen
      prikking.py              # Kall til R
      testenv.py               # Administrasjon av Oracle-testmiljø
  test/                        # Pytest-tester
```

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
