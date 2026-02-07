"""End-to-end integration tests for MinIO/S3-compatible blob flows."""

from __future__ import annotations

from uuid import uuid4

import pytest

from orchid_commons.blob import BlobNotFoundError
from orchid_commons.blob.minio import create_minio_profile

pytestmark = pytest.mark.integration


async def test_minio_profile_roundtrip(minio_settings) -> None:
    profile = await create_minio_profile(minio_settings)
    key = f"integration/{uuid4().hex}.txt"
    payload = b"orchid-minio-integration"

    await profile.upload(key, payload, content_type="text/plain", metadata={"suite": "integration"})
    assert await profile.exists(key) is True

    downloaded = await profile.download(key)
    assert downloaded.key == key
    assert downloaded.data == payload
    assert downloaded.content_type == "text/plain"
    assert downloaded.metadata["suite"] == "integration"

    health = await profile.health_check()
    assert health.healthy is True

    await profile.delete(key)
    assert await profile.exists(key) is False
    await profile.close()


async def test_minio_missing_key_raises_typed_error(minio_settings) -> None:
    profile = await create_minio_profile(minio_settings)

    with pytest.raises(BlobNotFoundError):
        await profile.download(f"missing/{uuid4().hex}.txt")

    await profile.close()
