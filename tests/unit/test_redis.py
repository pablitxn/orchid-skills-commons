"""Tests for Redis cache provider."""

from __future__ import annotations

from typing import Any

import pytest

import orchid_commons.db.redis as redis_module
from orchid_commons.config.resources import RedisSettings
from orchid_commons.db.redis import RedisCache, create_redis_cache


class FakeRedisClient:
    def __init__(self) -> None:
        self.values: dict[str, str | bytes] = {}
        self.closed = False
        self.ping_calls = 0
        self.ping_error: Exception | None = None

    async def ping(self) -> bool:
        self.ping_calls += 1
        if self.ping_error is not None:
            raise self.ping_error
        return True

    async def get(self, key: str) -> str | bytes | None:
        return self.values.get(key)

    async def set(self, key: str, value: str | bytes, *, ex: int | None = None) -> bool:
        del ex
        self.values[key] = value
        return True

    async def delete(self, key: str) -> int:
        return int(self.values.pop(key, None) is not None)

    async def exists(self, key: str) -> int:
        return int(key in self.values)

    async def aclose(self) -> None:
        self.closed = True


class FakeRedisModule:
    def __init__(self, client: FakeRedisClient) -> None:
        self._client = client
        self.from_url_calls: list[dict[str, Any]] = []

    def from_url(self, url: str, **kwargs: Any) -> FakeRedisClient:
        self.from_url_calls.append({"url": url, **kwargs})
        return self._client


class TestRedisCache:
    async def test_factory_and_cache_operations(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_client = FakeRedisClient()
        fake_module = FakeRedisModule(fake_client)
        monkeypatch.setattr(redis_module, "_import_redis_asyncio", lambda: fake_module)

        cache = await create_redis_cache(
            RedisSettings(
                url="redis://localhost:6379/0",
                key_prefix="svc",
                default_ttl_seconds=30,
            )
        )

        await cache.set("hello", "world")
        assert fake_client.values["svc:hello"] == "world"
        assert await cache.get("hello") == "world"
        assert await cache.exists("hello") is True
        assert await cache.delete("hello") == 1
        assert await cache.exists("hello") is False

        health = await cache.health_check()
        assert health.healthy is True

        await cache.close()

        assert fake_client.closed is True
        assert cache.is_connected is False
        assert fake_client.ping_calls >= 2
        assert fake_module.from_url_calls[0]["url"] == "redis://localhost:6379/0"

    async def test_health_check_unhealthy(self) -> None:
        client = FakeRedisClient()
        client.ping_error = RuntimeError("ping failed")
        cache = RedisCache(_client=client)

        status = await cache.health_check()

        assert status.healthy is False
        assert status.details == {"error_type": "CacheOperationError"}
