"""Configuration-specific exceptions."""

from __future__ import annotations


class ConfigError(Exception):
    """Base exception for configuration errors."""


class ConfigFileNotFoundError(ConfigError):
    """Raised when a required configuration file is not found."""

    def __init__(self, path: str) -> None:
        self.path = path
        super().__init__(f"Configuration file not found: {path}")


class ConfigValidationError(ConfigError):
    """Raised when configuration validation fails."""

    def __init__(self, errors: list[dict[str, str]]) -> None:
        self.errors = errors
        messages = []
        for err in errors:
            loc = err.get("loc", "unknown")
            msg = err.get("msg", "validation error")
            messages.append(f"  - {loc}: {msg}")
        detail = "\n".join(messages)
        super().__init__(f"Configuration validation failed:\n{detail}")


class PlaceholderResolutionError(ConfigError):
    """Raised when an environment variable placeholder cannot be resolved."""

    def __init__(self, placeholder: str, key_path: str) -> None:
        self.placeholder = placeholder
        self.key_path = key_path
        super().__init__(
            f"Cannot resolve placeholder '{placeholder}' at '{key_path}': "
            f"environment variable not set"
        )
