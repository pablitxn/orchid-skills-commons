"""Tests for blob abstractions and S3-compatible implementation."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import Mock

import pytest

from orchid_commons.blob import (
    BlobAuthError,
    BlobNotFoundError,
    BlobTransientError,
    S3BlobStorage,
)


class FakeS3Error(Exception):
    """Simple S3-like exception used for classification tests."""

    def __init__(self, code: str, status: int) -> None:
        self.code = code
        self.status = status
        super().__init__(f"{code} ({status})")


def make_client() -> Mock:
    """Build a S3 client mock with all required methods."""
    client = Mock()
    client.put_object = Mock()
    client.get_object = Mock()
    client.stat_object = Mock()
    client.remove_object = Mock()
    client.presigned_get_object = Mock()
    client.presigned_put_object = Mock()
    client.bucket_exists = Mock(return_value=True)
    return client


class TestS3BlobStorage:
    async def test_upload_passes_content_type_and_metadata(self) -> None:
        client = make_client()
        storage = S3BlobStorage(client=client, bucket="assets")

        await storage.upload(
            "reports/day-01.json",
            b'{"ok":true}',
            content_type="application/json",
            metadata={"team": "data"},
        )

        args, kwargs = client.put_object.call_args
        assert args[0] == "assets"
        assert args[1] == "reports/day-01.json"
        assert args[3] == len(b'{"ok":true}')

        payload_stream = args[2]
        payload_stream.seek(0)
        assert payload_stream.read() == b'{"ok":true}'

        assert kwargs["content_type"] == "application/json"
        assert kwargs["metadata"] == {"team": "data"}

    async def test_download_returns_payload_and_headers(self) -> None:
        client = make_client()
        response = Mock()
        response.read.return_value = b"hello world"
        response.headers = {
            "Content-Type": "text/plain",
            "x-amz-meta-origin": "unit-test",
        }
        client.get_object.return_value = response
        storage = S3BlobStorage(client=client, bucket="assets")

        result = await storage.download("greeting.txt")

        assert result.key == "greeting.txt"
        assert result.data == b"hello world"
        assert result.content_type == "text/plain"
        assert result.metadata == {"origin": "unit-test"}
        response.close.assert_called_once()
        response.release_conn.assert_called_once()

    async def test_exists_returns_false_for_not_found(self) -> None:
        client = make_client()
        client.stat_object.side_effect = FakeS3Error("NoSuchKey", 404)
        storage = S3BlobStorage(client=client, bucket="assets")

        assert await storage.exists("missing.txt") is False

    async def test_delete_is_idempotent_for_missing_object(self) -> None:
        client = make_client()
        client.remove_object.side_effect = FakeS3Error("NoSuchKey", 404)
        storage = S3BlobStorage(client=client, bucket="assets")

        await storage.delete("missing.txt")
        client.remove_object.assert_called_once_with("assets", "missing.txt")

    async def test_presign_routes_to_get_and_put(self) -> None:
        client = make_client()
        client.presigned_get_object.return_value = "https://example/get"
        client.presigned_put_object.return_value = "https://example/put"
        storage = S3BlobStorage(client=client, bucket="assets")

        get_url = await storage.presign("a.txt")
        put_url = await storage.presign(
            "b.txt",
            method="PUT",
            expires=timedelta(seconds=30),
        )

        assert get_url == "https://example/get"
        assert put_url == "https://example/put"
        client.presigned_get_object.assert_called_once()
        client.presigned_put_object.assert_called_once()

    async def test_download_not_found_maps_to_typed_error(self) -> None:
        client = make_client()
        client.get_object.side_effect = FakeS3Error("NoSuchKey", 404)
        storage = S3BlobStorage(client=client, bucket="assets")

        with pytest.raises(BlobNotFoundError):
            await storage.download("missing.txt")

    async def test_upload_auth_error_maps_to_typed_error(self) -> None:
        client = make_client()
        client.put_object.side_effect = FakeS3Error("AccessDenied", 403)
        storage = S3BlobStorage(client=client, bucket="assets")

        with pytest.raises(BlobAuthError):
            await storage.upload("private.txt", b"secret")

    async def test_presign_transient_error_maps_to_typed_error(self) -> None:
        client = make_client()
        client.presigned_get_object.side_effect = FakeS3Error("SlowDown", 503)
        storage = S3BlobStorage(client=client, bucket="assets")

        with pytest.raises(BlobTransientError):
            await storage.presign("retry.txt")

    async def test_health_check_reports_unhealthy_on_exception(self) -> None:
        client = make_client()
        client.bucket_exists.side_effect = ConnectionError("network down")
        storage = S3BlobStorage(client=client, bucket="assets")

        status = await storage.health_check()

        assert status.healthy is False
        assert status.latency_ms >= 0.0
        assert status.details == {"error_type": "ConnectionError"}

    async def test_health_check_reports_missing_bucket(self) -> None:
        client = make_client()
        client.bucket_exists.return_value = False
        storage = S3BlobStorage(client=client, bucket="assets")

        status = await storage.health_check()

        assert status.healthy is False
        assert status.latency_ms >= 0.0
        assert "does not exist" in (status.message or "")


def test_constructor_validates_bucket() -> None:
    client: Any = make_client()
    with pytest.raises(ValueError):
        S3BlobStorage(client=client, bucket=" ")

