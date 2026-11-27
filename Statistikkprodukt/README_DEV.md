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
      app/
        __init__.py            # Tom pakkefil
        models.py              # Metadata, overføring, lasteresultat og kjørelogg
        konfig.py              # Kontrollerer konfigurasjon
        last.py                # Leser, prikker og lagrer aggregater
        skjema.py              # Viser DDL eller oppretter aggregattabeller
        installer_r_pakker.py  # Installerer nødvendige R-pakker
        testenv.py             # Administrasjon av Oracle-testmiljø
      cli.py                   # CLI-konfigurasjon og argumentbehandling
      version.py               # Python-/R-versjoner og oppdateringskrav
      config/
        aggregate_schema.py    # Validering av småaggregater
        app_schema.py          # Validering av hovedkonfigurasjonen
        loader.py              # Innlasting av YAML-konfigurasjon
        register.py            # StatistikkproduktRegister og lokal registerkontroll
      csv.py                   # CSV-lesing og -skriving
      oracle/                  # Oracle-tilkobling og metadata
        aggregate_table.py     # Aggregatmetadata og Oracle-kommentarer
        meta_table.py          # Metadatatabeller i Oracle
        log_table.py           # Kjøreloggtabellen i Oracle
        operations.py          # Operasjoner på Oracle-databasen
      prikking.py              # Kall til R
  test/                        # Pytest-tester
```

## Commit-hash i kjøreloggen

`commit_hash` i `ost_logg` inneholder først commit-hashen fra Git i lokal checkout. Hvis Git ikke er tilgjengelig, brukes `STATISTIKKPRODUKT_COMMIT_SHA`, og deretter commit-hashen fra VCS-installasjonens `direct_url.json`. For containerbygg uten `.git`, bruk `docker build --build-arg COMMIT_SHA="$(git rev-parse HEAD)" -f Statistikkprodukt/Dockerfile .`. Byggargumentet settes som `STATISTIKKPRODUKT_COMMIT_SHA` i bildet. Hvis ingen commit-hash er tilgjengelig, lagres `NULL`.

Eksisterende metadatatabeller og loggtabeller oppgraderes ikke automatisk. Endring fra tidligere VARCHAR2-kolonner til LOB/JSON og fra per-subject-logg til `ost_logg` krever en eksplisitt migrering før lasting.

## Versjonering

`project.version` i repositoryets `pyproject.toml` er eneste kilde for Python-pakkens versjon og bumpes manuelt.

Bruk `tool.statistikkprodukt.minimum_version` for versjonen som faktisk må være installert. Sett den bare når en eldre pakke ikke trygt kan kjøre.  `tool.statistikkprodukt.update_message` er valgfri og skal forklare hvorfor oppdateringen er nødvendig. Fjern `tool.statistikkprodukt.update_message` når den ikke lenger er relevant.

R-versjonskravet ligger i `tool.statistikkprodukt.r_package_requirement` i `pyproject.toml` og skal oppdateres sammen med en inkompatibel endring i `NavDataprodukt/DESCRIPTION`. Byggeoppsettet kopierer kravet inn i Python- pakken, slik at det også er tilgjengelig etter installasjon med `uv tool`.  R-pakken installeres fortsatt fra grenen gitt av `GITHUB_BRANCH`, ikke fra en tag. Ved endring av arbeidergren må `GITHUB_BRANCH` oppdateres, og den skal settes til `main` når endringen er slått sammen.

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

## Endringer i R pakken

Til vanlig blir prikking R pakken installert fra den nyeste versjonen på GitHub. For å utvikle og bruke lokale endringer,
må man installere R pakken fra kildekoden i `NavDataprodukt/`:
    
```sh
uv run statistikkprodukt install_r_package --dev-lokal-r-pakke