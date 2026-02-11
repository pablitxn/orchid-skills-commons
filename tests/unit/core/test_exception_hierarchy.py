"""Tests for unified exception hierarchy."""

from __future__ import annotations

from orchid_commons.config.errors import (
    ConfigError,
    ConfigFileNotFoundError,
    ConfigValidationError,
    PlaceholderResolutionError,
)
from orchid_commons.runtime.errors import OrchidCommonsError


class TestExceptionHierarchy:
    """Verify all config exceptions inherit from OrchidCommonsError."""

    def test_config_error_is_orchid_commons_error(self) -> None:
        assert isinstance(ConfigError(), OrchidCommonsError)

    def test_config_file_not_found_is_orchid_commons_error(self) -> None:
        assert isinstance(ConfigFileNotFoundError("missing.json"), OrchidCommonsError)

    def test_config_validation_error_is_orchid_commons_error(self) -> None:
        assert isinstance(ConfigValidationError([]), OrchidCommonsError)

    def test_placeholder_resolution_error_is_orchid_commons_error(self) -> None:
        assert isinstance(PlaceholderResolutionError("${VAR}", "key.path"), OrchidCommonsError)

    def test_config_error_is_still_exception(self) -> None:
        assert isinstance(ConfigError(), Exception)

    def test_catch_config_error_with_orchid_commons_error(self) -> None:
        """Ensure except OrchidCommonsError catches ConfigError."""
        with_caught = False
        try:
            raise ConfigError("test")
        except OrchidCommonsError:
            with_caught = True
        assert with_caught
