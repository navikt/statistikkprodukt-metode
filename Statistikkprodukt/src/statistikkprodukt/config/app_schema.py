from __future__ import annotations

from enum import StrEnum
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator

from .aggregate_schema import (
    ConfigError,
    NonEmptyString,
    OracleIdentifier,
    Aggregate,
    StrictModel,
)


DATABASE_PROFILES = {
    "DWHP": {"host": "dwhp.example.nav.no", "port": 1521, "service_name": "DWHP"},
    "DWHQ": {"host": "dwhq.example.nav.no", "port": 1521, "service_name": "DWHQ"},
    "DWHU": {"host": "dwhu.example.nav.no", "port": 1521, "service_name": "DWHU"},
    "DWHR": {"host": "dwhr.example.nav.no", "port": 1521, "service_name": "DWHR"},
}


class StorageOption(StrEnum):
    DB = "db"
    CSV = "csv"


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
    aggregater: list[Aggregate]

    @field_validator("aggregater")
    @classmethod
    def validate_aggregates(cls, value: list[Aggregate]) -> list[Aggregate]:
        """Krever aggregater med unike måltabeller og ID-er."""
        if not value:
            raise ConfigError("aggregater må inneholde minst ett aggregat")
        tables = [item.tabell.upper() for item in value]
        if len(tables) != len(set(tables)):
            raise ConfigError("tabellnavn må være unike")
        ids = [item.id for item in value]
        if len(ids) != len(set(ids)):
            raise ConfigError("id må være unike, også for test-ID-er")
        return value
