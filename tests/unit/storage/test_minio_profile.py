"""Tests for MinIO blob profile helpers and factory."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from orchid_commons import ResourceManager
from orchid_commons.blob.minio import (
    MinioProfile,
    bootstrap_bucket,
    create_minio_profile,
    minio_local_dev_settings,
    register_minio_factory,
)
from orchid_commons.config.resources import MinioSettings, ResourceSettings


def make_minio_client(*, bucket_exists: bool = True) -> Mock:
    client = Mock()
    client.put_object = Mock()
    client.get_object = Mock()
    client.stat_object = Mock()
    client.remove_object = Mock()
    client.presigned_get_object = Mock()
    client.presigned_put_object = Mock()
    client.bucket_exists = Mock(return_value=bucket_exists)
    client.make_bucket = Mock()
    return client


class TestBucketBootstrap:
    async def test_bootstrap_noop_when_bucket_exists(self) -> None:
        client = make_minio_client(bucket_exists=True)

        result = await bootstrap_bucket(client, "assets", create_if_missing=True)

        assert result.exists is True
        assert result.created is False
        client.make_bucket.assert_not_called()

    async def test_bootstrap_reports_missing_without_create(self) -> None:
        client = make_minio_client(bucket_exists=False)

        result = await bootstrap_bucket(client, "assets", create_if_missing=False)

        assert result.exists is False
        assert result.created is False
        client.make_bucket.assert_not_called()

    async def test_bootstrap_creates_bucket_when_enabled(self) -> None:
        client = make_minio_client(bucket_exists=False)

        result = await bootstrap_bucket(
            client,
            "assets",
            create_if_missing=True,
            region="us-east-1",
        )

        assert result.exists is False
        assert result.created is True
        client.make_bucket.assert_called_once_with("assets", location="us-east-1")

    async def test_bootstrap_handles_race_when_bucket_created_elsewhere(self) -> None:
        client = make_minio_client(bucket_exists=False)
        client.bucket_exists.side_effect = [False, True]
        client.make_bucket.side_effect = RuntimeError("bucket already exists")

        result = await bootstrap_bucket(client, "assets", create_if_missing=True)

        assert result.exists is True
        assert result.created is False


class TestMinioProfile:
    async def test_ensure_bucket_uses_settings_default(self) -> None:
        settings = MinioSettings(
            endpoint="localhost:9000",
            access_key="minioadmin",
            secret_key="minioadmin",
            bucket="assets",
            create_bucket_if_missing=True,
        )
        client = make_minio_client(bucket_exists=False)
        profile = MinioProfile(client=client, settings=settings)

        result = await profile.ensure_bucket()

        assert result.created is True
        client.make_bucket.assert_called_once_with("assets", location=None)

    async def test_ensure_bucket_override_disables_creation(self) -> None:
        settings = MinioSettings(
            endpoint="localhost:9000",
            access_key="minioadmin",
            secret_key="minioadmin",
            bucket="assets",
            create_bucket_if_missing=True,
        )
        client = make_minio_client(bucket_exists=False)
        profile = MinioProfile(client=client, settings=settings)

        result = await profile.ensure_bucket(create_if_missing=False)

        assert result.created is False
        client.make_bucket.assert_not_called()

    async def test_health_check_reports_endpoint_and_bucket(self) -> None:
        settings = MinioSettings(
            endpoint="localhost:9000",
            access_key="minioadmin",
            secret_key="minioadmin",
            bucket="assets",
        )
        client = make_minio_client(bucket_exists=True)
        profile = MinioProfile(client=client, settings=settings)

        status = await profile.health_check()

        assert status.healthy is True
        assert status.details == {
            "provider": "minio",
            "endpoint": "localhost:9000",
            "bucket": "assets",
        }

    async def test_health_check_unhealthy_when_bucket_missing(self) -> None:
        settings = MinioSettings(
            endpoint="localhost:9000",
            access_key="minioadmin",
            secret_key="minioadmin",
            bucket="assets",
        )
        client = make_minio_client(bucket_exists=False)
        profile = MinioProfile(client=client, settings=settings)

        status = await profile.health_check()

        assert status.healthy is False
        assert "does not exist" in (status.message or "")

    async def test_health_check_handles_client_errors(self) -> None:
        settings = MinioSettings(
            endpoint="localhost:9000",
            access_key="minioadmin",
            secret_key="minioadmin",
            bucket="assets",
        )
        client = make_minio_client(bucket_exists=True)
        client.bucket_exists.side_effect = ConnectionError("network down")
        profile = MinioProfile(client=client, settings=settings)

        status = await profile.health_check()

        assert status.healthy is False
        assert status.details == {
            "provider": "minio",
            "endpoint": "localhost:9000",
            "bucket": "assets",
            "error_type": "ConnectionError",
        }


class TestMinioFactory:
    async def test_create_minio_profile_bootstraps_bucket(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        settings = MinioSettings(
            endpoint="localhost:9000",
            access_key="minioadmin",
            secret_key="minioadmin",
            bucket="assets",
            create_bucket_if_missing=True,
        )
        client = make_minio_client(bucket_exists=False)

        monkeypatch.setattr("orchid_commons.blob.minio._build_minio_client", lambda _: client)

        profile = await create_minio_profile(settings)

        assert isinstance(profile, MinioProfile)
        client.make_bucket.assert_called_once_with("assets", location=None)

    async def test_resource_manager_bootstraps_builtin_minio(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        settings = ResourceSettings(
            minio=MinioSettings(
                endpoint="localhost:9000",
                access_key="minioadmin",
                secret_key="minioadmin",
                bucket="assets",
            )
        )
        client = make_minio_client(bucket_exists=True)
        monkeypatch.setattr("orchid_commons.blob.minio._build_minio_client", lambda _: client)

        manager = ResourceManager()
        await manager.startup(settings, required=["minio"])

        resource = manager.get("minio")
        assert isinstance(resource, MinioProfile)

        await manager.close_all()

    def test_register_minio_factory_custom_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, object] = {}

        def fake_register_factory(name: str, settings_attr: str, factory: object) -> None:
            captured["name"] = name
            captured["settings_attr"] = settings_attr
            captured["factory"] = factory

        monkeypatch.setattr("orchid_commons.blob.minio.register_factory", fake_register_factory)

        register_minio_factory("blob_minio")

        assert captured["name"] == "blob_minio"
        assert captured["settings_attr"] == "minio"


def test_local_dev_settings_defaults() -> None:
    with pytest.warns(UserWarning, match="local development only"):
        settings = minio_local_dev_settings(access_key="minioadmin", secret_key="minioadmin")

    assert settings.endpoint == "localhost:9000"
    assert settings.access_key.get_secret_value() == "minioadmin"
    assert settings.secret_key.get_secret_value() == "minioadmin"
    assert settings.bucket == "orchid-dev"
    assert settings.create_bucket_if_missing is True
    assert settings.secure is False


def test_local_dev_settings_raises_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORCHID_ENV", "production")
    with pytest.raises(RuntimeError, match="must not be used in production"):
        minio_local_dev_settings(access_key="ak", secret_key="sk")


def test_local_dev_settings_emits_warning() -> None:
    with pytest.warns(UserWarning, match="local development only"):
        minio_local_dev_settings(access_key="ak", secret_key="sk")
