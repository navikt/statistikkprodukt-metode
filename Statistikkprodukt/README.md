# Statistikkprodukt

`statistikkprodukt` er et verktøy for datateam for å lage statistikkaggregater med Gaussian Suppression prikking/maskering av små verdier for personvernhensyn.

Verktøyet forbereder metadata til generering av PX-filer for Statistikkbanken. På sikt skal det bli mulig å bruke verktøyet også utenfor Statistikkbank konktekst. 

Verktøyet konfigureres med yaml filer og kan håndtere flere aggregater samtidig.

For hver aggregat::
- Lese data fra Oracle/CSV inkrementell
- Kjøre Gaussian Suppression algoritme implementert i R for hver 
- Lagre resultatet i Oracle/CSV
- Les metadata fra en bredt aggregat tabell (obt), oppdater fra konfigurasjonsfilen, skriv som databasekommentarer-

For hvert aggregat lages `{tabell}_prepub` med prikkede data, `{tabell}_kontroll` med kontrolldata og `{tabell}_meta` med metadata. Databasekjøringer logges i én felles tabell, `ost_logg`.

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

Kjør eksempelet først, som ikke bruker databasen (se [kjør mot databasen](#kjør-mot-databasen)). Eksempelet leser to CSV-filer fra `eksempel/data_inn` og skriver resultatene til `eksempel/data_ut`:

```sh
cd Statistikkprodukt/eksempel
uv run statistikkprodukt last \
  --config konfigurasjon.yml \
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
- `--periode/-p` for å begrense til én periode uansett inkrementell strategi. Kan brukes flere ganger. 

Det betyr man kan:
- Skrive fra Oracle til Oracle, normal ETL.
- Skrive fra CSV-filer til CSV-filer, uten Oracle-tilkobling, bra for testing.
- Skrive fra Oracle til CSV-filer, bra for testing.
- Skrive fra CSV-filer til Oracle, for eventuell manuell overstyring.

Som standard skrives alle små aggregater. Enkelte kan utvelges med navn:
```shell
uv run statistikkprodukt last ost_befolkning_alder ost_befolkning_kjonn
```

### Kjør mot databasen

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

Opprett logg, resultat- og metadatatabellene (se [Databaseskjema](#databaseskjema) for detaljer):

```shell
cd Statistikkprodukt/eksempel
statistikkprodukt skjema opprett --config konfigurasjon.yml --ingen-id-validering --db
```

Uten `--db` vises bare DDL.

Kjør lasten fra databasen til csv:
```shell
uv run statistikkprodukt last --config konfigurasjon.yml --ingen-id-validering --maal=csv
```

CSV filene i `data_ut` overskrives. Du kan slette de for å verifisere at data kommer inn det.

Kjør lasten fra databasen til databasen:
```shell
uv run statistikkprodukt last --config konfigurasjon.yml --ingen-id-validering
```

Sjekk resultatene i Oracle:
```sql
select * from ost_befolkning_alder_kontroll;
-- ... og andre ost tabeller
```

# System dokumentasjon
## Databaseskjema 
Uten `--db` vises bare DDL; med `--db` opprettes manglende tabeller etter oppslag i Oracle dictionary. Vanlig `last` oppretter eller endrer ikke tabellstrukturen.

```sh
# For alle aggregater i konfigurasjonen
uv run statistikkprodukt skjema opprett --config konfigurasjon.yml --db

# For enkelte aggregater
uv run statistikkprodukt skjema opprett --config konfigurasjon.yml --db ost_2 ost_1
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

For et eksempel for en SQL fil, se `eksempel/ost_befolkning_alder.sql`. Spørringen må kunne brukes i en subquery, ikke bruk `WITH`.

Fullstendig eksempel på aggregatkonfigurasjon:

```yaml
# Samler innstillinger og metadata for ett aggregat.
definisjon:
  # Identifiserer aggregatet med en unik id. Den må registreres i /statistikkprodukt_register.txt.
  # Produksjons id-er er (21000–22999); test-ID-er '00000'–'09999' (det krever --ingen-id-validering flagg).
  # PX: Grunnlag for MATRIX; eksportøren legger på NAV-prefiks ved Nav-publisering.
  id: 21000
  # Angir kildetabellen som leverer manglende metadata gjennom databasekommentarer. OBT tabell.
  referanse-tabell: obt_ab_befolkning_periode
  # Angir aggregatets norske navn.
  # PX: TITLE på norsk, brukt som tabellens tittel i menyen.
  navn: Befolkning etter periode og alder
  # Angir aggregatets engelske navn.
  # PX: TITLE på engelsk.
  navn-en: Population by period and age
  # Beskriver aggregatets innhold på norsk.
  # PX: CONTENTS på norsk, første del av overskriften når tabellen åpnes.
  innhold: Befolkning
  # Beskriver aggregatets innhold på engelsk.
  # PX: CONTENTS på engelsk.
  innhold-en: Population
  # Angir teamet som har ansvar for aggregatet.
  ansvarlig-team: Datateam

  # Samler metadata og visningsinnstillinger som gjelder Statistikkbanken.
  statistikkbank:
    # Angir statistikkens norske tema på nivå 2 i menyhierarkiet.
    # PX: SUBJECT på norsk.
    tema: Befolkning
    # Angir statistikkens engelske tema.
    # PX: SUBJECT på engelsk.
    tema-en: Population
    # Angir et kode for temaet; bruksområde må avklares med Statistikkbanken.
    # PX: SUBJECT_KORT; navnet må samsvare med Statistikkbankens mapping av tema-koden.
    tema-kode: BEFOLKNING
    # Angir fagområdets navn på nivå 1 i menyhierarkiet.
    # PX: SUBJECT-AREA; navnet må samsvare med Statistikkbankens mapping av områdekoden.
    fagomraade: Arbeidsmarked
    # Angir fagområdets kode fra Statistikkbankens kodeverk.
    # PX: SUBJECT-CODE.
    fagomraade-kode: AM

    # Gir en utfyllende merknad, gjerne som Markdown.
    # PX: NOTE på norsk.
    merknad: |
      Se [Om statistikken](https://www.nav.no/statistikk).
    # Gir den engelske merknaden og lenken til Om statistikken.
    # PX: NOTE på engelsk.
    merknad-en: |
      See [About the statistics](https://www.nav.no/statistikk).
    # Gir en norsk merknad som skal fremheves over den vanlige merknaden.
    # PX: NOTEX på norsk, vist i en blå boks i Statistikkbanken.
    fremhevet-merknad: Brudd i tidsserien fra januar 2025.
    # Gir den engelske varianten av den fremhevede merknaden.
    # PX: NOTEX på engelsk.
    fremhevet-merknad-en: Break in the time series from January 2025.

    # Angir om statistikken ikke lenger skal oppdateres.
    # PX: DISCONTINUED, med true som YES og false som NO.
    avsluttet: false
    # Angir om tabellen publiseres som offisiell statistikk.
    # PX: OFFICIAL-STATISTICS, med true som YES og false som NO.
    offisiell-statistikk: true

    # Angir antall måltall i startvisningen; positivt velger første N, negativt siste N, 0 ingen.
    # PX: Navs SQ STAT_VAR-rad, som eksportøren bruker til å lage startvisningen.
    startvisning-antall-tellepunkter: 1

    # Lister kontaktpunkter som kan svare på spørsmål om statistikken.
    # PX: CONTACT; eksportøren formaterer kontaktfeltene med # som skilletegn.
    kontakter:
      # Angir seksjonen som kan gi informasjon om statistikken.
      # PX: Seksjonsdelen av CONTACT.
      - seksjon: Statistikkseksjonen
        # Angir kontaktpunktets e-postadresse.
        # PX: E-postdelen av CONTACT.
        epost: nav.statistikk@nav.no
        # Angir et valgfritt telefonnummer som tekst.
        # PX: Telefondelen av CONTACT.
        telefonnummer: "1234"

  # Beskriver tidsdimensjonen som brukes til periodevalg og inkrementell lasting.
  periodisering:
    # Angir datakolonnen som inneholder perioden.
    # PX: Navs CS TIMEVAL-kolonnereferanse; dimensjonen inngår også i STUB eller HEADING.
    kolonne: periode
    # Angir tidsdimensjonens norske visningsnavn.
    # PX: Norsk visningsnavn via Navs CR-metadata for periodekolonnen.
    navn: Periode
    # Angir tidsdimensjonens engelske visningsnavn.
    # PX: Engelsk visningsnavn via Navs CR-metadata for periodekolonnen.
    navn-en: Period
    # Beskriver periodenes betydning og format på norsk.
    innhold: Månedsperiode på formatet YYYYMM.
    # Beskriver periodenes betydning og format på engelsk.
    innhold-en: Month in YYYYMM format.
    # Samler Statistikkbankens visningsinnstillinger for tidsdimensjonen.
    statistikkbank:
      # Velger siste 24 perioder; positivt velger første N, negativt siste N, 0 skjuler variabelen.
      # PX: Navs SQ-rad for periodekolonnen; alfabetisk sortering må tilsvare kronologisk orden.
      startvisning-antall-verdier: -24

  # Lister dimensjonene som dataene fordeles etter.
  # PX: Dimensjonskolonnene er grunnlag for Navs CS STUB og CS HEADING.
  variabler:
    # Angir datakolonnen for dimensjonen.
    # PX: Kolonnereferanse i Navs CS STUB eller CS HEADING og tilhørende CR-metadata.
    - kolonne: alder
      # Angir dimensjonens norske visningsnavn.
      # PX: Norsk visningsnavn via Navs CR-metadata for alder.
      navn: Alder
      # Angir dimensjonens engelske visningsnavn.
      # PX: Engelsk visningsnavn via Navs CR-metadata for alder.
      navn-en: Age
      # Beskriver dimensjonens innhold på norsk.
      innhold: Alder i ettårige grupper.
      # Beskriver dimensjonens innhold på engelsk.
      innhold-en: Age in single-year groups.
      # Samler Statistikkbankens visningsinnstillinger for dimensjonen.
      statistikkbank:
        # Velger første 12 verdier; positivt velger første N, negativt siste N, 0 skjuler variabelen.
        # PX: Navs SQ-rad for alder, med verdier valgt etter alfabetisk sortering.
        startvisning-antall-verdier: 12

  # Lister måltallskolonnene som behandles og prikkes hver for seg.
  # PX: Navs CS DATA og måltallsdimensjonen STAT_VAR hos eksportøren.
  tellepunkter:
    # Angir datakolonnen som inneholder måltallet.
    # PX: Kolonnereferanse i Navs CS DATA og tilhørende CR-metadata.
    - kolonne: antall
      # Angir måltallets norske visningsnavn.
      # PX: Norsk etikett for måltallet via Navs CR-metadata.
      navn: Antall personer
      # Angir måltallets engelske visningsnavn.
      # PX: Engelsk etikett for måltallet via Navs CR-metadata.
      navn-en: Number of people
      # Beskriver måltallet på norsk; dette er ikke måleenheten.
      innhold: Antall personer i gruppen.
      # Beskriver måltallet på engelsk; dette er ikke måleenheten.
      innhold-en: Number of people in the group.
      # Angir måleenheten på norsk.
      # PX: UNITS via Navs enhetsmetadata, med enhetsreferanse i CS DATA i versjon 1.
      enhet: personer
      # Angir måleenheten på engelsk.
      # PX: Engelsk UNITS via Navs enhetsmetadata.
      enhet-en: persons
      # Samler Statistikkbankens innstillinger for måltallets eksport og visning.
      statistikkbank:
        # Angir antall desimaler som skal vises, uten å endre lagrede data.
        # PX: PRECISION per måltall; støtte hos Nav må avklares.
        desimaler-visning: 0
        # Angir antall desimaler som skal bevares i PX-eksporten, ikke avrunding ved generell lasting.
        # PX: Grunnlag for global DECIMALS via Navs CS DATA; eksportøren samordner alle måltall.
        desimaler-lagring: 0

  # Bestemmer hvordan innkommende perioder erstatter eller supplerer eksisterende data.
  # Valgmuligheter:
    # - overskriv_eksisterende_perioder: erstatter alle eksisterende data for perioden.
    # - legg_til_nye_perioder: beholder eksisterende data for perioden, og legger til nye.
  inkrementell-strategi: overskriv_eksisterende_perioder
  # Angir R-formelen som beskriver sammenhenger brukt ved prikking.
  formula: "~ periode : alder"
  # Angir om dataene skal prikkes.
  prikking: true
  # Angir om særskilt behandling skal brukes.
  saerskilt: false
```

`referanse-tabell` er OBT-tabellen som er datagrunnlaget og leverer metadata i form av kolonne kommentarer.

Metadata på `periodisering`, `variabler` og `tellepunkter` kommer fra kolonnekommentarer med tags, med mindre man overskriver dem i konfigurasjonsfilen. Tags i kolonnekommentarer er som følger:

- `#NAVN` for norsk navn
- `#NAVN_EN` for engelsk navn
- `#INNHOLD` for norsk beskrivelse
- `#INNHOLD_EN` for engelsk beskrivelse

NB: Både periode og variabler må være streng.

### Statistikkproduktregisteret

Før publisering må du opprette en ny rad i `statistikkprodukt_register.txt` og velge en unik `id` mellom 21000 og 22999, uten `NAV`-prefiks. 

Til utvikling kan du bruke `'00000'` til `'09999'` med `--ingen-id-validering`. Oppgi test-ID-er som strenger i YAML for å bevare innledende nuller.

Alle aggregater i samme konfigurasjon må ha unik `id`, også ved bruk av test-ID-er.

### Inkrementelle laster

Følgende inkrementelle strategier er støttet. Inkrementelle laster baserer seg på periodekolonnen.

#### `legg_til_nye_perioder`

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

#### `overskriv_eksisterende_perioder`

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
