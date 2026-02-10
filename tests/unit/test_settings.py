"""Tests for settings conversion helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from orchid_commons.config import load_config
from orchid_commons.config.models import (
    MinioSettings,
    MongoDbSettings,
    QdrantSettings,
    R2Settings,
    RabbitMqSettings,
    RedisSettings,
    ResourceSettings,
)

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "config"


class TestResourceSettings:
    def test_resources_maps_postgres(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4317")
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/app")

        app_settings = load_config(config_dir=FIXTURES_DIR, env="production")
        resources = app_settings.resources

        assert resources.postgres is not None
        assert resources.postgres.dsn.get_secret_value() == "postgresql://user:pass@localhost:5432/app"
        assert resources.postgres.min_pool_size == 1
        assert resources.postgres.max_pool_size == 20
        assert resources.postgres.command_timeout_seconds == 60.0

    def test_resources_maps_sqlite(self) -> None:
        app_settings = load_config(config_dir=FIXTURES_DIR, env="development")
        resources = app_settings.resources

        assert resources.sqlite is not None
        assert str(resources.sqlite.db_path) == "data/base.db"

    def test_resources_maps_r2(self, tmp_path: Path) -> None:
        config_file = tmp_path / "appsettings.json"
        config_file.write_text(
            """
            {
              "service": {"name": "svc", "version": "1.0"},
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

        app_settings = load_config(config_dir=tmp_path)
        resources = app_settings.resources

        assert resources.r2 is not None
        assert resources.r2.resolved_endpoint == "account-123.r2.cloudflarestorage.com"

    def test_resources_maps_redis_and_mongodb(self, tmp_path: Path) -> None:
        config_file = tmp_path / "appsettings.json"
        config_file.write_text(
            """
            {
              "service": {"name": "svc", "version": "1.0"},
              "resources": {
                "redis": {
                  "url": "redis://localhost:6379/1",
                  "key_prefix": "svc",
                  "default_ttl_seconds": 60
                },
                "mongodb": {
                  "uri": "mongodb://localhost:27017",
                  "database": "orchid",
                  "app_name": "orchid-tests"
                }
              }
            }
            """,
            encoding="utf-8",
        )

        app_settings = load_config(config_dir=tmp_path)
        resources = app_settings.resources

        assert resources.redis is not None
        assert resources.redis.url.get_secret_value() == "redis://localhost:6379/1"
        assert resources.redis.key_prefix == "svc"
        assert resources.redis.default_ttl_seconds == 60
        assert resources.mongodb is not None
        assert resources.mongodb.uri.get_secret_value() == "mongodb://localhost:27017"
        assert resources.mongodb.database == "orchid"
        assert resources.mongodb.app_name == "orchid-tests"

    def test_resources_maps_rabbitmq_and_qdrant(self, tmp_path: Path) -> None:
        config_file = tmp_path / "appsettings.json"
        config_file.write_text(
            """
            {
              "service": {"name": "svc", "version": "1.0"},
              "resources": {
                "rabbitmq": {
                  "url": "amqp://guest:guest@localhost:5672/",
                  "prefetch_count": 20
                },
                "qdrant": {
                  "host": "localhost",
                  "port": 6333,
                  "collection_prefix": "orchid"
                }
              }
            }
            """,
            encoding="utf-8",
        )

        app_settings = load_config(config_dir=tmp_path)
        resources = app_settings.resources

        assert resources.rabbitmq is not None
        assert resources.rabbitmq.url.get_secret_value() == "amqp://guest:guest@localhost:5672/"
        assert resources.rabbitmq.prefetch_count == 20
        assert resources.qdrant is not None
        assert resources.qdrant.host == "localhost"
        assert resources.qdrant.port == 6333
        assert resources.qdrant.collection_prefix == "orchid"


class TestR2Settings:
    def test_requires_endpoint_or_account(self) -> None:
        with pytest.raises(ValueError):
            R2Settings(access_key="ak", secret_key="sk")

    def test_resolves_endpoint_from_account(self) -> None:
        settings = R2Settings(access_key="ak", secret_key="sk", account_id="account-123")

        assert settings.resolved_endpoint == "account-123.r2.cloudflarestorage.com"
        assert settings.to_s3_client_kwargs()["endpoint"] == settings.resolved_endpoint
        assert settings.presign_base_url() == "https://account-123.r2.cloudflarestorage.com"

    def test_to_minio_settings_keeps_s3_contract(self) -> None:
        settings = R2Settings(
            access_key="ak",
            secret_key="sk",
            endpoint="r2.example.com",
            bucket="assets",
            secure=False,
            region="wnam",
        )

        minio_settings = settings.to_minio_settings()
        assert isinstance(minio_settings, MinioSettings)
        assert minio_settings.bucket == "assets"
        assert minio_settings.to_s3_client_kwargs() == settings.to_s3_client_kwargs()


class TestResourceSettingsFromEnv:
    def test_loads_r2_from_env_with_account(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_R2_ACCOUNT_ID", "account-123")
        monkeypatch.setenv("TEST_R2_ACCESS_KEY", "ak")
        monkeypatch.setenv("TEST_R2_SECRET_KEY", "sk")

        settings = ResourceSettings.from_env(prefix="TEST_")

        assert settings.r2 is not None
        assert settings.r2.resolved_endpoint == "account-123.r2.cloudflarestorage.com"
        assert settings.r2.secure is True
        assert settings.r2.region == "auto"

    def test_loads_r2_from_env_with_endpoint_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TEST_R2_ENDPOINT", "custom.r2.example.com")
        monkeypatch.setenv("TEST_R2_ACCESS_KEY", "ak")
        monkeypatch.setenv("TEST_R2_SECRET_KEY", "sk")
        monkeypatch.setenv("TEST_R2_SECURE", "false")
        monkeypatch.setenv("TEST_R2_REGION", "eu")

        settings = ResourceSettings.from_env(prefix="TEST_")

        assert settings.r2 is not None
        assert settings.r2.resolved_endpoint == "custom.r2.example.com"
        assert settings.r2.secure is False
        assert settings.r2.region == "eu"

    def test_loads_minio_bucket_bootstrap_flags(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_MINIO_ENDPOINT", "localhost:9000")
        monkeypatch.setenv("TEST_MINIO_ACCESS_KEY", "minioadmin")
        monkeypatch.setenv("TEST_MINIO_SECRET_KEY", "minioadmin")
        monkeypatch.setenv("TEST_MINIO_BUCKET", "assets")
        monkeypatch.setenv("TEST_MINIO_CREATE_BUCKET_IF_MISSING", "true")

        settings = ResourceSettings.from_env(prefix="TEST_")

        assert settings.minio is not None
        assert settings.minio.endpoint == "localhost:9000"
        assert settings.minio.bucket == "assets"
        assert settings.minio.create_bucket_if_missing is True

    def test_loads_redis_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_REDIS_URL", "redis://localhost:6379/2")
        monkeypatch.setenv("TEST_REDIS_KEY_PREFIX", "orchid")
        monkeypatch.setenv("TEST_REDIS_DEFAULT_TTL_SECONDS", "45")
        monkeypatch.setenv("TEST_REDIS_DECODE_RESPONSES", "false")

        settings = ResourceSettings.from_env(prefix="TEST_")

        assert settings.redis is not None
        assert isinstance(settings.redis, RedisSettings)
        assert settings.redis.url.get_secret_value() == "redis://localhost:6379/2"
        assert settings.redis.key_prefix == "orchid"
        assert settings.redis.default_ttl_seconds == 45
        assert settings.redis.decode_responses is False

    def test_loads_mongodb_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_MONGODB_URI", "mongodb://localhost:27017")
        monkeypatch.setenv("TEST_MONGODB_DATABASE", "orchid")
        monkeypatch.setenv("TEST_MONGODB_APP_NAME", "orchid-tests")

        settings = ResourceSettings.from_env(prefix="TEST_")

        assert settings.mongodb is not None
        assert isinstance(settings.mongodb, MongoDbSettings)
        assert settings.mongodb.uri.get_secret_value() == "mongodb://localhost:27017"
        assert settings.mongodb.database == "orchid"
        assert settings.mongodb.app_name == "orchid-tests"

    def test_loads_rabbitmq_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
        monkeypatch.setenv("TEST_RABBITMQ_PREFETCH_COUNT", "10")
        monkeypatch.setenv("TEST_RABBITMQ_PUBLISHER_CONFIRMS", "false")

        settings = ResourceSettings.from_env(prefix="TEST_")

        assert settings.rabbitmq is not None
        assert isinstance(settings.rabbitmq, RabbitMqSettings)
        assert settings.rabbitmq.url.get_secret_value() == "amqp://guest:guest@localhost:5672/"
        assert settings.rabbitmq.prefetch_count == 10
        assert settings.rabbitmq.publisher_confirms is False

    def test_loads_qdrant_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_QDRANT_HOST", "qdrant.local")
        monkeypatch.setenv("TEST_QDRANT_PORT", "6333")
        monkeypatch.setenv("TEST_QDRANT_COLLECTION_PREFIX", "orchid")

        settings = ResourceSettings.from_env(prefix="TEST_")

        assert settings.qdrant is not None
        assert isinstance(settings.qdrant, QdrantSettings)
        assert settings.qdrant.host == "qdrant.local"
        assert settings.qdrant.port == 6333
        assert settings.qdrant.collection_prefix == "orchid"


class TestMinioSettings:
    def test_local_dev_defaults(self) -> None:
        settings = MinioSettings.local_dev()

        assert settings.endpoint == "localhost:9000"
        assert settings.access_key.get_secret_value() == "minioadmin"
        assert settings.secret_key.get_secret_value() == "minioadmin"
        assert settings.bucket == "orchid-dev"
        assert settings.create_bucket_if_missing is True
        assert settings.secure is False
