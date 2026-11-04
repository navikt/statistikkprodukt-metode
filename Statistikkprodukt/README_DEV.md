# Utviklerhåndbok

## Prosjektlayout

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
    config.py # pydantic models for konfigurasjonsfilen
    lokal.py # lesing/skriving av csv filer
    testenv.py # administrasjon av eksternt Oracle-testmiljø
    app.py # syr sammen applikasjonen
test/
  conftest.py # start testmiljø før testene starter
  # pytest tester her
R/
  dvh_prikking.R # entrypoint when python calls R
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
