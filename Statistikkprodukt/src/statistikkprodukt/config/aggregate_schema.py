from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Any

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


NonEmptyString = Annotated[str, AfterValidator(is_not_empty)]
OracleIdentifier = Annotated[str, AfterValidator(validate_identifier)]
OracleTableIdentifier = Annotated[str, AfterValidator(validate_table_identifier)]
AggregateId = Annotated[
    str, BeforeValidator(normalize_aggregate_id), AfterValidator(is_aggregate_id)
]


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
    startvisning_antall_verdier: int | None = Field(
        default=None,
        alias="startvisning-antall-verdier",
        description=(
            "Antall dimensjonsverdier i startvisningen. Positivt velger første N, "
            "negativt siste N i alfabetisk rekkefølge, og 0 skjuler variabelen."
        ),
    )


class MeasureStatistikkbank(StrictModel):
    desimaler_visning: int | None = Field(
        default=None, alias="desimaler-visning", ge=0,
        description="Antall desimaler som vises for måltallet, uten å endre lagrede data.",
    )
    desimaler_lagring: int | None = Field(
        default=None, alias="desimaler-lagring", ge=0,
        description="Antall desimaler som bevares i PX-eksporten, ikke ved generell lasting.",
    )


class Kontakt(StrictModel):
    seksjon: NonEmptyString = Field(description="Seksjonen som svarer på spørsmål om statistikken.")
    epost: NonEmptyString = Field(description="Kontaktpunktets e-postadresse.")
    telefonnummer: str | None = Field(
        default=None, description="Kontaktpunktets telefonnummer som tekst."
    )


class TableStatistikkbank(StrictModel):
    tema: str | None = Field(
        default=None, description="Norsk tema på nivå 2 i menyhierarkiet; PX SUBJECT."
    )
    tema_en: str | None = Field(
        default=None, alias="tema-en", description="Engelsk tema; PX SUBJECT."
    )
    tema_kode: str | None = Field(
        default=None, alias="tema-kode", description="Statistikkbankens temakode; PX SUBJECT_KORT."
    )
    fagomraade: str | None = Field(
        default=None, description="Norsk fagområde på nivå 1 i menyhierarkiet; PX SUBJECT-AREA."
    )
    fagomraade_kode: str | None = Field(
        default=None, alias="fagomraade-kode",
        description="Fagområdets kode fra Statistikkbankens kodeverk; PX SUBJECT-CODE.",
    )
    merknad: str | None = Field(
        default=None, description="Norsk merknad, gjerne med lenke til Om statistikken; PX NOTE."
    )
    merknad_en: str | None = Field(
        default=None, alias="merknad-en", description="Engelsk merknad; PX NOTE."
    )
    fremhevet_merknad: str | None = Field(
        default=None, alias="fremhevet-merknad",
        description="Norsk merknad som fremheves over den vanlige merknaden; PX NOTEX.",
    )
    fremhevet_merknad_en: str | None = Field(
        default=None, alias="fremhevet-merknad-en",
        description="Engelsk fremhevet merknad; PX NOTEX.",
    )
    avsluttet: bool | None = Field(
        default=None,
        description="Om statistikken ikke lenger skal oppdateres; PX DISCONTINUED.",
    )
    offisiell_statistikk: bool | None = Field(
        default=None, alias="offisiell-statistikk",
        description="Om tabellen publiseres som offisiell statistikk; PX OFFICIAL-STATISTICS.",
    )
    startvisning_antall_tellepunkter: int = Field(
        default=1, alias="startvisning-antall-tellepunkter",
        description=(
            "Antall måltall i startvisningen (SQ STAT_VAR). Positivt velger første N, "
            "negativt siste N, og 0 ingen."
        ),
    )
    kontakter: list[Kontakt] = Field(
        default_factory=list,
        description="Kontaktpunkter for spørsmål om statistikken; én PX CONTACT-rad per kontakt.",
    )


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


class Aggregate(StrictModel):
    statistikkbank: TableStatistikkbank = Field(
        default_factory=TableStatistikkbank,
        description="Tabellens metadata og startvisningsinnstillinger for Statistikkbanken.",
    )
    ansvarlig_team: str | None = Field(
        default=None, alias="ansvarlig-team", description="Teamet som har ansvar for aggregatet."
    )
    id: AggregateId = Field(description="Aggregatets ID.")
    navn: str = Field(description="Navn på aggregatet.")
    navn_en: str = Field(alias="navn-en", description="Engelsk navn på aggregatet.")
    tabell: OracleIdentifier = Field(description="Navn på måltabellen.")
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
        description="R-formelen som brukes til prikking."
    )
    prikking: bool = Field(description="Angir om dataene skal prikkes.")
    saerskilt: bool = Field(
        description="Angir om særskilt behandling skal brukes ved prikking."
    )
    variabler: list[Variable] = Field(
        description="Dimensjonskolonner aggregatet fordeles på."
    )

    sporring_sql: NonEmptyString = Field(
        description="Sti til SQL-filen som henter data, relativt til konfigurasjonen. Internt."
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


class AggregateConfiguration(StrictModel):
    definisjon: Aggregate = Field(description="Definisjonen av ett aggregat.")
