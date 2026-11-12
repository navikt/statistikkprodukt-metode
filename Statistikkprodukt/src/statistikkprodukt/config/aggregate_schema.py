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
    OVERSKRIV_IKKE = "overskriv_ikke"
    OVERSKRIV_ALT = "overskriv_alt"
    OVERSKRIV_SISTE_PERIODE = "overskriv_siste_periode"


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
    if not re.fullmatch(r"2[1-2][0-9]{3}", value):
        raise ConfigError(
            "id må være mellom 21000 og 22999 uten NAV-prefiks. "
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


class Variable(KolonneMetadata):
    pass


class Tellepunkt(KolonneMetadata):
    pass


class Aggregate(StrictModel):
    id: AggregateId = Field(description="Aggregatets ID.")
    navn: str = Field(description="Navn på aggregatet.")
    navn_en: str = Field(alias="navn-en", description="Engelsk navn på aggregatet.")
    tabell: OracleIdentifier = Field(description="Navn på måltabellen.")
    obt_tabell: OracleTableIdentifier = Field(
        alias="obt-tabell",
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
        max_length=1,
        description="Måltallskolonnen som inneholder tellingen.",
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
        if len(columns) != len(set(columns)):
            raise ConfigError("variabler kan ikke inneholde gjentatte kolonner")
        if self.periodisering.kolonne in columns:
            raise ConfigError("periodisering.kolonne kan ikke også være en variabel")
        if any(tellepunkt.kolonne in columns for tellepunkt in self.tellepunkter):
            raise ConfigError("tellepunkt kan ikke også være en variabel")
        if any(
            tellepunkt.kolonne == self.periodisering.kolonne in columns
            for tellepunkt in self.tellepunkter
        ):
            raise ConfigError("tellepunkt.kolonne kan ikke være periodisering.kolonne")
        return self


class AggregateConfiguration(StrictModel):
    definisjon: Aggregate = Field(description="Definisjonen av ett aggregat.")
