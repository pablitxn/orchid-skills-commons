"""Redis cache provider backed by redis-py asyncio client."""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any, ClassVar

from orchid_commons.config.resources import RedisSettings
from orchid_commons.observability._observable import ObservableMixin
from orchid_commons.observability.metrics import MetricsRecorder
from orchid_commons.runtime.errors import MissingDependencyError
from orchid_commons.runtime.health import HealthStatus


def _import_redis_asyncio() -> Any:
    try:
        import redis.asyncio as redis_asyncio
    except ImportError as exc:  # pragma: no cover - exercised when extras are absent
        raise MissingDependencyError(
            "Redis cache requires optional dependency 'redis'. "
            "Install with: uv sync --extra redis (or --extra db)"
        ) from exc
    return redis_asyncio


def _normalize_prefix(prefix: str) -> str:
    if not prefix:
        return ""
    return prefix if prefix.endswith(":") else f"{prefix}:"


@dataclass(slots=True)
class RedisCache(ObservableMixin):
    """Managed Redis cache with common key/value helpers."""

    _resource_name: ClassVar[str] = "redis"

    _client: Any
    key_prefix: str = ""
    default_ttl_seconds: int | None = None
    _metrics: MetricsRecorder | None = None
    _closed: bool = False

    @classmethod
    async def create(cls, settings: RedisSettings) -> RedisCache:
        """Create and validate a redis cache client from settings."""
        redis_asyncio = _import_redis_asyncio()
        client = redis_asyncio.from_url(
            settings.url.get_secret_value(),
            encoding=settings.encoding,
            decode_responses=settings.decode_responses,
            socket_timeout=settings.socket_timeout_seconds,
            socket_connect_timeout=settings.connect_timeout_seconds,
            health_check_interval=settings.health_check_interval_seconds,
        )
        cache = cls(
            _client=client,
            key_prefix=_normalize_prefix(settings.key_prefix),
            default_ttl_seconds=settings.default_ttl_seconds,
        )
        await cache.ping()
        return cache

    @property
    def client(self) -> Any:
        """Expose underlying redis client for advanced usage."""
        return self._client

    @property
    def is_connected(self) -> bool:
        """Whether cache can still serve requests."""
        return not self._closed

    def _scoped_key(self, key: str) -> str:
        return f"{self.key_prefix}{key}"

    async def ping(self) -> bool:
        """Run a ping command against Redis."""
        started = perf_counter()
        try:
            result = bool(await self._client.ping())
        except Exception as exc:
            self._observe_error("ping", started, exc)
            raise

        self._observe_operation("ping", started, success=True)
        return result

    async def get(self, key: str) -> str | bytes | None:
        """Get a value from cache."""
        started = perf_counter()
        try:
            value = await self._client.get(self._scoped_key(key))
        except Exception as exc:
            self._observe_error("get", started, exc)
            raise

        self._observe_operation("get", started, success=True)
        return value

    async def set(
        self,
        key: str,
        value: str | bytes,
        *,
        ttl_seconds: int | None = None,
    ) -> bool:
        """Set a cache value with optional TTL."""
        started = perf_counter()
        ttl = self.default_ttl_seconds if ttl_seconds is None else ttl_seconds
        try:
            success = bool(
                await self._client.set(
                    self._scoped_key(key),
                    value,
                    ex=ttl,
                )
            )
        except Exception as exc:
            self._observe_error("set", started, exc)
            raise

        self._observe_operation("set", started, success=True)
        return success

    async def delete(self, key: str) -> int:
        """Delete a key from cache and return removed count."""
        started = perf_counter()
        try:
            deleted = int(await self._client.delete(self._scoped_key(key)))
        except Exception as exc:
            self._observe_error("delete", started, exc)
            raise

        self._observe_operation("delete", started, success=True)
        return deleted

    async def exists(self, key: str) -> bool:
        """Return whether a key exists in cache."""
        started = perf_counter()
        try:
            exists = bool(await self._client.exists(self._scoped_key(key)))
        except Exception as exc:
            self._observe_error("exists", started, exc)
            raise

        self._observe_operation("exists", started, success=True)
        return exists

    async def health_check(self) -> HealthStatus:
        """Verify Redis liveness with a ping command."""
        start = perf_counter()
        try:
            await self.ping()
            latency_ms = (perf_counter() - start) * 1000
            return HealthStatus(
                healthy=True,
                latency_ms=latency_ms,
                message="ok",
                details={"key_prefix": self.key_prefix or None},
            )
        except Exception as exc:
            latency_ms = (perf_counter() - start) * 1000
            return HealthStatus(
                healthy=False,
                latency_ms=latency_ms,
                message=str(exc),
                details={"error_type": exc.__class__.__name__},
            )

    async def close(self) -> None:
        """Close Redis client and free underlying connections."""
        started = perf_counter()
        try:
            close = getattr(self._client, "aclose", None)
            if close is None:
                close = getattr(self._client, "close", None)

            if callable(close):
                maybe_awaitable = close()
                if hasattr(maybe_awaitable, "__await__"):
                    await maybe_awaitable
        except Exception as exc:
            self._observe_error("close", started, exc)
            raise
        finally:
            self._closed = True

        self._observe_operation("close", started, success=True)


async def create_redis_cache(settings: RedisSettings) -> RedisCache:
    """Factory used by ResourceManager bootstrap."""
    return await RedisCache.create(settings)
