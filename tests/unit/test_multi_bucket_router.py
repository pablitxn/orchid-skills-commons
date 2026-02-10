"""Tests for MultiBucketBlobRouter."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import Mock

import pytest

from orchid_commons.blob import MultiBucketBlobRouter
from orchid_commons.config.resources import MultiBucketSettings


class FakeS3Error(Exception):
    """Simple S3-like exception used for classification tests."""

    def __init__(self, code: str, status: int) -> None:
        self.code = code
        self.status = status
        super().__init__(f"{code} ({status})")


class FakeObject:
    """Fake object returned by list_objects."""

    def __init__(self, name: str) -> None:
        self.object_name = name


def make_client() -> Mock:
    """Build a MinIO client mock with all required methods."""
    client = Mock()
    client.put_object = Mock()
    client.get_object = Mock()
    client.stat_object = Mock()
    client.remove_object = Mock()
    client.presigned_get_object = Mock()
    client.presigned_put_object = Mock()
    client.bucket_exists = Mock(return_value=True)
    client.make_bucket = Mock()
    client.list_objects = Mock(return_value=[])
    return client


def make_settings(
    buckets: dict[str, str] | None = None,
) -> MultiBucketSettings:
    """Build test settings."""
    return MultiBucketSettings(
        endpoint="localhost:9000",
        access_key="minioadmin",
        secret_key="minioadmin",
        buckets=buckets or {"videos": "prod-videos", "chunks": "prod-chunks"},
    )


class TestMultiBucketBlobRouter:
    def test_aliases_returns_configured_aliases(self) -> None:
        client = make_client()
        settings = make_settings()
        router = MultiBucketBlobRouter(client=client, settings=settings)

        assert set(router.aliases) == {"videos", "chunks"}

    def test_get_bucket_resolves_alias(self) -> None:
        client = make_client()
        settings = make_settings()
        router = MultiBucketBlobRouter(client=client, settings=settings)

        assert router.get_bucket("videos") == "prod-videos"
        assert router.get_bucket("chunks") == "prod-chunks"

    def test_get_bucket_raises_for_unknown_alias(self) -> None:
        client = make_client()
        settings = make_settings()
        router = MultiBucketBlobRouter(client=client, settings=settings)

        with pytest.raises(KeyError, match="Unknown bucket alias"):
            router.get_bucket("unknown")

    def test_get_storage_returns_storage_for_alias(self) -> None:
        client = make_client()
        settings = make_settings()
        router = MultiBucketBlobRouter(client=client, settings=settings)

        storage = router.get_storage("videos")
        assert storage.bucket == "prod-videos"

    def test_get_storage_raises_for_unknown_alias(self) -> None:
        client = make_client()
        settings = make_settings()
        router = MultiBucketBlobRouter(client=client, settings=settings)

        with pytest.raises(KeyError, match="Unknown bucket alias"):
            router.get_storage("unknown")

    async def test_upload_routes_to_correct_bucket(self) -> None:
        client = make_client()
        settings = make_settings()
        router = MultiBucketBlobRouter(client=client, settings=settings)

        await router.upload("videos", "clip.mp4", b"video data")

        args, _ = client.put_object.call_args
        assert args[0] == "prod-videos"
        assert args[1] == "clip.mp4"

    async def test_upload_with_metadata_and_content_type(self) -> None:
        client = make_client()
        settings = make_settings()
        router = MultiBucketBlobRouter(client=client, settings=settings)

        await router.upload(
            "videos",
            "clip.mp4",
            b"video data",
            content_type="video/mp4",
            metadata={"duration": "120"},
        )

        _, kwargs = client.put_object.call_args
        assert kwargs["content_type"] == "video/mp4"
        assert kwargs["metadata"] == {"duration": "120"}

    async def test_download_routes_to_correct_bucket(self) -> None:
        client = make_client()
        response = Mock()
        response.read.return_value = b"chunk data"
        response.headers = {"Content-Type": "video/mp2t"}
        client.get_object.return_value = response
        settings = make_settings()
        router = MultiBucketBlobRouter(client=client, settings=settings)

        result = await router.download("chunks", "segment-001.ts")

        assert result.data == b"chunk data"
        assert result.content_type == "video/mp2t"
        client.get_object.assert_called_once_with("prod-chunks", "segment-001.ts")

    async def test_exists_routes_to_correct_bucket(self) -> None:
        client = make_client()
        settings = make_settings()
        router = MultiBucketBlobRouter(client=client, settings=settings)

        result = await router.exists("videos", "clip.mp4")

        assert result is True
        client.stat_object.assert_called_once_with("prod-videos", "clip.mp4")

    async def test_exists_returns_false_for_missing_object(self) -> None:
        client = make_client()
        client.stat_object.side_effect = FakeS3Error("NoSuchKey", 404)
        settings = make_settings()
        router = MultiBucketBlobRouter(client=client, settings=settings)

        result = await router.exists("videos", "missing.mp4")

        assert result is False

    async def test_delete_routes_to_correct_bucket(self) -> None:
        client = make_client()
        settings = make_settings()
        router = MultiBucketBlobRouter(client=client, settings=settings)

        await router.delete("chunks", "old-segment.ts")

        client.remove_object.assert_called_once_with("prod-chunks", "old-segment.ts")

    async def test_presign_get_routes_to_correct_bucket(self) -> None:
        client = make_client()
        client.presigned_get_object.return_value = "https://example.com/signed"
        settings = make_settings()
        router = MultiBucketBlobRouter(client=client, settings=settings)

        url = await router.presign("videos", "clip.mp4")

        assert url == "https://example.com/signed"
        client.presigned_get_object.assert_called_once()
        args, _kwargs = client.presigned_get_object.call_args
        assert args[0] == "prod-videos"
        assert args[1] == "clip.mp4"

    async def test_presign_put_routes_to_correct_bucket(self) -> None:
        client = make_client()
        client.presigned_put_object.return_value = "https://example.com/upload"
        settings = make_settings()
        router = MultiBucketBlobRouter(client=client, settings=settings)

        url = await router.presign(
            "videos",
            "new-clip.mp4",
            method="PUT",
            expires=timedelta(minutes=30),
        )

        assert url == "https://example.com/upload"
        client.presigned_put_object.assert_called_once()

    async def test_list_objects_routes_to_correct_bucket(self) -> None:
        client = make_client()
        client.list_objects.return_value = [
            FakeObject("segment-001.ts"),
            FakeObject("segment-002.ts"),
        ]
        settings = make_settings()
        router = MultiBucketBlobRouter(client=client, settings=settings)

        keys = await router.list_objects("chunks", prefix="segment-")

        assert keys == ["segment-001.ts", "segment-002.ts"]
        client.list_objects.assert_called_once_with(
            "prod-chunks", prefix="segment-", recursive=True
        )

    async def test_ensure_buckets_creates_all_buckets(self) -> None:
        client = make_client()
        client.bucket_exists.return_value = False
        settings = make_settings(
            buckets={"videos": "prod-videos", "chunks": "prod-chunks", "frames": "prod-frames"}
        )
        router = MultiBucketBlobRouter(client=client, settings=settings)

        results = await router.ensure_buckets(create_if_missing=True)

        assert len(results) == 3
        assert client.make_bucket.call_count == 3

    async def test_ensure_buckets_returns_bucket_info(self) -> None:
        client = make_client()
        # First bucket exists, second doesn't
        client.bucket_exists.side_effect = [True, False, False]
        settings = make_settings(
            buckets={"videos": "prod-videos", "chunks": "prod-chunks"}
        )
        router = MultiBucketBlobRouter(client=client, settings=settings)

        results = await router.ensure_buckets(create_if_missing=True)

        videos_info = next(r for r in results if r.alias == "videos")
        chunks_info = next(r for r in results if r.alias == "chunks")

        assert videos_info.exists is True
        assert videos_info.created is False
        assert chunks_info.exists is True
        assert chunks_info.created is True

    async def test_health_check_reports_all_buckets_healthy(self) -> None:
        client = make_client()
        client.bucket_exists.return_value = True
        settings = make_settings()
        router = MultiBucketBlobRouter(client=client, settings=settings)

        status = await router.health_check()

        assert status.healthy is True
        assert status.message == "All buckets are reachable"
        assert status.details is not None
        assert status.details["buckets"] == {"videos": True, "chunks": True}

    async def test_health_check_reports_unhealthy_bucket(self) -> None:
        client = make_client()
        client.bucket_exists.side_effect = [True, False]
        settings = make_settings()
        router = MultiBucketBlobRouter(client=client, settings=settings)

        status = await router.health_check()

        assert status.healthy is False
        assert "chunks" in (status.message or "")

    async def test_health_check_handles_exceptions(self) -> None:
        client = make_client()
        client.bucket_exists.side_effect = ConnectionError("network down")
        settings = make_settings()
        router = MultiBucketBlobRouter(client=client, settings=settings)

        status = await router.health_check()

        assert status.healthy is False
        assert status.details is not None
        assert "error_videos" in status.details


class TestMultiBucketSettings:
    def test_requires_at_least_one_bucket(self) -> None:
        with pytest.raises(ValueError, match="at least one bucket"):
            MultiBucketSettings(
                endpoint="localhost:9000",
                access_key="minioadmin",
                secret_key="minioadmin",
                buckets={},
            )

    def test_get_bucket_returns_physical_name(self) -> None:
        settings = make_settings()
        assert settings.get_bucket("videos") == "prod-videos"

    def test_get_bucket_raises_for_unknown_alias(self) -> None:
        settings = make_settings()
        with pytest.raises(KeyError, match="Unknown bucket alias"):
            settings.get_bucket("unknown")

    def test_to_s3_client_kwargs(self) -> None:
        settings = MultiBucketSettings(
            endpoint="localhost:9000",
            access_key="mykey",
            secret_key="mysecret",
            buckets={"default": "my-bucket"},
            secure=True,
            region="us-east-1",
        )

        kwargs = settings.to_s3_client_kwargs()

        assert kwargs["endpoint"] == "localhost:9000"
        assert kwargs["access_key"] == "mykey"
        assert kwargs["secret_key"] == "mysecret"
        assert kwargs["secure"] is True
        assert kwargs["region"] == "us-east-1"

    def test_presign_base_url_http(self) -> None:
        settings = make_settings()
        assert settings.presign_base_url() == "http://localhost:9000"

    def test_presign_base_url_https(self) -> None:
        settings = MultiBucketSettings(
            endpoint="s3.example.com",
            access_key="key",
            secret_key="secret",
            buckets={"default": "bucket"},
            secure=True,
        )
        assert settings.presign_base_url() == "https://s3.example.com"

    def test_local_dev_factory(self) -> None:
        settings = MultiBucketSettings.local_dev(
            buckets={"videos": "dev-videos", "chunks": "dev-chunks"}
        )

        assert settings.endpoint == "localhost:9000"
        assert settings.access_key == "minioadmin"
        assert settings.create_buckets_if_missing is True
        assert settings.buckets == {"videos": "dev-videos", "chunks": "dev-chunks"}
