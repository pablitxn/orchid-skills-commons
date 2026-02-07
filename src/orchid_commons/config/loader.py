"""Configuration loader with hierarchical merge and validation."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from orchid_commons.config.errors import (
    ConfigFileNotFoundError,
    ConfigValidationError,
)
from orchid_commons.config.models import AppSettings
from orchid_commons.config.placeholders import resolve_placeholders

DEFAULT_CONFIG_DIR = Path("config")
DEFAULT_BASE_FILE = "appsettings.json"
ENV_VAR_NAME = "ORCHID_ENV"
DEFAULT_ENV = "development"


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep merge two dictionaries. Override values take precedence.

    Args:
        base: Base dictionary.
        override: Override dictionary (values take precedence).

    Returns:
        New merged dictionary.
    """
    result = base.copy()

    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value

    return result


def load_json_file(path: Path) -> dict[str, Any]:
    """Load and parse a JSON configuration file.

    Args:
        path: Path to the JSON file.

    Returns:
        Parsed configuration dictionary.

    Raises:
        ConfigFileNotFoundError: If the file does not exist.
    """
    if not path.exists():
        raise ConfigFileNotFoundError(str(path))

    with path.open(encoding="utf-8") as f:
        return json.load(f)


def load_config(
    *,
    config_dir: Path | str | None = None,
    env: str | None = None,
    strict_placeholders: bool = True,
) -> AppSettings:
    """Load application configuration with hierarchical merging.

    Configuration is loaded in the following order (later sources override earlier):
    1. config/appsettings.json (base configuration)
    2. config/appsettings.<environment>.json (environment-specific overrides)
    3. Environment variable placeholder resolution

    Args:
        config_dir: Directory containing configuration files. Defaults to "config".
        env: Environment name. Defaults to ORCHID_ENV or "development".
        strict_placeholders: If True, raise error for unresolved placeholders.

    Returns:
        Validated and frozen AppSettings instance.

    Raises:
        ConfigFileNotFoundError: If base configuration file is not found.
        ConfigValidationError: If configuration validation fails.
        PlaceholderResolutionError: If strict_placeholders=True and a placeholder
            cannot be resolved.
    """
    if config_dir is None:
        config_dir = DEFAULT_CONFIG_DIR
    else:
        config_dir = Path(config_dir)

    if env is None:
        env = os.environ.get(ENV_VAR_NAME, DEFAULT_ENV)

    base_path = config_dir / DEFAULT_BASE_FILE
    config = load_json_file(base_path)

    env_path = config_dir / f"appsettings.{env}.json"
    if env_path.exists():
        env_config = load_json_file(env_path)
        config = deep_merge(config, env_config)

    config = resolve_placeholders(config, strict=strict_placeholders)

    try:
        return AppSettings.model_validate(config)
    except ValidationError as e:
        errors = [
            {"loc": " -> ".join(str(loc) for loc in err["loc"]), "msg": err["msg"]}
            for err in e.errors()
        ]
        raise ConfigValidationError(errors) from e
