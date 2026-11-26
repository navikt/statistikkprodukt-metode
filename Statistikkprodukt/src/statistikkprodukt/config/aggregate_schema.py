from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Any, Literal
from pathlib import Path

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationInfo,
    ValidatorFunctionWrapHandler,
    field_validator,
    model_validator,
)


class ConfigError(ValueError):
    """Feil i konfigurasjonen av statistikkprodukt."""


_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_$#]{0,127}$")


class IncrementalMode(StrEnum):
    LEGG_TIL_NYE_PERIODER = "legg_til_nye_perioder"
    OVERSKRIV_EKSISTERENDE_PERIODER = "overskriv_eksisterende_perioder"


def validate_identifier(value: str) -> str:
    """Kontrollerer og småbokstaverer en Oracle-identifikator."""
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"Må være et gyldig Oracle-navn, er: {value!r}")
    return value.lower()


def validate_table_identifier(value: str) -> str:
    """Kontrollerer et tabellnavn, eventuelt kvalifisert med skjema."""
    parts = value.split(".")
    if len(parts) not in {1, 2}:
        raise ValueError(f"Må være et gyldig Oracle-tabellnavn, er: {value!r}")
    return ".".join(validate_identifier(part) for part in parts)


def validate_aggregate_name(value: Any) -> str:
    """Kontrollerer at aggregatnavnet er skrevet med små bokstaver."""
    if not isinstance(value, str) or not re.fullmatch(r"ost_[a-z0-9_]+", value):
        raise ValueError(
            "Aggregatnavnet må være skrevet med små bokstaver og følge formatet "
            "ost_<subject-code>_<subject-kort>_<periode>_<fordeling>."
        )
    return value


def validate_period(value: Any) -> str:
    """Kontrollerer år, måned (M01–M12) eller kvartal (K1–K4)."""
    if not isinstance(value, str) or not re.fullmatch(
        r"[0-9]{4}(?:M(?:0[1-9]|1[0-2])|K[1-4])?", value
    ):
        raise ValueError(
            f"Ugyldig periode {value!r}. Må være YYYY, YYYYM01–YYYYM12 "
            "eller YYYYK1–YYYYK4."
        )
    return value


def is_not_empty(value: str) -> str:
    """Validerer at en streng ikke er tom eller bare mellomrom."""
    if not value.strip():
        raise ConfigError("Strengen kan ikke være tom eller bare mellomrom.")
    return value


def is_aggregate_id(value: str) -> str:
    """Validerer aggregat-ID uten NAV-prefiks."""
    if not re.fullmatch(r"2[0-2][0-9]{3}", value):
        raise ConfigError(
            "id må være mellom 20000 og 22999 uten NAV-prefiks. "
            "Bruk --ingen-id-validering for å tillate test-ID-er fra '00000' til '09999'."
        )
    return value


def is_test_id(value: Any) -> bool:
    """Gjenkjenner femsifrede test-ID-er fra 00000 til 09999."""
    return isinstance(value, str) and re.fullmatch(r"0[0-9]{4}", value) is not None


def normalize_aggregate_id(value: Any) -> Any:
    """Gjør en numerisk aggregat-ID om til en streng før validering."""
    return str(value) if isinstance(value, int) and not isinstance(value, bool) else value


def normalize_yes_no(value: Any) -> Any:
    """Normaliserer tekstverdiene YES og NO til store bokstaver."""
    return value.upper() if isinstance(value, str) else value


NonEmptyString = Annotated[str, AfterValidator(is_not_empty)]
OracleIdentifier = Annotated[str, AfterValidator(validate_identifier)]
OracleTableIdentifier = Annotated[str, AfterValidator(validate_table_identifier)]
AggregateName = Annotated[
    str, BeforeValidator(validate_aggregate_name), AfterValidator(validate_identifier)
]
AggregateId = Annotated[
    str, BeforeValidator(normalize_aggregate_id), AfterValidator(is_aggregate_id)
]
YesNo = Annotated[Literal["YES", "NO"], BeforeValidator(normalize_yes_no)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=False)


class Metadata(StrictModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    navn: str | None = Field(
        default=None, description="Navn."
    )
    navn_en: str | None = Field(
        default=None,
        alias="navn-en",
        description="Engelsk navn.",
    )
    innhold: str | None = Field(
        default=None,
        description="Beskrivelse av innholdet.",
    )
    innhold_en: str | None = Field(
        default=None,
        alias="innhold-en",
        description="Engelsk beskrivelse av innholdet.",
    )


class KolonneMetadata(StrictModel):
    navn: str | None = Field(default=None, description="Navn på kolonnen.")
    navn_en: str | None = Field(
        default=None, alias="navn-en", description="Engelsk navn på kolonnen."
    )
    kolonne: OracleIdentifier = Field(description="Navn på kolonnen i databasen.")
    innhold: str | None = Field(
        default=None, description="Beskrivelse av innholdet i kolonnen."
    )
    innhold_en: str | None = Field(
        default=None,
        alias="innhold-en",
        description="Engelsk beskrivelse av innholdet i kolonnen.",
    )

    @field_validator(*Metadata.model_fields.keys(), mode="before")
    @classmethod
    def normalize_metadata(cls, value: Any) -> Any:
        """Gjør tomme metadatafelt om til manglende verdier."""
        return value.strip() or None if isinstance(value, str) else value


class DimensionStatistikkbank(StrictModel):
    sq: int | None = Field(
        default=None,
        description=(
            "Antall dimensjonsverdier i startvisningen. Positivt velger første N, "
            "negativt siste N i alfabetisk rekkefølge, og 0 skjuler variabelen."
        ),
    )


class MeasureStatistikkbank(StrictModel):
    precision: int | None = Field(
        default=None, ge=0,
        description="Antall desimaler som vises for måltallet, uten å endre lagrede data.",
    )
    decimals: int | None = Field(
        default=None, ge=0,
        description="Antall desimaler som bevares i PX-eksporten, ikke ved generell lasting.",
    )


class Kontakt(StrictModel):
    navn: NonEmptyString = Field(description="Kontaktpunktets navn, for eksempel ansvarlig team.")
    beskrivelse: str | None = Field(
        default=None, description="Kort beskrivelse av kontaktpunktet."
    )
    epost: NonEmptyString = Field(description="Kontaktpunktets e-postadresse.")
    telefonnummer: str | None = Field(
        default=None, description="Kontaktpunktets telefonnummer som tekst."
    )


_SUBJECT_CODE_TO_SUBJECT_AREA_MAPPING = {
    "AM": "Arbeidsmarked",
    "SF": "Sykefravær",
    "AU": "Arbeidsavklaringspenger og uføretrygd",
    "FA": "Familie",
    "OT": "Oppfølging og tiltak",
    "PE": "Pensjon",
    "SB": "Saksbehandling",
    "GA": "Flere statistikkområder",
}


class TableStatistikkbank(StrictModel):
    subject: str | None = Field(
        default=None, description="Norsk tema på nivå 2 i menyhierarkiet."
    )
    subject_en: str | None = Field(
        default=None, alias="subject-en", description="Engelsk tema."
    )
    subject_kort: str = Field(
        min_length=1, alias="subject-kort", description="Statistikkbankens temakode."
    )
    subject_area: str | None = Field(
        default=None, alias="subject-area",
        description="Norsk fagområde på nivå 1 i menyhierarkiet.",
    )
    subject_code: str = Field(
        min_length=1, alias="subject-code",
        description="Fagområdets kode fra Statistikkbankens kodeverk.",
    )
    note: str | None = Field(
        default=None, description="Norsk merknad, gjerne med lenke til Om statistikken."
    )
    note_en: str | None = Field(
        default=None, alias="note-en", description="Engelsk merknad."
    )
    notex: str | None = Field(
        default=None,
        description="Norsk merknad som fremheves over den vanlige merknaden.",
    )
    notex_en: str | None = Field(
        default=None, alias="notex-en",
        description="Engelsk fremhevet merknad.",
    )
    discontinued: YesNo | None = Field(
        default=None,
        description="Om statistikken ikke lenger skal oppdateres: YES eller NO.",
    )
    official_statistics: YesNo | None = Field(
        default=None, alias="official-statistics",
        description="Om tabellen publiseres som offisiell statistikk: YES eller NO.",
    )
    sq: int = Field(
        default=1,
        description=(
            "Antall måltall i startvisningen. Positivt velger første N, "
            "negativt siste N, og 0 ingen."
        ),
    )
    ekstra_kontakter: list[Kontakt] = Field(
        alias="ekstra-kontakter",
        default_factory=list,
        description="Ekstra kontaktpunkter i tillegg til statistikkseksjonen.",
    )

    @model_validator(mode="after")
    def validate_subject_area_mapping(self) -> TableStatistikkbank:
        """Kontrollerer at fagområdekode og fagområde samsvarer."""
        if self.subject_code is None or self.subject_area is None:
            return self
        if _SUBJECT_CODE_TO_SUBJECT_AREA_MAPPING.get(self.subject_code) != self.subject_area:
            mapping = "\n".join(
                f"{code}: {area}" for code, area in _SUBJECT_CODE_TO_SUBJECT_AREA_MAPPING.items()
            )
            raise ConfigError(
                "subject-code og subject-area må følge mappingen:\n"
                f"{mapping}\nMottatt: {self.subject_code}: {self.subject_area}"
            )
        return self


class Variable(KolonneMetadata):
    statistikkbank: DimensionStatistikkbank = Field(
        default_factory=DimensionStatistikkbank,
        description="Statistikkbankens startvisningsinnstillinger for dimensjonen.",
    )


class Tellepunkt(KolonneMetadata):
    enhet: str | None = Field(
        default=None, description="Måltallets norske måleenhet, ikke innholdsbeskrivelsen."
    )
    enhet_en: str | None = Field(
        default=None, alias="enhet-en", description="Måltallets engelske måleenhet."
    )
    statistikkbank: MeasureStatistikkbank = Field(
        default_factory=MeasureStatistikkbank,
        description="Statistikkbankens innstillinger for måltallets visning og PX-eksport.",
    )


class YamlSqlPathPair(StrictModel):
    yaml: Path = Field(description="YAML filbane. Internt")
    sql: Path = Field(description="SQL filbane. Internet")


class Aggregate(StrictModel):
    statistikkbank: TableStatistikkbank = Field(
        description="Tabellens metadata og startvisningsinnstillinger for Statistikkbanken.",
    )
    ansvarlig_team: str | None = Field(
        default=None, alias="ansvarlig-team", description="Teamet som har ansvar for aggregatet."
    )
    id: AggregateId = Field(description="Aggregatets ID.")
    navn: str = Field(description="Navn på aggregatet.")
    navn_en: str = Field(alias="navn-en", description="Engelsk navn på aggregatet.")
    tabell: AggregateName = Field(
        description="Navn på småagregat, definerer <tabell>_PREPUB, <tabell>_META og <tabell>_KONTROLL."
    )
    referanse_tabell: OracleTableIdentifier = Field(
        alias="referanse-tabell",
        description="Kildetabellen som brukes til å hente metadata.",
    )
    innhold: str = Field(description="Beskrivelse av innholdet i aggregatet.")
    innhold_en: str = Field(
        alias="innhold-en",
        description="Engelsk beskrivelse av innholdet i aggregatet.",
    )
    periodisering: Variable = Field(
        description="Kolonne med perioden dataene gjelder for."
    )
    inkrementell_strategi: IncrementalMode = Field(
        alias="inkrementell-strategi",
        description="Bestemmer hvordan perioder i påløpende data overskrives.",
    )
    tellepunkter: list[Tellepunkt] = Field(
        min_length=1,
        description="Måltallskolonnene som inneholder tellingen. Hver prikkes for seg.",
    )
    formula: NonEmptyString = Field(
        description=(
            "R-formelen som brukes til prikking. Må starte med ~ og bare inneholde "
            "kolonner fra variabler og periodisering, samt * + / : og parenteser."
        )
    )
    prikking: bool = Field(description="Angir om dataene skal prikkes.")
    saerskilt: bool = Field(
        description="Angir om særskilt behandling skal brukes ved prikking."
    )
    variabler: list[Variable] = Field(
        description="Dimensjonskolonner aggregatet fordeles på."
    )

    sporring_sql: NonEmptyString = Field(
        description=(
            "Sti til SQL-filen som henter data, relativt til mappen med "
            "hovedkonfigurasjonen. Internt."
        )
    )

    @field_validator("tabell")
    @classmethod
    def validate_derived_table_names(cls, value: str) -> str:
        """Sikrer at alle navngitte tabeller med suffiks er gyldige Oracle-navn."""
        if len(value) + len("_kontroll") > 128:
            raise ValueError(f"Må være et gyldig Oracle-navn, er: {value!r}")
        return value

    @field_validator("id", mode="wrap")
    @classmethod
    def allow_development_id(
        cls, value: Any, handler: ValidatorFunctionWrapHandler, info: ValidationInfo
    ) -> str:
        """Tillater utviklings-ID bare når brukeren uttrykkelig har valgt det."""
        if is_test_id(value) and (info.context or {}).get("ingen_id_validering"):
            return value
        return handler(value)

    @field_validator(*Metadata.model_fields.keys(), mode="before")
    @classmethod
    def normalize_metadata(cls, value: Any) -> Any:
        """Gjør tomme metadatafelt om til manglende verdier."""
        return value.strip() or None if isinstance(value, str) else value

    @property
    def prepub_tabell(self) -> str:
        """Navnet på tabellen/CSV-en med prikkede data."""
        return f"{self.tabell}_prepub"

    @property
    def kontroll_tabell(self) -> str:
        """Navnet på tabellen/CSV-en med kontrolldata for feilsøking."""
        return f"{self.tabell}_kontroll"

    @property
    def meta_tabell(self) -> str:
        """Navnet på tabellen med metadata om aggregatet; overskrives ved hver kjøring."""
        return f"{self.tabell}_meta"

    @field_validator("inkrementell_strategi", mode="before")
    @classmethod
    def validate_incremental_mode(cls, value: str) -> str:
        """Kontrollerer at inkrementell modus er støttet."""
        allowed = list(IncrementalMode)
        if value not in allowed:
            raise ConfigError(
                f"inkrementell-strategi må være én av {', '.join(sorted(allowed))}"
            )
        return value

    @field_validator("variabler")
    @classmethod
    def validate_variables(cls, value: list[Variable]) -> list[Variable]:
        """Krever minst én dimensjonsvariabel."""
        if not value:
            raise ConfigError("variabler må inneholde minst én variabel")
        return value

    @model_validator(mode="after")
    def validate_column_contract(self) -> Aggregate:
        """Kontrollerer at dimensjoner og måltall har unike kolonner."""
        columns = [variable.kolonne for variable in self.variabler]
        tellepunkt_columns = [tellepunkt.kolonne for tellepunkt in self.tellepunkter]
        if len(columns) != len(set(columns)):
            raise ConfigError("variabler kan ikke inneholde gjentatte kolonner")
        if len(tellepunkt_columns) != len(set(tellepunkt_columns)):
            raise ConfigError("tellepunkter kan ikke inneholde gjentatte kolonner")
        if self.periodisering.kolonne in columns:
            raise ConfigError("periodisering.kolonne kan ikke også være en variabel")
        if any(column in columns for column in tellepunkt_columns):
            raise ConfigError("tellepunkt kan ikke også være en variabel")
        if self.periodisering.kolonne in tellepunkt_columns:
            raise ConfigError("tellepunkt.kolonne kan ikke være periodisering.kolonne")
        if {"tellepunkt", "verdi"}.intersection(
            [self.periodisering.kolonne, *columns, *tellepunkt_columns]
        ):
            raise ConfigError("tellepunkt og verdi er reserverte kolonnenavn")
        return self

    @model_validator(mode="after")
    def validate_aggregate_name_parts(self) -> Aggregate:
        """Kontrollerer at aggregatnavnet samsvarer med statistikkbank og periode."""
        subject_code = self.statistikkbank.subject_code.lower()
        subject_kort = self.statistikkbank.subject_kort.lower()
        period = self.periodisering.kolonne
        prefix = f"ost_{subject_code}_{subject_kort}_{period}_"
        # 'else ""' for å feile i følgende regex check
        user_configurable_suffix = self.tabell[len(prefix):] if self.tabell.startswith(prefix) else ""
        distribution_parts = user_configurable_suffix.split("_")
        if (
            not re.fullmatch(r"[a-z]+(?:_[a-z]+)*", user_configurable_suffix)
            or distribution_parts != sorted(distribution_parts)
        ):
            raise ConfigError(
                "Aggregatnavnet må følge formatet "
                "ost_<subject-code>_<subject-kort>_<periode>_<fordeling>, "
                "der subject-code, subject-kort og periode samsvarer med "
                "statistikkbank og periodisering, og fordeling består av "
                "identifikatorer adskilt med _ i alfabetisk sortering. "
                f"Mottatt: {self.tabell!r}."
            )
        return self

    @model_validator(mode="after")
    def validate_formula(self) -> Aggregate:
        """Kontrollerer formelsyntaks og at bare dimensjonskolonner brukes."""
        if not self.formula.startswith("~"):
            raise ConfigError("formula må starte med ~")

        columns = {self.periodisering.kolonne, *(v.kolonne for v in self.variabler)}
        error = (
            "formula må inneholde et gyldig uttrykk med bare kolonner fra variabler "
            "og periodisering, operatorene * + / : og balanserte parenteser; "
            "tellepunkter er ikke tillatt"
        )
        expression = self.formula[1:]
        if not re.fullmatch(r"[A-Za-z0-9_*+/:()\s]+", expression):
            raise ConfigError(error)
        tokens = re.findall(r"[A-Za-z0-9_]+|[*+/:()]", expression)
        expect_column = True
        parentheses = 0
        for token in tokens:
            if expect_column:
                if token == "(":
                    parentheses += 1
                elif token in columns:
                    expect_column = False
                else:
                    raise ConfigError(error)
            elif token == ")" and parentheses:
                parentheses -= 1
            elif token in {"*", "+", "/", ":"}:
                expect_column = True
            else:
                raise ConfigError(error)
        if expect_column or parentheses:
            raise ConfigError(error)
        return self


class AggregateConfiguration(StrictModel):
    version: Literal[1] = Field(description="Versjon av aggregatkonfigurasjonens format.")
    definisjon: Aggregate = Field(description="Definisjonen av ett aggregat.")
