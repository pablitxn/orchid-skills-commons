"""RabbitMQ provider backed by aio-pika robust connections."""

from __future__ import annotations

import json
from dataclasses import dataclass
from time import perf_counter
from typing import Any, ClassVar

from orchid_commons.config.resources import RabbitMqSettings
from orchid_commons.observability._observable import ObservableMixin
from orchid_commons.observability.metrics import MetricsRecorder
from orchid_commons.runtime.errors import MissingDependencyError
from orchid_commons.runtime.health import HealthStatus


def _import_aio_pika() -> Any:
    try:
        import aio_pika
    except ImportError as exc:  # pragma: no cover - exercised when extras are absent
        raise MissingDependencyError(
            "RabbitMQ provider requires optional dependency 'aio-pika'. "
            "Install with: uv sync --extra rabbitmq (or --extra db)"
        ) from exc
    return aio_pika


@dataclass(slots=True)
class RabbitMqBroker(ObservableMixin):
    """Managed RabbitMQ connection with lightweight publish helpers."""

    _resource_name: ClassVar[str] = "rabbitmq"

    _connection: Any
    _channel: Any
    prefetch_count: int
    _metrics: MetricsRecorder | None = None
    _closed: bool = False

    @classmethod
    async def create(cls, settings: RabbitMqSettings) -> RabbitMqBroker:
        """Create and validate a RabbitMQ broker from settings."""
        aio_pika = _import_aio_pika()
        connection = await aio_pika.connect_robust(
            settings.url.get_secret_value(),
            timeout=settings.connect_timeout_seconds,
            heartbeat=settings.heartbeat_seconds,
        )
        channel = await connection.channel(
            publisher_confirms=settings.publisher_confirms,
        )
        await channel.set_qos(prefetch_count=settings.prefetch_count)
        broker = cls(
            _connection=connection,
            _channel=channel,
            prefetch_count=settings.prefetch_count,
        )
        await broker.health_check()
        return broker

    @property
    def connection(self) -> Any:
        """Expose underlying robust connection for advanced usage."""
        return self._connection

    @property
    def channel(self) -> Any:
        """Expose shared channel for advanced usage."""
        return self._channel

    @property
    def is_connected(self) -> bool:
        """Whether broker can still serve requests."""
        return not self._closed and not bool(getattr(self._connection, "is_closed", False))

    async def declare_queue(
        self,
        queue_name: str,
        *,
        durable: bool = True,
        passive: bool = False,
    ) -> Any:
        """Declare (or validate) a queue and return queue handle."""
        started = perf_counter()
        try:
            queue = await self._channel.declare_queue(
                queue_name,
                durable=durable,
                passive=passive,
            )
        except Exception as exc:
            self._observe_error("declare_queue", started, exc)
            raise

        self._observe_operation("declare_queue", started, success=True)
        return queue

    async def publish(
        self,
        payload: bytes | str | dict[str, Any],
        *,
        queue_name: str | None = None,
        exchange_name: str = "",
        routing_key: str | None = None,
        headers: dict[str, Any] | None = None,
        content_type: str | None = None,
        persistent: bool = True,
    ) -> None:
        """Publish payload to a queue or exchange."""
        started = perf_counter()
        aio_pika = _import_aio_pika()

        resolved_body: bytes
        resolved_content_type = content_type
        if isinstance(payload, bytes):
            resolved_body = payload
            if resolved_content_type is None:
                resolved_content_type = "application/octet-stream"
        elif isinstance(payload, str):
            resolved_body = payload.encode("utf-8")
            if resolved_content_type is None:
                resolved_content_type = "text/plain"
        else:
            resolved_body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
            if resolved_content_type is None:
                resolved_content_type = "application/json"

        target_routing_key = routing_key or queue_name
        if not target_routing_key:
            raise ValueError("publish requires either queue_name or routing_key")

        try:
            if exchange_name:
                exchange = await self._channel.get_exchange(exchange_name)
            else:
                exchange = self._channel.default_exchange

            message = aio_pika.Message(
                body=resolved_body,
                headers=headers,
                content_type=resolved_content_type,
                delivery_mode=(
                    aio_pika.DeliveryMode.PERSISTENT
                    if persistent
                    else aio_pika.DeliveryMode.NOT_PERSISTENT
                ),
            )
            await exchange.publish(message, routing_key=target_routing_key)
        except Exception as exc:
            self._observe_error("publish", started, exc)
            raise

        self._observe_operation("publish", started, success=True)

    async def health_check(self) -> HealthStatus:
        """Verify RabbitMQ connectivity by opening/closing a probe channel."""
        started = perf_counter()
        try:
            if bool(getattr(self._connection, "is_closed", False)):
                raise RuntimeError("RabbitMQ connection is closed")
            probe_channel = await self._connection.channel()
            await probe_channel.close()
            latency_ms = (perf_counter() - started) * 1000
            return HealthStatus(
                healthy=True,
                latency_ms=latency_ms,
                message="ok",
                details={"prefetch_count": self.prefetch_count},
            )
        except Exception as exc:
            latency_ms = (perf_counter() - started) * 1000
            return HealthStatus(
                healthy=False,
                latency_ms=latency_ms,
                message=str(exc),
                details={"error_type": type(exc).__name__},
            )

    async def close(self) -> None:
        """Close channel and connection."""
        started = perf_counter()
        try:
            if not bool(getattr(self._channel, "is_closed", False)):
                await self._channel.close()
            if not bool(getattr(self._connection, "is_closed", False)):
                await self._connection.close()
        except Exception as exc:
            self._observe_error("close", started, exc)
            raise
        finally:
            self._closed = True

        self._observe_operation("close", started, success=True)


async def create_rabbitmq_broker(settings: RabbitMqSettings) -> RabbitMqBroker:
    """Factory used by ResourceManager bootstrap."""
    return await RabbitMqBroker.create(settings)
