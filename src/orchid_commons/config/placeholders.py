"""Environment variable placeholder resolution."""

from __future__ import annotations

import os
import re
from typing import Any

from orchid_commons.config.errors import PlaceholderResolutionError

PLACEHOLDER_PATTERN = re.compile(r"\$\{([^}]+)\}")


def resolve_placeholders(
    data: dict[str, Any],
    *,
    strict: bool = True,
    _path: str = "",
) -> dict[str, Any]:
    """Resolve ${ENV_VAR} placeholders in configuration data.

    Args:
        data: Configuration dictionary to process.
        strict: If True, raise error for unresolved placeholders.
        _path: Internal path tracker for error messages.

    Returns:
        New dictionary with placeholders resolved.

    Raises:
        PlaceholderResolutionError: If strict=True and a placeholder cannot be resolved.
    """
    result: dict[str, Any] = {}

    for key, value in data.items():
        current_path = f"{_path}.{key}" if _path else key

        if isinstance(value, dict):
            result[key] = resolve_placeholders(value, strict=strict, _path=current_path)
        elif isinstance(value, list):
            result[key] = [
                _resolve_value(item, f"{current_path}[{i}]", strict)
                for i, item in enumerate(value)
            ]
        else:
            result[key] = _resolve_value(value, current_path, strict)

    return result


def _resolve_value(value: Any, path: str, strict: bool) -> Any:
    """Resolve placeholders in a single value."""
    if not isinstance(value, str):
        return value

    def replace_match(match: re.Match[str]) -> str:
        env_var = match.group(1)
        env_value = os.environ.get(env_var)

        if env_value is None:
            if strict:
                raise PlaceholderResolutionError(f"${{{env_var}}}", path)
            return match.group(0)

        return env_value

    return PLACEHOLDER_PATTERN.sub(replace_match, value)
