# Instruksjoner for Copilot

Dette prosjektet leverer Python-CLI-en `statistikkprodukt`; kommando- og feilmeldinger skal være på norsk. Følg `Statistikkprodukt/src/statistikkprodukt/PLAN.md` for domenekrav og arkitektur.

Python biblioteket er i `Statistikkprodukt/`. `pyproject.toml` er i repository root mappen, et nivå over `Statistikkprodukt/`.

Kjør enhetstester sånn:
```sh
uv run --frozen statistikkprodukt testenv pytest -- -m "not r_dependent"
```

Med argumenter:
```sh
uv run --frozen statistikkprodukt testenv pytest -- -m "not r_dependent" -q
```

Tester som krever R er merket `r_dependent`; hopp over dem i vanlige testkjøringer.

Oracle-testcontaineren må startes og stoppes utenfor sandkassen med `statistikkprodukt testenv setup` og `statistikkprodukt testenv teardown`. Ikke start Docker-containere direkte fra sandkassen.

SQL-filer leses relativt til YAML-konfigurasjonen. Ikke legg hemmeligheter i konfigurasjonsfiler, og ikke la standardkjøring endre databaseskjemaet.
