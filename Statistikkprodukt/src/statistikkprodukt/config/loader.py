from __future__ import annotations

import getpass
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, SplitResult
from collections import defaultdict
from typing import cast

from pydantic import ValidationError

from .aggregate_schema import AggregateConfiguration, ConfigError, Aggregate, YamlSqlPathPair
from .app_schema import (
    Configuration,
    ConfigurationSource,
)

LOGGER = logging.getLogger(__name__)


def load_configuration(
    config_path: str | Path | None, *, ingen_id_validering: bool = False
) -> tuple[Configuration, Path]:
    """Leser fellesinnstillinger og aggregatdefinisjoner fra separate YAML-filer."""

    if config_path is None:
        raise ConfigError(
            "Konfigurasjonsfilen må angis med --config eller STATISTIKKPRODUKT_AGGREGAT_CONFIG."
        )

    import yaml

    path = Path(config_path).expanduser().resolve()
    LOGGER.debug("Leser hovedkonfigurasjon %s.", path)
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
    ) # note: casting since pylance doesnt think both paths are not None
    aggregates: list[Aggregate] = []
    for name, pathpair in aggregate_paths.items():
        LOGGER.debug("Leser aggregatkonfigurasjon %s.", name)
        aggregate_data = _read_yaml(cast(Path, pathpair.yaml) , yaml)
        if isinstance(aggregate_data, dict) and isinstance(
            aggregate_data.get("definisjon"), dict
        ):
            aggregate_data["definisjon"]["tabell"] = name
            aggregate_data["definisjon"]["sporring_sql"] = cast(Path, pathpair.sql).as_posix()
        try:
            aggregate_config = AggregateConfiguration.model_validate(
                aggregate_data, context={"ingen_id_validering": ingen_id_validering}
            )
        except ValidationError as exc:
            raise ConfigError(
                f"Konfigurasjonen i {name} er ugyldig:\n"
                + "\n".join(format_validation_error(error) for error in exc.errors())
            ) from exc

        aggregates.append(aggregate_config.definisjon)

    try:
        config = Configuration(
            version=source.version,
            connection=source.connection,
            aggregater=aggregates,
        )
    except ValidationError as exc:
        raise ConfigError(
            "Konfigurasjonen er ugyldig:\n"
            + "\n".join(format_validation_error(error) for error in exc.errors())
        ) from exc
    LOGGER.debug("Konfigurasjonen er lastet med %d aggregater.", len(aggregates))
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
) -> dict[str, YamlSqlPathPair]:
    """Finner konfigurasjonsfiler for valgte aggregater i søkemønstrene."""
    paths_by_name: dict[str, YamlSqlPathPair] = defaultdict(YamlSqlPathPair)
    errstr_toomany = "{} har for mange {} filer, f.eks. {} og {}"
    for pattern in patterns:
        try:
            file_pattern = (
                f"{pattern}/*" if pattern == "**" or pattern.endswith("/**") else pattern
            )
            for path in directory.glob(file_pattern):
                if not path.is_file():
                    continue
                if (name := path.stem) not in aggregate_names:
                    continue
                suff, newpath = path.suffix.lower(), path.resolve()
                if suff in {".yaml", ".yml"}:
                    if (existingpath := paths_by_name[name].yaml):
                        raise ConfigError(errstr_toomany.format(name, "yaml", existingpath, newpath))
                    paths_by_name[name].yaml = newpath
                elif suff == ".sql":
                    if (existingpath := paths_by_name[name].sql):
                        raise ConfigError(errstr_toomany.format(name, "sql", existingpath, newpath))
                    paths_by_name[name].sql = newpath

        except (OSError, ValueError) as exc:
            raise ConfigError(
                f"Ugyldig mønster for aggregatkonfigurasjoner: {pattern}"
            ) from exc

    LOGGER.debug("Fant aggregatkonfigurasjoner: %s.", sorted(paths_by_name))
    missing = [
        name for name in aggregate_names
        if name not in paths_by_name
        or not (paths_by_name[name].yaml and paths_by_name[name].sql)
    ]
    if missing:
        raise ConfigError(
            f"Fant ikke konfigurasjonsfiler for aggregatene: {', '.join(missing)}."
        )
    return dict(paths_by_name)


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


def resolve_config(config_key: str, reference: str, *, password: bool = False) -> str:
    """Slår opp en bokstavelig verdi eller støttet hemmelighetsreferanse."""
    if "://" not in reference:
        if password:
            raise ConfigError(
                f"Passord for {config_key} må angis som en hemmelighetsreferanse "
                "(f.eks. 'terminal://')."
            )
        return reference

    parsed = urlsplit(reference)
    LOGGER.debug("Løser referansen for %s med skjema %s://.", config_key, parsed.scheme)
    if parsed.scheme == "env":
        return _resolve_config_from_env(config_key, parsed)
    elif parsed.scheme == "terminal":
        return _resolve_config_from_terminal(config_key)
    elif parsed.scheme == "gsm":
        return _resolve_config_from_gsm(config_key, parsed)
    else:
        raise ConfigError(f"Ukjent referanse for {config_key}: {parsed.scheme}://.")


def _resolve_config_from_env(config_key: str, parsed_url: SplitResult) -> str:
    env_var = parsed_url.netloc or parsed_url.path.lstrip("/")
    value = os.environ.get(env_var)
    if value is None:
        raise ConfigError(
            f"Kan ikke lese referansen for {config_key}: "
            f"Miljøvariabelen {env_var} er ikke satt."
        )
    return value


def _resolve_config_from_terminal(config_key: str) -> str:
    if not sys.stdin.isatty():
        raise ConfigError(
            f"Kan ikke lese referansen for {config_key}: "
            "'terminal://' kan bare brukes når standard input er en terminal."
        )
    value = getpass.getpass(f"Tast inn verdien for {config_key}: ")
    if not value:
        raise ConfigError(
            f"Kan ikke lese referansen for {config_key}: "
            "Den inntastete verdien er tom."
        )
    return value


def _resolve_config_from_gsm(config_key: str, parsed_url: SplitResult) -> str:
    from google.api_core.exceptions import GoogleAPIError
    from google.auth.exceptions import GoogleAuthError
    from google.cloud import secretmanager

    secret_path = parsed_url.path.strip("/")
    if parsed_url.netloc:
        secret_path = f"{parsed_url.netloc}/{secret_path}"
    parts = secret_path.split("/")
    json_key = None
    if len(parts) >= 7 and parts[-3] == "versions":
        json_key = parts.pop()
    secret_name = "/".join(parts)
    try:
        response = secretmanager.SecretManagerServiceClient().access_secret_version(
            request={"name": secret_name}
        )
        payload = response.payload.data.decode("utf-8")
        if json_key:
            values = json.loads(payload)
            if not isinstance(values, dict):
                raise ConfigError(
                    f"Kan ikke lese referansen for {config_key} fra GSM: "
                    "Den lagrede verdien er ikke et JSON-objekt."
                )
            if json_key not in values:
                raise ConfigError(
                    f"Kan ikke lese referansen for {config_key} fra GSM: "
                    f"Nøkkelen {json_key!r} finnes ikke i den lagrede verdien."
                )
            payload = values[json_key]
        if not isinstance(payload, str) or not payload:
            raise ConfigError(
                f"Kan ikke lese referansen for {config_key} fra GSM: "
                "Den refererte verdien er tom eller ugyldig."
            )
        return payload
    except (
        GoogleAPIError,
        GoogleAuthError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise ConfigError(
            f"Kan ikke lese referansen for {config_key}: "
            "Feil i kommunikasjon med Google Secret Manager."
        ) from exc
