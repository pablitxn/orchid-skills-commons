"""End-to-end integration tests for MultiBucketBlobRouter."""

from __future__ import annotations

from uuid import uuid4

import pytest

from orchid_commons.blob import BlobNotFoundError, MultiBucketBlobRouter
from orchid_commons.blob.router import create_multi_bucket_router

pytestmark = pytest.mark.integration


async def test_multi_bucket_router_roundtrip(multi_bucket_settings) -> None:
    """Test complete upload/download/delete flow across multiple buckets."""
    router = await create_multi_bucket_router(multi_bucket_settings)

    # Test videos bucket
    video_key = f"integration/{uuid4().hex}.mp4"
    video_payload = b"fake-video-content"
    await router.upload(
        "videos",
        video_key,
        video_payload,
        content_type="video/mp4",
        metadata={"duration": "120"},
    )
    assert await router.exists("videos", video_key) is True

    downloaded = await router.download("videos", video_key)
    assert downloaded.data == video_payload
    assert downloaded.content_type == "video/mp4"
    assert downloaded.metadata["duration"] == "120"

    # Test chunks bucket
    chunk_key = f"integration/{uuid4().hex}.ts"
    chunk_payload = b"fake-chunk-content"
    await router.upload("chunks", chunk_key, chunk_payload, content_type="video/mp2t")
    assert await router.exists("chunks", chunk_key) is True

    # Test frames bucket
    frame_key = f"integration/{uuid4().hex}.jpg"
    frame_payload = b"fake-frame-content"
    await router.upload("frames", frame_key, frame_payload, content_type="image/jpeg")
    assert await router.exists("frames", frame_key) is True

    # Cleanup
    await router.delete("videos", video_key)
    await router.delete("chunks", chunk_key)
    await router.delete("frames", frame_key)

    assert await router.exists("videos", video_key) is False
    assert await router.exists("chunks", chunk_key) is False
    assert await router.exists("frames", frame_key) is False

    await router.close()


async def test_multi_bucket_health_check(multi_bucket_settings) -> None:
    """Test health check reports status for all buckets."""
    router = await create_multi_bucket_router(multi_bucket_settings)

    health = await router.health_check()

    assert health.healthy is True
    assert health.message == "All buckets are reachable"
    assert health.details is not None
    assert "buckets" in health.details
    assert health.details["buckets"]["videos"] is True
    assert health.details["buckets"]["chunks"] is True
    assert health.details["buckets"]["frames"] is True

    await router.close()


async def test_multi_bucket_list_objects(multi_bucket_settings) -> None:
    """Test listing objects in a specific bucket."""
    router = await create_multi_bucket_router(multi_bucket_settings)

    prefix = f"list-test/{uuid4().hex}/"
    keys = [f"{prefix}file-{i}.txt" for i in range(3)]

    # Upload test files
    for key in keys:
        await router.upload("videos", key, b"content")

    # List objects
    listed = await router.list_objects("videos", prefix=prefix)
    assert set(listed) == set(keys)

    # Cleanup
    for key in keys:
        await router.delete("videos", key)

    await router.close()


async def test_multi_bucket_presign(multi_bucket_settings) -> None:
    """Test generating presigned URLs for different buckets."""
    router = await create_multi_bucket_router(multi_bucket_settings)

    key = f"presign/{uuid4().hex}.txt"
    await router.upload("videos", key, b"presign content")

    # Generate presigned GET URL
    get_url = await router.presign("videos", key, method="GET")
    assert multi_bucket_settings.endpoint in get_url
    assert "videos" in get_url or multi_bucket_settings.buckets["videos"] in get_url

    # Generate presigned PUT URL for chunks
    put_url = await router.presign("chunks", "new-file.txt", method="PUT")
    assert multi_bucket_settings.endpoint in put_url

    # Cleanup
    await router.delete("videos", key)
    await router.close()


async def test_multi_bucket_missing_key_raises_typed_error(multi_bucket_settings) -> None:
    """Test that downloading a missing key raises BlobNotFoundError."""
    router = await create_multi_bucket_router(multi_bucket_settings)

    with pytest.raises(BlobNotFoundError):
        await router.download("videos", f"missing/{uuid4().hex}.txt")

    await router.close()


async def test_multi_bucket_bucket_isolation(multi_bucket_settings) -> None:
    """Test that objects in one bucket are not visible in another."""
    router = await create_multi_bucket_router(multi_bucket_settings)

    key = f"isolation/{uuid4().hex}.txt"
    await router.upload("videos", key, b"video content")

    # Object should exist in videos
    assert await router.exists("videos", key) is True

    # Object should not exist in chunks or frames
    assert await router.exists("chunks", key) is False
    assert await router.exists("frames", key) is False

    await router.delete("videos", key)
    await router.close()


async def test_multi_bucket_ensure_buckets(multi_bucket_settings) -> None:
    """Test that ensure_buckets reports status for all buckets."""
    router = await create_multi_bucket_router(multi_bucket_settings)

    # Buckets should already exist from router creation
    results = await router.ensure_buckets()

    assert len(results) == 3
    for info in results:
        assert info.alias in {"videos", "chunks", "frames"}
        assert info.exists is True

    await router.close()
