"""Tests for Cloudflare R2 profile over S3-compatible implementation."""

from __future__ import annotations

import sys
from types import SimpleNamespace

from orchid_commons.blob.r2 import create_r2_profile
from orchid_commons.blob.s3 import S3BlobStorage
from orchid_commons.settings import MinioSettings, R2Settings


def _install_fake_minio(monkeypatch) -> list[object]:
    created: list[object] = []

    class FakeMinioClient:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs
            created.append(self)

        def bucket_exists(self, bucket_name: str) -> bool:
            return True

        def make_bucket(self, bucket_name: str, location: str | None = None) -> None:
            return None

    monkeypatch.setitem(sys.modules, "minio", SimpleNamespace(Minio=FakeMinioClient))
    return created


class TestS3Constructors:
    def test_from_minio_settings_uses_s3_kwargs(self, monkeypatch) -> None:
        created = _install_fake_minio(monkeypatch)
        settings = MinioSettings(
            endpoint="localhost:9000",
            access_key="ak",
            secret_key="sk",
            bucket="assets",
            secure=False,
            region=None,
        )

        storage = S3BlobStorage.from_minio_settings(settings)

        assert storage.bucket == "assets"
        assert created
        assert created[0].kwargs == settings.to_s3_client_kwargs()

    def test_from_r2_settings_uses_resolved_endpoint(self, monkeypatch) -> None:
        created = _install_fake_minio(monkeypatch)
        settings = R2Settings(
            access_key="ak",
            secret_key="sk",
            account_id="account-123",
            bucket="assets",
        )

        storage = S3BlobStorage.from_r2_settings(settings)

        assert storage.bucket == "assets"
        assert created
        assert created[0].kwargs == settings.to_s3_client_kwargs()


class TestR2Profile:
    async def test_create_r2_profile_reports_cloudflare_provider(self, monkeypatch) -> None:
        created = _install_fake_minio(monkeypatch)
        settings = R2Settings(
            access_key="ak",
            secret_key="sk",
            account_id="account-123",
            bucket="assets",
        )

        profile = await create_r2_profile(settings)
        health = await profile.health_check()

        assert created
        assert created[0].kwargs == settings.to_s3_client_kwargs()
        assert health.healthy is True
        assert health.details is not None
        assert health.details["provider"] == "cloudflare-r2"
        assert "Cloudflare R2" in (health.message or "")
