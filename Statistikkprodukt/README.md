# Statistikkprodukt

`statistikkprodukt` er et verktøy for datateam for å lage statistikkaggregater med Gaussian Suppression prikking/maskering av små verdier for personvernhensyn.

Verktøyet konfigureres med yaml filer og kan håndtere flere aggregater samtidig.

For hver aggregat::
- Lese data fra Oracle/CSV inkrementell
- Kjøre Gaussian Suppression algoritme implementert i R for hver 
- Lagre resultatet i Oracle/CSV
- Les metadata fra en bredt aggregat tabell (obt), oppdater fra konfigurasjonsfilen, skriv som databasekommentarer-

For hver aggregat lages to resultattabeller: `{tabell}_prepub` med de prikkede dataene, og `{tabell}_kontroll` med kontrolldata for feilsøking.

Denne filen er brukerdokumentasjon. [README_DEV.md](README_DEV.md) er utviklerhåndbok.

## Kom i gang

### Lokal / KNAST
Last ned repositoriet og installer python avhengigheter:
```sh
uv sync
uv run statistikkprodukt --help
```

KNAST har allerede R installert. For å installere eller oppdatere R avhengighetene for prikking, kjør:
```sh
uv run statistikkprodukt installer-r-pakker
```

Kjør eksempelet, som leser to CSV-filer fra `eksempel/data_inn` og skriver resultatene til `eksempel/data_ut`, uten Oracle-tilkobling eller hemmeligheter:

```sh
cd Statistikkprodukt/eksempel
uv run statistikkprodukt last \
  --config konfigurasjon.yml \
  --kilde=csv \
  --maal=csv
```

Konfigurasjonsmuligheter:
- `--dry-run` viser planlagte endringer uten å modifisere data.
- `--kilde`/`--maal` er enten `db` (default) eller `csv`
- `--kilde-mappe`/`--maal-mappe` angir mappene for CSV-filer (default: `data_inn` og `data_ut`)

Det betyr man kan:
- Skrive fra Oracle til Oracle, normal ETL.
- Skrive fra CSV-filer til CSV-filer, uten Oracle-tilkobling, bra for testing.
- Skrive fra Oracle til CSV-filer, bra for testing.
- Skrive fra CSV-filer til Oracle, for eventuell manuell overstyring.

Som standard skrives alle små aggregater. Enkelte kan utvelges med navn:
```shell
uv run statistikkprodukt last --config konfigurasjon.yml ost_befolkning_alder ost_befolkning_kjonn
```

### Mot databasen

Kjør `eksempel/data_inn/obt_befolkning.sql` i Oracle for å opprette en test obt tabell.

Modifiser `eksempel/konfigurasjon.yml` med riktig databaseprofil:
```yaml
connection:
  host: oracle.example.no
  port: 1521
  service: my_service
  user: my_user[my_schema]
  password: terminal://
```

Opprett småaggregat-tabellene i Oracle med følgende kommando:
- ost_befolkning_alder_prepub
- ost_befolkning_alder_kontroll
- ost_befolkning_kjonn_prepub
- ost_befolkning_kjonn_kontroll

```shell
cd Statistikkprodukt/eksempel
statistikkprodukt skjema opprett --config konfigurasjon.yml
```

Kjør lasten fra databasen til csv:
```shell
uv run statistikkprodukt last --config konfigurasjon.yml --maal=csv
```

CSV filene i `data_ut` overskrives, men de skal ikke endre seg, siden eksempelet er ekvivalent med lasten fra csv til csv.
Du kan slette de for å verifisere at data kommer inn det.

Kjør lasten fra databasen til databasen:
```shell
uv run statistikkprodukt last --config konfigurasjon.yml
```

Sjekk resultatene i Oracle:
```sql
select * from ost_befolkning_alder_kontroll;
-- ... og andre ost tabeller
```

# System dokumentasjon
## Databaseskjema 
Databasetabeller kan opprettes som følger:

```sh
# For alle aggregater i konfigurasjonen
uv run statistikkprodukt skjema opprett --config konfigurasjon.yml

# For enkelte aggregater
uv run statistikkprodukt skjema opprett --config konfigurasjon.yml ost_2 ost_1
```

Det opprettes to tabeller per aggregat.
```sql
create table ost_1_kontroll (
  -- Fra SQL spørringen:
  periode_kolonne varchar2(10),
  variabel1_kolonne varchar2(10),
  variabel2_kolonne varchar2(10),
  -- tall som ikke er prikket
  tall_kolonne number,
    
  -- Lagd til:
  -- primærprikking (små tall) 1/0 for ja/nei
  primary number, 
  -- sekundærprikking via gaussian suppression 1/0 for ja/nei
  suppressed number
);

create table ost_1_prepub (
  -- Fra SQL spørringen:
  periode_kolonne varchar2(10),
  variabel_kolonne varchar2(10),
  -- nan når det er prikket
  tall_kolonne number
);
```

## Konfigurasjon

### Hovedkonfigurasjonsfil
Konfigurasjonen er delt i én felles fil og én YAML-fil per småaggregat. Den felles filen inneholder databasetilkobling, søkemønstre for aggregatfilene og navnene på aggregatene som skal kjøres. Lag ulike filer for ulike miljøer

```yaml
connection:
  # Velg enten en profil ...
  database: DWHP
  # ... eller oppgi alle verdiene direkte i stedet:
  # host: oracle.example.no
  # port: 1521
  # service: ORCL
  schema: mitt_skjemanavn
  # Bruker kan oppgis direkte eller hentes fra en støttet hemmelighetskilde.
  user: gsm://projects/my_project/secrets/my_secret/versions/latest/username
  password: gsm://projects/my_project/secrets/my_secret/versions/latest/password
  timeout: 30

# Hvor i repositoriet aggregatkonfigurasjonen finnes
mapper:
  - aggregater/**

# Hvilke aggregater som skal kjøres. Navnene må samsvare med YAML-filene i `mapper`.
aggregater:
  - ost_befolkning_alder
  - ost_befolkning_kjonn
```

Mer om lasting av hemmeligheter følger.

## Konfigurasjon per småaggregat

Per småaggregat må det lages:
- `{aggregatnavn}.yml` som konfigurasjon
- `{aggregatnavn}.sql` som SQL-spørring for å hente data fra OBT-tabellen.


Eksempel på aggregatfil:

```yaml
definisjon:
  # Globalt unik id for aggregatet, få den tildelt fra Statistikk og Metode
  # Format: 21000–22999
  id: 21000
  obt-tabell: befolkning
  
  # Metadata per småaggregat, påkrevd  
  navn: "Kort navn på norsk"
  navn-en: "Short name in English"
  innhold: "Beskrivelse av innhold på norsk"
  innhold-en: "Description of content in English"
  
  # Konfigurasjon av aggregat
  periodisering:
    kolonne: periode
    # Følgende metadata kan hentes fra kolonnekommentarer i OBT-tabellen,
    # men kan også overskrives her. Det samme gjelder for tellepunkter og variabler.
    navn: Periode
    navn-en: Period
    innhold: Månedsperiode
    innhold-en: Month period
  
  tellepunkter:
    - kolonne: antall
      navn: Antall personer
      navn-en: Number of people
      innhold: Antall personer i gruppen.
      innhold-en: Number of people in the group.
    
  variabler:
    - kolonne: alder
      navn: Alder
      navn-en: Age
      innhold: Alder på personene i gruppen.
      innhold-en: Age of the people in the group.
    
  # Konfigurasjon av last
  inkrementell-strategi: overskriv_alt  # overskriv_ikke, overskriv_alt, overskriv_siste_periode
  
  # Konfigurasjon av prikking
  formula: "~ periode * alder"
  prikking: true
  saerskilt: false
```

`obt-tabell` er OBT-tabellen som er datagrunnlaget og leverer metadata i form av kolonne kommentarer.

Metadata på `periodisering`, variabler og `tellepunkter` kommer fra kolonnekommentarer med tags, med mindre man overskriver dem i konfigurasjonsfilen. Tags i kolonnekommentarer er som følger:

- `#NAVN` for norsk navn
- `#NAVN_EN` for engelsk navn
- `#INNHOLD` for norsk beskrivelse
- `#INNHOLD_EN` for engelsk beskrivelse

NB: Både periode og variabler må være streng.

### Løpenummerlisten

Før `last` og `skjema` kontrolleres hvert aggregat mot `statistikkprodukt_register.txt` i rotmappa i repoet (eller filen i miljøvariabelen `STATISTIKKPRODUKT_LOPENUMMER`). Filen har kolonnene `Løpenr`, `Team` og `Statistikkprodukt`, skilt med tabulator, semikolon, komma eller `|`. Kontrollen krever at `id` fra YAML-filen finnes som løpenummer, og at tabellnavnet til statistikkproduktet i listen er det samme som aggregatets tabellnavn (filnavnet til YAML-filen). Løpenummer kan stå med eller uten `NAV`-prefiks, og store og små bokstaver i tabellnavnet spiller ingen rolle.

### Inkrementelle laster

Følgende inkrementelle strategier er støttet. Inkrementelle laster baserer seg på periodekolonnen.

#### `overskriv_ikke`

Laster kun nye perioder:
```text
# I kilden
202501
202502
202503

# I målet
202502

# Lastet
202501
202503
```

#### `overskriv_alt`

Overskrive alle perioder som finnes i kilden. Perioder som ikke finnes i kilden, men som finnes i målet, beholdes.

```text
# I kilden
202501
202502
202503

# I målet
202412
202502

# Lastet
202501
202501
202503

# Perioden 202412 beholdes.
```

#### `overskriv_siste_periode`

Som `overskriv_ikke`, men den nyeste perioden i målet overskrives. 

```text
# I kilden
202412
202501
202502
202503

# I målet
202501
202502

# Lastet
202412
202502
202503
```

### Hemmeligheter

Hemmeligheter kan lastes fra Google Secret Manager (GSM), miljøvariabler eller terminalen (lokal utvikling).

NB at passordet _må_ være en hemmelighetsreferanse, der er ikke mulig å legge inn passord i klartekst.

#### GSM

```yaml
  user: gsm://projects/my_project/secrets/my_secret/versions/latest/username
  password: gsm://projects/my_project/secrets/my_secret/versions/latest/password
```
Leser fra hemmeligheten `my_secret` i prosjektet `my_project`. Det forventes at den inneholder en JSON-string, og den pakkes ut med nøklene `username` og `password`. De kan velges fritt. 

Nøklene er valgfritt, man kan også bruke hele hemmeligheten som en enkel verdi:
```yaml
  user: my_user
  password: gsm://projects/my_project/secrets/my_secret/versions/latest
```

#### Miljøvariabler

```yaml
  user: env://DB_USER
  password: env://DB_PASSWORD
```

#### Lokal utvikling

Lag deg en egen fil til lokal utvikling, ikke sjekk den inn i versjonskontroll.

```yaml
  user: my_ident
  password: terminal://
```

Da leses hemmeligheten fra terminalen.

Vi jobber på å få integrasjon med KNAST sin `dvh` kommandoen for å slippe å taste passord ved hver kjøring.


# Bruk av KI

Dette prosjektet bruker generativ KI for å generere kode.
