# Statistikkprodukt

`statistikkprodukt` er et verktøy for datateam for å lage statistikkaggregater med Gaussian Suppression prikking/maskering av små verdier for personvernhensyn.

Verktøyet forbereder metadata til generering av PX-filer for Statistikkbanken. På sikt skal det bli mulig å bruke verktøyet også utenfor Statistikkbank konktekst.

Verktøyet konfigureres med yaml filer og kan håndtere flere aggregater samtidig.

For hver aggregat::
- Lese data fra Oracle/CSV inkrementell
- Kjøre Gaussian Suppression algoritme implementert i R for hver aggregat
- Lagre resultatet i Oracle/CSV
- Les metadata fra en bredt aggregat tabell (obt), oppdater fra konfigurasjonsfilen, skriv som databasekommentarer

For hvert aggregat lages `{tabell}_prepub` med prikkede data, `{tabell}_kontroll` med kontrolldata og `{tabell}_meta` med metadata. Databasekjøringer logges i én felles tabell, `ost_logg`.

Denne filen er brukerdokumentasjon. [README_DEV.md](README_DEV.md) er utviklerhåndbok.

## Innhold

- [Statistikkprodukt](#statistikkprodukt)
  - [Kom i gang](#kom-i-gang)
    - [Lokal / KNAST](#lokal--knast)
    - [Sjekk konfigurasjonen](#sjekk-konfigurasjonen)
    - [Kjør mot databasen](#kjør-mot-databasen)
- [System dokumentasjon](#system-dokumentasjon)
  - [Databaseskjema](#databaseskjema)
  - [Konfigurasjon](#konfigurasjon)
    - [Hovedkonfigurasjonsfil](#hovedkonfigurasjonsfil)
  - [Konfigurasjon per småaggregat](#konfigurasjon-per-småaggregat)
    - [Statistikkproduktregisteret](#statistikkproduktregisteret)
    - [Inkrementelle laster](#inkrementelle-laster)
      - [`legg_til_nye_perioder`](#legg_til_nye_perioder)
      - [`overskriv_eksisterende_perioder`](#overskriv_eksisterende_perioder)
    - [Hemmeligheter](#hemmeligheter)
      - [GSM](#gsm)
      - [Miljøvariabler](#miljøvariabler)
      - [Lokal utvikling](#lokal-utvikling)
- [Bruk av KI](#bruk-av-ki)

## Kom i gang

### Lokal / KNAST

Er du på KNAST må du først autentisere deg for å kunne nedlaste python pakker:
```shell
gcloud auth login
pypi-auth
```

Installer `statistikkprodukt` med uv:
```sh
# legg til @branch eller @tag for å velge versjon
uv tool install git+https://github.com/navikt/statistikkprodukt-metode@meta-felter
```

Har du allerede installert, oppdater til nyeste versjon med:
```sh
statistikkprodukt oppdater
```

Ved oppstart kontrollerer verktøyet om en ny versjon er tilgjengelig.

Test om installasjonen er vellykket:
```sh
statistikkprodukt --help
```

For å gå gjennom eksempelet, klon repoet. Men det er ikke nødvendig for å bruke verktøyet.
```sh
git clone https://github.com/navikt/statistikkprodukt-metode.git
```

KNAST har allerede R installert. For å installere eller oppdatere R avhengighetene for prikking, åpne brannmuren til
- packagemanager.posit.co/*
- rspm-sync.rstudio.com/bin/*
- codeload.github.com/navikt/statistikkprodukt-metode/*
- raw.githubusercontent.com/navikt/statistikkprodukt-metode/*

og kjør:
```sh
statistikkprodukt installer-r-pakker
```
På spørsmålet "Would you like to create a personal library" skal du svare "Yes", og "Y" på "Do you want to continue". Om dette feiler, sjekk brannmuråpningene, ellers ta kontakt med teamet. Om alt har godt bra, skal de siste få linjene inneholde `R-pakker installert og funksjonstest fullført.`.

Kjør eksempelet først, som ikke bruker databasen (se [kjør mot databasen](#kjør-mot-databasen)). Eksempelet leser to CSV-filer fra `tutorial` og skriver resultatene til `tutorial`:

```sh
cd Statistikkprodukt/tutorial
statistikkprodukt last \
  --konfig-fil konfigurasjon.yml \
  --ingen-id-validering \
  --kilde=csv \
  --maal=csv
```

Konfigurasjonsmuligheter:
- `--ingen-id-validering` tillater bruk av test-ID-er (00000–09999) som ikke finnes i registeret.
- `--dry-run` viser planlagte endringer uten å modifisere data.
- `--kilde`/`--maal` er enten `db` (default) eller `csv`
- `--kilde-mappe`/`--maal-mappe` angir mappene for CSV-filer (default: `data_inn` og `data_ut`)
- `--log-level=DEBUG` for å se mer detaljer om hva som skjer.
- `--period/-p` for å begrense til én periode uansett inkrementell strategi. Kan brukes flere ganger.
- `--full-relast` sletter alle eksisterende data og laster alle kildens rader på nytt.

Perioder i `--period/-p` og i CSV-/Oracle-data må være år (`2025`), måned
(`2025M01`–`2025M12`) eller kvartal (`2025K1`–`2025K4`).

Det betyr man kan:
- Skrive fra Oracle til Oracle, normal ETL.
- Skrive fra CSV-filer til CSV-filer, uten Oracle-tilkobling, bra for testing.
- Skrive fra Oracle til CSV-filer, bra for testing.
- Skrive fra CSV-filer til Oracle, for eventuell manuell overstyring.

Som standard skrives alle små aggregater. Enkelte kan utvelges med navn:
```shell
statistikkprodukt last ost_ga_befolkning_mnd_alder ost_ga_befolkning_mnd_kjonn
```

> [!WARNING]
> Legg `*.csv` til din `.gitignore` og heller bruke `git add -f min_fil.csv`, for å unngå å sjekke inn
> csv filer med produksjonsdata ved et uhell.

### Sjekk konfigurasjonen

Valider hovedkonfigurasjonen, aggregatdefinisjonene og ID-er mot statistikkproduktregisteret:

```sh
statistikkprodukt konfig sjekk --konfig-fil konfigurasjon.yml

# For eksempler med test-ID-er
statistikkprodukt konfig sjekk --konfig-fil konfigurasjon.yml --ingen-id-validering
```

`--konfig-fil` kan erstattes med miljøvariabelen `STATISTIKKPRODUKT_AGGREGAT_CONFIG`.

Gyldig konfigurasjon gir avslutningskode `0`; konfigurasjonsfeil gir `2`.

### Kjør mot databasen

Kjør `tutorial` i Oracle for å opprette en test obt tabell.

Modifiser `tutorial` med riktig databaseprofil:
```yaml
version: 1
connection:
  host: oracle.example.no
  port: 1521
  service: my_service
  user: my_user[my_schema]
  password: terminal://
```

Opprett logg, resultat- og metadatatabellene (se [Databaseskjema](#databaseskjema) for detaljer):

```shell
cd Statistikkprodukt/tutorial
statistikkprodukt skjema opprett --konfig-fil konfigurasjon.yml --ingen-id-validering --db
```

Uten `--db` vises bare DDL.

Kjør lasten fra databasen til csv:
```shell
statistikkprodukt last --konfig-fil konfigurasjon.yml --ingen-id-validering --maal=csv
```

CSV filene i `data_ut` overskrives. Du kan slette de for å verifisere at data kommer inn det.

Kjør lasten fra databasen til databasen:
```shell
statistikkprodukt last --konfig-fil konfigurasjon.yml --ingen-id-validering
```

Sjekk resultatene i Oracle:
```sql
select * from ost_ga_befolkning_mnd_alder_kontroll;
-- ... og andre ost tabeller
```

# System dokumentasjon
## Databaseskjema
Uten `--db` vises bare DDL; med `--db` opprettes manglende tabeller etter oppslag i Oracle dictionary. Vanlig `last` oppretter eller endrer ikke tabellstrukturen.

```sh
# For alle aggregater i konfigurasjonen
statistikkprodukt skjema opprett --konfig-fil konfigurasjon.yml --db

# For enkelte aggregater
statistikkprodukt skjema opprett --konfig-fil konfigurasjon.yml --db ost_2 ost_1
```

Tabellene per aggregat:
```sql
create table ost_1_kontroll (
  -- Fra SQL spørringen:
  periode_kolonne varchar2(10),
  variabel1_kolonne varchar2(10),
  variabel2_kolonne varchar2(10),
  -- Navnet på tellepunktet (kolonnen) verdien gjelder for, og verdien som ikke er prikket.
  -- Hvert tellepunkt får egne rader.
  tellepunkt varchar2(128),
  verdi number,

  -- Lagd til:
  -- primærprikking (små tall) 1/0 for ja/nei
  primary number,
  -- sekundærprikking via gaussian suppression 1/0 for ja/nei
  suppressed number,
  dvh_lastet_tid DATE DEFAULT SYSDATE NOT NULL
);

create table ost_1_prepub (
  -- Fra SQL spørringen:
  periode_kolonne varchar2(10),
  variabel_kolonne varchar2(10),
  -- En kolonne per konfigurerte tellepunkt, nan når det er prikket
  tall_kolonne number,
  tall2_kolonne number,
  dvh_lastet_tid DATE DEFAULT SYSDATE NOT NULL
);

CREATE TABLE ost_1_meta (
  type               CHAR(2) NOT NULL,
  keyword            VARCHAR2(256 CHAR) NOT NULL,
  scope              VARCHAR2(256 CHAR),
  language           CHAR(2),
  value              VARCHAR2(4000 CHAR),
  dvh_lastet_tid DATE DEFAULT SYSDATE NOT NULL
);
```

Metadatatabellen erstattes transaksjonelt ved hver databasekjøring.
`TYPE` grupperer radene som PX (PX-nøkkelord), CS (kolonneoppsett),
CR (kolonnemetadata) eller SQ (startvisning). `SCOPE` er foreløpig NULL.
`LANGUAGE` er NO/EN for oversatte felt og NULL for språkuavhengige felt.
`VALUE` kan være opptil 4000 byte i UTF-8. Kolonnesuffiksene `__no` og `__en`
støttes foreløpig ikke. Se [metafelter.md](metafelter.md) for config-feltene.

Eksisterende metadatatabeller med `felt`/`verdi` må fjernes og opprettes på nytt
via `skjema opprett --db`; vanlig lasting migrerer ikke skjemaet.

Logg tabell:

```sql


CREATE TABLE ost_logg (
  kjoring_id            VARCHAR2(36 CHAR) NOT NULL,
  starttid              TIMESTAMP NOT NULL,
  sluttid               TIMESTAMP NOT NULL,
  status                VARCHAR2(10 CHAR) NOT NULL,  -- OK, FEILET
  db_bruker             VARCHAR2(128 CHAR) DEFAULT SYS_CONTEXT('USERENV', 'SESSION_USER'),
  commit_hash           VARCHAR2(64 CHAR),
  konfigurasjon         CLOB CHECK (konfigurasjon IS JSON), -- hele yml konfigurasjonsfil som json
  kilde                 VARCHAR2(10 CHAR),
  maal                  VARCHAR2(10 CHAR),
  antall_aggregater     NUMBER,
  python_pakke_versjon  VARCHAR2(128 CHAR),
  r_pakke_versjon       VARCHAR2(128 CHAR),
  feilmelding           CLOB,
  aggregat_id           VARCHAR2(5 CHAR) NOT NULL,
  tabell                VARCHAR2(128 CHAR) NOT NULL,
  navn                  VARCHAR2(4000 CHAR),
  navn_en               VARCHAR2(4000 CHAR),
  innhold               CLOB,
  innhold_en            CLOB,
  referanse_tabell      VARCHAR2(261 CHAR),
  periodisering         VARCHAR2(128 CHAR),
  inkrementell          VARCHAR2(128 CHAR),
  formula               VARCHAR2(4000 CHAR),
  prikking              NUMBER(1),
  saerskilt             NUMBER(1),
  sporring_sql          CLOB,
  perioder              CLOB CHECK (perioder IS JSON), -- alle behandlete perioder
  antall_rader          NUMBER,
  antall_primaerprikket NUMBER,
  antall_prikket        NUMBER,
  dvh_lastet_tid        DATE DEFAULT SYSDATE NOT NULL
);
```


Eksisterende loggtabeller med `inkrementell VARCHAR2(30 CHAR)` må utvides
manuelt i riktig skjema for å romme de nye modusnavnene. Vanlig lasting og
`skjema opprett --db` endrer ikke eksisterende loggtabeller:

```sql
ALTER TABLE ost_logg MODIFY (inkrementell VARCHAR2(128 CHAR));
```

## Konfigurasjon

### Hovedkonfigurasjonsfil
Konfigurasjonen er delt i én felles fil og én YAML-fil per småaggregat. Den felles filen inneholder databasetilkobling, søkemønstre for aggregatfilene og navnene på aggregatene som skal kjøres. Lag ulike filer for ulike miljøer

```yaml
version: 1
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

# Søkemønstre for YAML- og SQL-filer, relativt til hovedkonfigurasjonen
filsok:
  - aggregater/**

# Hvilke aggregater som skal kjøres. Navnene må samsvare med YAML-filene i `filsok`.
aggregater:
  - ost_ga_befolkning_mnd_alder
  - ost_ga_befolkning_mnd_kjonn
```

Søkemønstrene i `filsok` tolkes relativt til mappen med hovedkonfigurasjonsfilen. `*` treffer alle filer i mappen, `*.sql` kun SQL-filer, `**` treffer alle undermapper rekursivt.

```text
produkt/
├── konfigurasjon.yml
└── aggregater/
    ├── ost_ga_befolkning_mnd_alder.yml
    ├── ost_ga_befolkning_mnd_alder.sql
    ├── ost_ga_befolkning_mnd_kjonn.yml
    └── ost_ga_befolkning_mnd_kjonn.sql
```

For denne strukturen brukes:

```yaml
filsok:
  - aggregater/**
aggregater:
  - ost_ga_befolkning_mnd_alder
  - ost_ga_befolkning_mnd_kjonn
```

Hver aggregat trenger én `<aggregat>.sql` fil og én `<aggregat>.yml` fil funnet av et mønster i `filsok` konfigurasjon.

Mer om lasting av hemmeligheter følger.

## Konfigurasjon per småaggregat

Per småaggregat må det lages:
- `{aggregatnavn}.yml` eller `{aggregatnavn}.yaml` som konfigurasjon
- `{aggregatnavn}.sql` som SQL-spørring for å hente data fra OBT-tabellen.

For et eksempel for en SQL fil, se `tutorial`. Spørringen må kunne brukes i en subquery, ikke bruk `WITH`.

Aggregatets YAML-filnavn må være småskrevet og følge formatet
`ost_<subject-code>_<subject-kort>_<periode>_<fordeling>`. `<periode>` må være `aar`, `mnd`, `kvartal`, `uke` eller `dag`.
`<fordeling>` er brukerdefinert og består av én
eller flere identifikatorer adskilt med `_` i alfabetisk rekkefølge.

Fullstendig eksempel på aggregatkonfigurasjon:

```yaml
version: 1

# Samler innstillinger og metadata for ett aggregat.
definisjon:
  # Identifiserer aggregatet med en unik id. Den må registreres i /statistikkprodukt_register.md.
  # Produksjons id-er er (20000–22999); test-ID-er '00000'–'09999' (det krever flagget --ingen-id-validering).
  # PX: MATRIX
  id: '01234'
  # Angir kildetabellen (OBT-tabell). Brukes for å hente kolonnekommentarer. De kan overstyres i denne filen.
  referanse-tabell: obt_fa_kontantstotte_kvartal

  # Aggregatnavn (norsk)
  # PX: TITLE (norsk), brukt som tabellens tittel i menyen.
  navn: Eksempel Mottakere av kontantstøtte per kvartal fordelt etter alder og kjønn.
  # Valgfritt: engelsk.
  navn-en: Example Receivers of Cash-for-Care Benefit per quarter and divided by age and sex.
  # Aggregatets innhold (norsk).
  # PX: CONTENTS (norsk), første del av overskriften når tabellen åpnes.
  innhold: Mottakere av kontantstøtte.
  # Valgfritt: engelsk.
  innhold-en: Receivers of Cash-for-Care Benefit,
  # Valgfritt: Teamet med ansvar for datakilden. Publiseres ikke på statistikkbanken.
  ansvarlig-team: Team Familie

  # Samler metadata og visningsinnstillinger som gjelder Statistikkbanken (PX).
  statistikkbank:
    # Statistikkens tema (norsk) på nivå 2 i menyhierarkiet.
    subject: Kontantstøtte
    # Valgfritt: engelsk.
    subject-en: Cash-for-Care Benefit
    # Angir en kode for temaet; må samsvare med kodeverk.
    subject-kort: Kontantstotte
    # Angir fagområdets navn på nivå 1 i menyhierarkiet; må samsvare med kodeverket.
    subject-area: Familie
    # Angir fagområdets kode fra kodeverket.
    subject-code: FA

    # Valgfritt: Norsk merknad, gjerne som markdown.
    note: |
      Denne statistikken inneholder antall mottakere av kontantstøtte per siste måned hvert kvartal, fordelt på alder og kjønn. 
      
      Se [Om statistikken](https://www.nav.no/statistikk).
    # Valgfritt: engelsk.
    note-en: |
      These statistics contain the number of recipients of the cash-for-care benefit as of the last month of each quarter, broken down by age and sex. 
      
      See [About the statististics](https://www.nav.no/statistikk).
    # Valgfritt: Fremheved viktig informasjon over den vanlige merknaden i en blå boks.
    notex: Brudd i statistikken før 2018.
    # Valgfritt: engelsk.
    notex-en: Break in continuity for the statistics before 2018.

    # Valgfritt: Angir om statistikken ikke lenger skal oppdateres. "YES" eller "NO". Trenger anførselstegn.
    discontinued: "NO"
    # Valgfritt: Angir om tabellen publiseres som offisiell statistikk. "YES" eller "NO". Trenger anførselstegn.
    official-statistics: "NO"

    # Valgfritt: Angir antall tellepunkter i startvisningen; positivt velger første N, negativt siste N, 0 ingen.
    # SQ: STAT_VAR
    sq: 1

    # Valgfritt: Statistikkseksjonen vises automatisk; her kan du angi ekstra kontaktpunkter som eksporteres som
    # PX: CONTACT
    ekstra-kontakter:
      # Angir teamet som kan gi informasjon om statistikken.
      - navn: Team Familie
        # Angir kontaktpunktets e-postadresse.
        epost: eksempel@nav.no
        # Valgfritt: Beskrivelse av kontaktpunktet.
        beskrivelse: Team Familie kan svare på spørsmål om statistikken.
        # Valgfritt: Angir et telefonnummer som tekst (med anførselstegn).
        telefonnummer: "+47 12345678"

  # Beskriver tidsdimensjonen.
  periodisering:
    # Angir datakolonnen i kildespørringen som inneholder perioden.
    # CS: TIMEVAL, STUB
    kolonne: aar_kvartal_besk_kort

    # Norsk visningsnavn. Valgfritt: Overstyrer #NAVN fra referansetabellen.
    # CR
    navn: Kvartal
    # Valgfritt: engelsk. Overstyrer #NAVN_EN fra referansetabellen.
    navn-en: Quarter
    # Norsk beskrivelse. Valgfritt: Overstyrer #INNHOLD fra referansetabellen.
    innhold: Kvartalsperiode på formatet 2026K1.
    # Valgfritt: engelsk. Overstyrer #INNHOLD_EN fra referansetabellen.
    innhold-en: Quarter periode on the format 2026K1. (K for quarter in Norwegian)

    # Valgfritt.
    statistikkbank:
      # Valgfritt: Velger antall perioder i startvisning; positivt velger første N, negativt siste N, 0 skjuler variabelen.
      # SQ: periode
      sq: -24

  # Lister tellepunktskolonnene som behandles og prikkes hver for seg.
  tellepunkter:
    # Angir datakolonnen i kildespørringen som inneholder tellepunktet.
    # CS DATA
    - kolonne: kontantstotte_antall_mottakere

      # Norsk visningsnavn.
      # CR
      navn: Antall mottakere
      # Valgfritt: engelsk.
      navn-en: Number of receivers
      # Norsk beskrivelse
      innhold: Antall personer som mottar kontantstøtte
      # Valgfritt: engelsk.
      innhold-en: Number of receivers of Cash-for-Care Benefit.

      # Måleenheten på norsk.
      # CR: enhet for tellepunktet
      enhet: personer
      # Valgfritt: engelsk.
      enhet-en: persons

      # Valgfritt.
      statistikkbank:
        # Valgfritt: Antall desimaler som vises. Del av DATA.
        precision: 0

  # Lister variabler som dataene fordeles etter.
  variabler:
    # Angir datakolonnen for variablen i kildespørringen.
    # CS STUB
    - kolonne: ti_aar_gruppe_besk

      # Norsk visningsnavn. Valgfritt: Overstyrer #NAVN fra referansetabellen.
      # CR
      navn: Aldersgruppe
      # Valgfritt: engelsk. Overstyrer #NAVN_EN fra referansetabellen.
      navn-en: Age group
      # Norsk beskrivelse. Valgfritt: Overstyrer #INNHOLD fra referansetabellen.
      # Valgfritt: Overstyrer #INNHOLD fra referansetabellen.
      innhold: Alder i 10-års grupper.
      # Valgfritt: engelsk. Overstyrer #INNHOLD_EN fra referansetabellen.
      innhold-en: Age in 10-year groups.

      # Valgfritt. Visningsinnstillinger for variablen.
      statistikkbank:
        # Velger første 3 verdier; positivt velger første N, negativt siste N, 0 skjuler variabelen etter alfabetisk sortering.
        sq: 3

    # Variable der all metadata hentes fra referansetabellen og sq default brukes.
    - kolonne: kjonn_flertall_besk

  # Bestemmer hvordan innkommende perioder erstatter eller supplerer eksisterende data.
  # Valgmuligheter:
    # - overskriv_eksisterende_perioder: erstatter alle eksisterende data for perioden.
    # - legg_til_nye_perioder: beholder eksisterende data for perioden, og legger til nye.
  inkrementell-strategi: overskriv_eksisterende_perioder

  # Angir R-formelen som beskriver sammenhenger brukt ved prikking.
  formula: "~ aar_kvartal_besk_kort : (ti_aar_gruppe_besk * kjonn_flertall_besk)"
  # Angir om dataene skal prikkes.
  prikking: true
  # Angir om dataene er særskilt (påvirker småtallsgrense).
  saerskilt: true
```

`referanse-tabell` er OBT-tabellen som er datagrunnlaget og leverer metadata i form av kolonne kommentarer.

Metadata på `periodisering`, `variabler` og `tellepunkter` kommer fra kolonnekommentarer med tags, med mindre man overskriver dem i konfigurasjonsfilen. Tags i kolonnekommentarer er som følger:

- `#NAVN` for norsk navn
- `#NAVN_EN` for engelsk navn
- `#INNHOLD` for norsk beskrivelse
- `#INNHOLD_EN` for engelsk beskrivelse

NB: Både periode og variabler må være streng.

### Statistikkproduktregisteret

Før publisering må du opprette en ny rad i `statistikkprodukt_register.md` og velge en unik `id` mellom 20000 og 22999, uten `NAV`-prefiks.

Fra en utsjekket kopi brukes registeret i repository-roten. Ved installasjon følger
registeret med i pakken. Sett `STATISTIKKPRODUKT_REGISTER_FIL` til en filsti for å
bruke et annet register i stedet.

Til utvikling kan du bruke `'00000'` til `'09999'` med `--ingen-id-validering`. Oppgi test-ID-er som strenger i YAML for å bevare innledende nuller.

Alle aggregater i samme konfigurasjon må ha unik `id`, også ved bruk av test-ID-er.

### Inkrementelle laster

Følgende inkrementelle strategier er støttet. Inkrementelle laster baserer seg på periodekolonnen.

Bruk `--full-relast` for å slette all innhold og laste på nytt fra kilden.

#### `legg_til_nye_perioder`

Laster kun nye perioder:
```text
# I kilden
2025M01
2025M02
2025M03

# I målet
2025M02

# Lastet
2025M01
2025M03
```

#### `overskriv_eksisterende_perioder`

Overskrive alle perioder som finnes i kilden. Perioder som ikke finnes i kilden, men som finnes i målet, beholdes.

```text
# I kilden
2025M01
2025M02
2025M03

# I målet
2024M12
2025M02

# Lastet
2025M01
2025M01
2025M03

# Perioden 2024M12 beholdes.
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
