from __future__ import annotations

import getpass
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import ValidationError

from .aggregate_schema import AggregateConfiguration, ConfigError, SmallAggregate
from .app_schema import (
    Configuration,
    ConfigurationSource,
)


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
            file_pattern = (
                f"{pattern}/*" if pattern == "**" or pattern.endswith("/**") else pattern
            )
            matches = directory.glob(file_pattern)
            paths.update(
                path.resolve()
                for path in matches
                if path.is_file() and path.suffix.lower() in {".yml", ".yaml"}
            )
        except (OSError, ValueError) as exc:
            raise ConfigError(
                f"Ugyldig mønster for aggregatkonfigurasjoner: {pattern}"
            ) from exc

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
                    raise ConfigError(
                        f"Nøkkelen {key!r} finnes ikke i den lagrede hemmeligheten."
                    )
                payload = values[key]
            if not isinstance(payload, str) or not payload:
                raise ConfigError(
                    "Den refererte hemmelighetsverdien er tom eller ugyldig."
                )
            return payload
        except (
            GoogleAPIError,
            GoogleAuthError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            raise ConfigError(
                "Kunne ikke hente den refererte hemmeligheten fra Google Secret Manager."
            ) from exc
    raise ConfigError(f"Ukjent hemmelighetsreferanse: {parsed.scheme}://.")
