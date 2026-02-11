"""End-to-end integration tests for the Redis cache provider."""

from __future__ import annotations

import pytest

from orchid_commons.db import create_redis_cache

pytestmark = pytest.mark.integration


async def test_redis_roundtrip(redis_settings) -> None:
    cache = await create_redis_cache(redis_settings)
    try:
        await cache.set("test_key", "hello")
        value = await cache.get("test_key")
        assert value == "hello"

        exists = await cache.exists("test_key")
        assert exists is True

        deleted = await cache.delete("test_key")
        assert deleted == 1

        exists_after = await cache.exists("test_key")
        assert exists_after is False

        assert (await cache.health_check()).healthy is True
    finally:
        await cache.close()


async def test_redis_ttl(redis_settings) -> None:
    cache = await create_redis_cache(redis_settings)
    try:
        await cache.set("ttl_key", "expires", ttl_seconds=1)
        value = await cache.get("ttl_key")
        assert value == "expires"

        import asyncio

        await asyncio.sleep(1.5)

        value_after = await cache.get("ttl_key")
        assert value_after is None
    finally:
        await cache.close()
