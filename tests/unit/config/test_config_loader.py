"""Tests for configuration loader module."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from orchid_commons.config import (
    ConfigFileNotFoundError,
    ConfigValidationError,
    PlaceholderResolutionError,
    deep_merge,
    load_config,
)

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "config"


class TestDeepMerge:
    """Tests for deep_merge function."""

    def test_simple_merge(self) -> None:
        base = {"a": 1, "b": 2}
        override = {"b": 3, "c": 4}
        result = deep_merge(base, override)
        assert result == {"a": 1, "b": 3, "c": 4}

    def test_nested_merge(self) -> None:
        base = {"a": {"x": 1, "y": 2}, "b": 3}
        override = {"a": {"y": 10, "z": 20}}
        result = deep_merge(base, override)
        assert result == {"a": {"x": 1, "y": 10, "z": 20}, "b": 3}

    def test_override_dict_with_scalar(self) -> None:
        base = {"a": {"x": 1}}
        override = {"a": "replaced"}
        result = deep_merge(base, override)
        assert result == {"a": "replaced"}

    def test_override_scalar_with_dict(self) -> None:
        base = {"a": "scalar"}
        override = {"a": {"x": 1}}
        result = deep_merge(base, override)
        assert result == {"a": {"x": 1}}

    def test_does_not_mutate_original(self) -> None:
        base = {"a": {"x": 1}}
        override = {"a": {"y": 2}}
        deep_merge(base, override)
        assert base == {"a": {"x": 1}}


class TestLoadConfig:
    """Tests for load_config function."""

    def test_load_base_config(self) -> None:
        settings = load_config(config_dir=FIXTURES_DIR, env="nonexistent")
        assert settings.service.name == "test-service"
        assert settings.service.version == "1.0.0"
        assert settings.logging.level == "INFO"

    def test_load_development_env(self) -> None:
        settings = load_config(config_dir=FIXTURES_DIR, env="development")
        assert settings.logging.level == "DEBUG"
        assert settings.observability.enabled is False
        assert settings.service.name == "test-service"

    def test_load_staging_env(self) -> None:
        settings = load_config(config_dir=FIXTURES_DIR, env="staging")
        assert settings.service.port == 8080
        assert settings.logging.level == "INFO"
        assert settings.observability.sample_rate == 0.25

    def test_load_production_with_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4317")
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")

        settings = load_config(config_dir=FIXTURES_DIR, env="production")

        assert settings.service.port == 8080
        assert settings.logging.level == "WARNING"
        assert settings.observability.otlp_endpoint == "http://collector:4317"
        assert settings.resources.postgres is not None
        assert (
            settings.resources.postgres.dsn.get_secret_value()
            == "postgresql://user:pass@localhost/db"
        )
        assert settings.resources.postgres.max_pool_size == 20

    def test_missing_base_config_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigFileNotFoundError) as exc_info:
            load_config(config_dir=tmp_path)
        assert "appsettings.json" in str(exc_info.value)

    def test_unresolved_placeholder_raises(self) -> None:
        with pytest.raises(PlaceholderResolutionError) as exc_info:
            load_config(config_dir=FIXTURES_DIR, env="production")
        assert "OTEL_EXPORTER_OTLP_ENDPOINT" in str(exc_info.value)

    def test_non_strict_placeholders(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
        monkeypatch.delenv("DATABASE_URL", raising=False)

        settings = load_config(
            config_dir=FIXTURES_DIR,
            env="production",
            strict_placeholders=False,
        )
        assert settings.observability.otlp_endpoint == "${OTEL_EXPORTER_OTLP_ENDPOINT}"

    def test_env_from_orchid_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ORCHID_ENV", "staging")
        settings = load_config(config_dir=FIXTURES_DIR)
        assert settings.service.port == 8080
        assert settings.observability.sample_rate == 0.25

    def test_config_is_frozen(self) -> None:
        settings = load_config(config_dir=FIXTURES_DIR, env="development")
        with pytest.raises(ValidationError):
            settings.service.port = 9999  # type: ignore[misc]

    def test_load_langfuse_config(self, tmp_path: Path) -> None:
        config_file = tmp_path / "appsettings.json"
        config_file.write_text(
            """
            {
              "service": {"name": "test", "version": "1.0"},
              "observability": {
                "enabled": true,
                "langfuse": {
                  "enabled": true,
                  "public_key": "pk-test",
                  "secret_key": "sk-test",
                  "environment": "staging",
                  "sample_rate": 0.25
                }
              }
            }
            """,
            encoding="utf-8",
        )

        settings = load_config(config_dir=tmp_path)
        assert settings.observability.langfuse.enabled is True
        assert settings.observability.langfuse.public_key.get_secret_value() == "pk-test"
        assert settings.observability.langfuse.secret_key.get_secret_value() == "sk-test"
        assert settings.observability.langfuse.environment == "staging"
        assert settings.observability.langfuse.sample_rate == 0.25

    def test_load_observability_otlp_retry_config(self, tmp_path: Path) -> None:
        config_file = tmp_path / "appsettings.json"
        config_file.write_text(
            """
            {
              "service": {"name": "test", "version": "1.0"},
              "observability": {
                "enabled": true,
                "otlp_endpoint": "http://collector:4317",
                "otlp_timeout_seconds": 7.5,
                "retry_enabled": true,
                "retry_max_attempts": 5,
                "retry_initial_backoff_seconds": 0.3,
                "retry_max_backoff_seconds": 3.0,
                "metrics_export_interval_seconds": 15.0
              }
            }
            """,
            encoding="utf-8",
        )

        settings = load_config(config_dir=tmp_path)
        assert settings.observability.enabled is True
        assert settings.observability.otlp_endpoint == "http://collector:4317"
        assert settings.observability.otlp_timeout_seconds == 7.5
        assert settings.observability.retry_enabled is True
        assert settings.observability.retry_max_attempts == 5
        assert settings.observability.retry_initial_backoff_seconds == 0.3
        assert settings.observability.retry_max_backoff_seconds == 3.0
        assert settings.observability.metrics_export_interval_seconds == 15.0


class TestConfigValidation:
    """Tests for configuration validation errors."""

    def test_missing_required_field(self, tmp_path: Path) -> None:
        config_file = tmp_path / "appsettings.json"
        config_file.write_text('{"service": {"name": "test"}}')

        with pytest.raises(ConfigValidationError) as exc_info:
            load_config(config_dir=tmp_path)

        error_msg = str(exc_info.value)
        assert "service" in error_msg
        assert "version" in error_msg

    def test_invalid_port(self, tmp_path: Path) -> None:
        config_file = tmp_path / "appsettings.json"
        config_file.write_text('{"service": {"name": "test", "version": "1.0", "port": 999999}}')

        with pytest.raises(ConfigValidationError) as exc_info:
            load_config(config_dir=tmp_path)

        error_msg = str(exc_info.value)
        assert "port" in error_msg

    def test_invalid_log_level(self, tmp_path: Path) -> None:
        config_file = tmp_path / "appsettings.json"
        config_file.write_text(
            '{"service": {"name": "test", "version": "1.0"}, "logging": {"level": "INVALID"}}'
        )

        with pytest.raises(ConfigValidationError) as exc_info:
            load_config(config_dir=tmp_path)

        error_msg = str(exc_info.value)
        assert "logging" in error_msg
        assert "level" in error_msg


class TestPlaceholderResolution:
    """Tests for environment variable placeholder resolution."""

    def test_resolve_single_placeholder(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MY_SECRET", "secret123")

        config_file = tmp_path / "appsettings.json"
        config_file.write_text('{"service": {"name": "${MY_SECRET}", "version": "1.0"}}')

        settings = load_config(config_dir=tmp_path)
        assert settings.service.name == "secret123"

    def test_resolve_multiple_in_string(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOST", "localhost")
        monkeypatch.setenv("PORT", "5432")

        config_file = tmp_path / "appsettings.json"
        config_file.write_text('{"service": {"name": "db-${HOST}-${PORT}", "version": "1.0"}}')

        settings = load_config(config_dir=tmp_path)
        assert settings.service.name == "db-localhost-5432"

    def test_placeholder_in_nested_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DB_PATH", "/var/data/app.db")

        config_file = tmp_path / "appsettings.json"
        config_file.write_text(
            '{"service": {"name": "test", "version": "1.0"}, '
            '"resources": {"sqlite": {"db_path": "${DB_PATH}"}}}'
        )

        settings = load_config(config_dir=tmp_path)
        assert settings.resources.sqlite is not None
        assert str(settings.resources.sqlite.db_path) == "/var/data/app.db"


class TestR2Config:
    """Tests for Cloudflare R2 configuration profile."""

    def test_r2_endpoint_is_derived_from_account(self, tmp_path: Path) -> None:
        config_file = tmp_path / "appsettings.json"
        config_file.write_text(
            """
            {
              "service": {"name": "test", "version": "1.0"},
              "resources": {
                "r2": {
                  "account_id": "account-123",
                  "access_key": "ak",
                  "secret_key": "sk"
                }
              }
            }
            """,
            encoding="utf-8",
        )

        settings = load_config(config_dir=tmp_path)
        assert settings.resources.r2 is not None
        assert settings.resources.r2.resolved_endpoint == "account-123.r2.cloudflarestorage.com"
        assert settings.resources.r2.secure is True
        assert settings.resources.r2.region == "auto"

    def test_r2_requires_endpoint_or_account_id(self, tmp_path: Path) -> None:
        config_file = tmp_path / "appsettings.json"
        config_file.write_text(
            """
            {
              "service": {"name": "test", "version": "1.0"},
              "resources": {
                "r2": {
                  "access_key": "ak",
                  "secret_key": "sk"
                }
              }
            }
            """,
            encoding="utf-8",
        )

        with pytest.raises(ConfigValidationError) as exc_info:
            load_config(config_dir=tmp_path)

        assert "resources -> r2" in str(exc_info.value)
