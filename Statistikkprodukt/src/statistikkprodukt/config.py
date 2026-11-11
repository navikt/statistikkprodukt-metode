from __future__ import annotations

import getpass
import json
import os
import re
import sys
from enum import StrEnum
from pathlib import Path
from typing import Any, Annotated
from urllib.parse import urlsplit

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator, SecretStr,
)


class ConfigError(ValueError):
    """Feil i konfigurasjonen av statistikkprodukt."""


DATABASE_PROFILES = {
    "DWHP": {"host": "dwhp.example.nav.no", "port": 1521, "service_name": "DWHP"},
    "DWHQ": {"host": "dwhq.example.nav.no", "port": 1521, "service_name": "DWHQ"},
    "DWHU": {"host": "dwhu.example.nav.no", "port": 1521, "service_name": "DWHU"},
    "DWHR": {"host": "dwhr.example.nav.no", "port": 1521, "service_name": "DWHR"},
}

_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_$#]{0,127}$")


class InkrementalMode(StrEnum):
    OVERSKRIV_IKKE = "overskriv_ikke"
    OVERSKRIV_ALT = "overskriv_alt"
    OVERSKRIV_SISTE_PERIODE = "overskriv_siste_periode"


class StorageOption(StrEnum):
    DB = "db"
    CSV = "csv"


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
    """Validerer at en streng ikke er tom eller bare mellomrom."""
    if not re.match("^NAV2[1-2][0-9]{3}$", value):
        raise ConfigError("Må være et gyldig NAV-ID, f.eks. NAV22023.")
    return value

NonEmptyString = Annotated[str, AfterValidator(is_not_empty)]
OracleIdentifier = Annotated[str, AfterValidator(validate_identifier)]
OracleTableIdentifier = Annotated[str, AfterValidator(validate_table_identifier)]
AggregateId = Annotated[str, AfterValidator(is_aggregate_id)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=False)


class Metadata(StrictModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    navn: str | None = None
    navn_en: str | None = Field(default=None, alias="navn-en")
    innhold: str | None = None
    innhold_en: str | None = Field(default=None, alias="innhold-en")


class DatabaseConnection(StrictModel):
    database: str | None = None
    host: NonEmptyString | None = None
    port: int | None = Field(default=None, gt=0, le=65535)
    service: NonEmptyString | None = None
    schema_name: OracleIdentifier = Field(alias="schema")
    user: NonEmptyString
    password: SecretStr
    timeout: int = Field(default=30, gt=0)

    @field_validator("database")
    @classmethod
    def validate_database(cls, value: str | None) -> str | None:
        """Kontrollerer at databasenavnet viser til en støttet profil."""
        if value is not None and value not in DATABASE_PROFILES:
            valid_db_names = ", ".join(sorted(DATABASE_PROFILES.keys()))
            raise ConfigError("database må være en av " + valid_db_names)
        return value

    @model_validator(mode="after")
    def validate_connection_source(self) -> DatabaseConnection:
        """Krever enten en profil eller et komplett sett med tilkoblingsverdier."""
        direct_values = (self.host, self.port, self.service)
        has_direct_values = any(value is not None for value in direct_values)
        if self.database is not None and has_direct_values:
            raise ConfigError(
                "Angi enten database eller host, port og service, ikke begge."
            )
        if self.database is None and not has_direct_values:
            raise ConfigError(
                "Angi enten database eller alle feltene host, port og service."
            )
        return self

    def oracle_parameters(self) -> tuple[str, int, str]:
        """Returnerer host, port og tjenestenavn for Oracle-tilkoblingen."""
        if self.database is not None:
            profile = DATABASE_PROFILES[self.database]
            return profile["host"], profile["port"], profile["service_name"]
        if self.host is None or self.port is None or self.service is None:
            raise ConfigError(
                "Angi enten database eller alle feltene host, port og service."
            )
        return self.host, self.port, self.service

    @field_validator("password")
    @classmethod
    def require_password_reference(cls, value: SecretStr) -> SecretStr:
        """Krever en støttet hemmelighetsreferanse for passordet."""
        reference = value.get_secret_value()
        if "://" not in reference:
            raise ConfigError("password må være en hemmelighetsreferanse")
        if urlsplit(reference).scheme not in {"env", "terminal", "gsm"}:
            raise ConfigError("password må bruke env://, terminal:// eller gsm://")
        return value


class KolonneMetadata(StrictModel):
    navn: str | None = None
    navn_en: str | None = Field(default=None, alias="navn-en")
    kolonne: OracleIdentifier
    innhold: str | None = None
    innhold_en: str | None = Field(default=None, alias="innhold-en")

    # For everyone of navn, navn_en, ..., we want to normalize empty strings to None
    @field_validator(*Metadata.model_fields.keys(), mode="before")
    @classmethod
    def normalize_metadata(cls, value: Any) -> Any:
        """Gjør tomme metadatafelt om til manglende verdier."""
        return value.strip() or None if isinstance(value, str) else value


class Variable(KolonneMetadata):
    pass


class Tellepunkt(KolonneMetadata):
    pass


class SmallAggregate(StrictModel):
    id: AggregateId
    navn: str
    navn_en: str = Field(alias="navn-en")
    tabell: OracleIdentifier
    obt_tabell: OracleTableIdentifier = Field(alias="obt-tabell")
    innhold: str
    innhold_en: str = Field(alias="innhold-en")
    sporring_sql: NonEmptyString
    periodisering: Variable
    inkrementell_strategi: InkrementalMode = Field(alias="inkrementell-strategi")
    tellepunkter: list[Tellepunkt] = Field(min_length=1, max_length=1)
    formula: NonEmptyString
    prikking: bool
    saerskilt: bool
    variabler: list[Variable]

    # For everyone of navn, navn_en, ..., we want to normalize empty strings to None
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

    @field_validator("inkrementell_strategi", mode="before")
    @classmethod
    def validate_incremental_mode(cls, value: str) -> str:
        """Kontrollerer at inkrementell modus er støttet."""
        allowed = list(InkrementalMode)
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
    def validate_column_contract(self) -> SmallAggregate:
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
    definisjon: SmallAggregate


class ConfigurationSource(StrictModel):
    connection: DatabaseConnection
    mapper: list[NonEmptyString]
    aggregater: list[OracleIdentifier]

    @field_validator("mapper")
    @classmethod
    def validate_mappers(cls, value: list[str]) -> list[str]:
        """Krever minst ett mønster for aggregatkonfigurasjoner."""
        if not value:
            raise ConfigError("mapper må inneholde minst ett mønster")
        return value

    @field_validator("aggregater")
    @classmethod
    def validate_aggregate_names(cls, value: list[str]) -> list[str]:
        """Krever minst ett unikt aggregatnavn."""
        if not value:
            raise ConfigError("aggregater må inneholde minst ett aggregat")
        if len(value) != len(set(value)):
            raise ConfigError("aggregatnavn må være unike")
        return value


class Configuration(StrictModel):
    connection: DatabaseConnection
    aggregater: list[SmallAggregate]

    @field_validator("aggregater")
    @classmethod
    def validate_aggregates(cls, value: list[SmallAggregate]) -> list[SmallAggregate]:
        """Krever aggregater med unike måltabeller."""
        if not value:
            raise ConfigError("aggregater må inneholde minst ett aggregat")
        tables = [item.tabell.upper() for item in value]
        if len(tables) != len(set(tables)):
            raise ConfigError("tabellnavn må være unike")
        return value


def load_configuration(config_path: str | Path | None) -> tuple[Configuration, Path]:
    """Leser fellesinnstillinger og aggregatdefinisjoner fra separate YAML-filer."""

    if config_path is None:
        raise ConfigError(
            "Konfigurasjonsfilen må angis med --config eller STATISTIKKPRODUKT_AGGREGAT_CONFIG."
        )

    import yaml

    path = Path(config_path).expanduser().resolve()
    data = _read_yaml(path, yaml)
    try:
        source = ConfigurationSource.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(
            "Konfigurasjonen er ugyldig:\n"
            + "\n".join(format_validation_error(error) for error in exc.errors())
        ) from exc

    aggregate_paths = _find_aggregate_configurations(
        source.mapper, source.aggregater, path.parent
    )
    aggregates: list[SmallAggregate] = []
    for aggregate_path in aggregate_paths:
        aggregate_data = _read_yaml(aggregate_path, yaml)
        if isinstance(aggregate_data, dict) and isinstance(
                aggregate_data.get("definisjon"), dict
        ):
            aggregate_data["definisjon"]["tabell"] = aggregate_path.stem
            aggregate_data["definisjon"]["sporring_sql"] = Path(
                os.path.relpath(aggregate_path.with_suffix(".sql"), path.parent)
            ).as_posix()
        try:
            aggregate_config = AggregateConfiguration.model_validate(aggregate_data)
        except ValidationError as exc:
            raise ConfigError(
                f"Konfigurasjonen i {aggregate_path} er ugyldig:\n"
                + "\n".join(format_validation_error(error) for error in exc.errors())
            ) from exc

        aggregates.append(aggregate_config.definisjon)

    try:
        config = Configuration(
            connection=source.connection,
            aggregater=aggregates,
        )
    except ValidationError as exc:
        raise ConfigError(
            "Konfigurasjonen er ugyldig:\n"
            + "\n".join(format_validation_error(error) for error in exc.errors())
        ) from exc
    return config, path.parent


def _read_yaml(path: Path, yaml: Any) -> Any:
    """Leser YAML og avviser gjentatte nøkler."""

    def unique_mapping(loader: Any, node: Any, deep: bool = False) -> dict:
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            if key in mapping:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"nøkkelen {key!r} er oppgitt flere ganger",
                    key_node.start_mark,
                )
            mapping[key] = loader.construct_object(value_node, deep=deep)
        return mapping

    class UniqueSafeLoader(yaml.SafeLoader):
        pass

    UniqueSafeLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping
    )

    try:
        return yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueSafeLoader)
    except UnicodeDecodeError as exc:
        raise ConfigError(f"Konfigurasjonsfilen må være kodet som UTF-8: {path}") from exc
    except OSError as exc:
        raise ConfigError(f"Konfigurasjonsfilen kan ikke leses: {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"Konfigurasjonsfilen inneholder ugyldig YAML: {path}") from exc


def _find_aggregate_configurations(
        patterns: list[str], aggregate_names: list[str], directory: Path
) -> list[Path]:
    """Finner konfigurasjonsfiler for valgte aggregater i søkemønstrene."""
    paths: set[Path] = set()
    for pattern in patterns:
        try:
            file_pattern = f"{pattern}/*" if pattern == "**" or pattern.endswith("/**") else pattern
            matches = directory.glob(file_pattern)
            paths.update(
                path.resolve()
                for path in matches
                if path.is_file() and path.suffix.lower() in {".yml", ".yaml"}
            )
        except (OSError, ValueError) as exc:
            raise ConfigError(f"Ugyldig mønster for aggregatkonfigurasjoner: {pattern}") from exc

    paths_by_name: dict[str, Path] = {}
    for path in sorted(paths):
        name = path.stem.lower()
        if name in paths_by_name:
            raise ConfigError(
                f"Aggregatnavnet {name} finnes i flere konfigurasjonsfiler: "
                f"{paths_by_name[name]} og {path}."
            )
        paths_by_name[name] = path

    missing = [name for name in aggregate_names if name not in paths_by_name]
    if missing:
        raise ConfigError(
            f"Fant ikke konfigurasjonsfiler for aggregatene: {', '.join(missing)}."
        )
    return [paths_by_name[name] for name in aggregate_names]


def format_validation_error(error: dict[str, Any]) -> str:
    """Gjør en Pydantic-feil om til en kort norsk melding."""
    location = ".".join(str(item) for item in error.get("loc", ())) or "konfigurasjonen"
    if error["type"] == "extra_forbidden":
        return f"Ukjent felt: {location}."
    if error["type"] == "missing":
        return f"Feltet {location} mangler."
    message = str(error.get("msg", ""))
    if message.startswith("Value error, "):
        message = message.removeprefix("Value error, ")
    if not message or not any(character in message for character in "æøåÆØÅ"):
        return f"Ugyldig verdi for feltet {location}."
    return f"{location}: {message}"


def resolve_secret(reference: str, *, password: bool = False) -> str:
    """Slår opp en bokstavelig verdi eller støttet hemmelighetsreferanse."""
    if "://" not in reference:
        if password:
            raise ConfigError("Passord må angis som en hemmelighetsreferanse.")
        return reference

    parsed = urlsplit(reference)
    if parsed.scheme == "env":
        key = parsed.netloc or parsed.path.lstrip("/")
        value = os.environ.get(key)
        if value is None:
            raise ConfigError(f"Miljøvariabelen {key} er ikke satt.")
        return value
    if parsed.scheme == "terminal":
        if not sys.stdin.isatty():
            raise ConfigError("terminal:// kan bare brukes når standard input er en terminal.")
        value = getpass.getpass("Skriv inn hemmelig verdi: ")
        if not value:
            raise ConfigError("Den innleste hemmeligheten kan ikke være tom.")
        return value
    if parsed.scheme == "gsm":
        from google.api_core.exceptions import GoogleAPIError
        from google.auth.exceptions import GoogleAuthError
        from google.cloud import secretmanager

        secret_path = parsed.path.strip("/")
        if parsed.netloc:
            secret_path = f"{parsed.netloc}/{secret_path}"
        parts = secret_path.split("/")
        key = None
        if len(parts) >= 7 and parts[-3] == "versions":
            key = parts.pop()
        secret_name = "/".join(parts)
        try:
            response = secretmanager.SecretManagerServiceClient().access_secret_version(
                request={"name": secret_name}
            )
            payload = response.payload.data.decode("utf-8")
            if key:
                values = json.loads(payload)
                if not isinstance(values, dict) or key not in values:
                    raise ConfigError(f"Nøkkelen {key!r} finnes ikke i den lagrede hemmeligheten.")
                payload = values[key]
            if not isinstance(payload, str) or not payload:
                raise ConfigError("Den refererte hemmelighetsverdien er tom eller ugyldig.")
            return payload
        except (GoogleAPIError, GoogleAuthError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ConfigError("Kunne ikke hente den refererte hemmeligheten fra Google Secret Manager.") from exc
    raise ConfigError(f"Ukjent hemmelighetsreferanse: {parsed.scheme}://.")
