"""Tests for RabbitMQ broker provider."""

from __future__ import annotations

from typing import Any

import pytest

import orchid_commons.db.rabbitmq as rabbitmq_module
from orchid_commons.config.resources import RabbitMqSettings
from orchid_commons.db.rabbitmq import RabbitMqBroker, create_rabbitmq_broker


class FakeExchange:
    def __init__(self) -> None:
        self.published: list[tuple[Any, str]] = []

    async def publish(self, message: Any, routing_key: str) -> None:
        self.published.append((message, routing_key))


class FakeQueue:
    def __init__(self, name: str) -> None:
        self.name = name


class FakeChannel:
    def __init__(self) -> None:
        self.qos_prefetch_count: int | None = None
        self.declared_queues: list[tuple[str, bool, bool]] = []
        self.default_exchange = FakeExchange()
        self.is_closed = False

    async def set_qos(self, *, prefetch_count: int) -> None:
        self.qos_prefetch_count = prefetch_count

    async def declare_queue(self, name: str, *, durable: bool, passive: bool) -> FakeQueue:
        self.declared_queues.append((name, durable, passive))
        return FakeQueue(name)

    async def get_exchange(self, name: str) -> FakeExchange:
        return self.default_exchange

    async def close(self) -> None:
        self.is_closed = True


class FakeConnection:
    def __init__(self, channel: FakeChannel) -> None:
        self._channel = channel
        self.is_closed = False
        self.channel_calls = 0

    async def channel(self, *, publisher_confirms: bool = True) -> FakeChannel:
        del publisher_confirms
        self.channel_calls += 1
        return self._channel

    async def close(self) -> None:
        self.is_closed = True


class FakeAioPikaModule:
    class DeliveryMode:
        PERSISTENT = 2
        NOT_PERSISTENT = 1

    class Message:
        def __init__(
            self,
            *,
            body: bytes,
            headers: dict[str, Any] | None,
            content_type: str | None,
            delivery_mode: int,
        ) -> None:
            self.body = body
            self.headers = headers
            self.content_type = content_type
            self.delivery_mode = delivery_mode

    def __init__(self, connection: FakeConnection) -> None:
        self._connection = connection
        self.connect_calls: list[dict[str, Any]] = []

    async def connect_robust(self, url: str, **kwargs: Any) -> FakeConnection:
        timeout = float(kwargs.get("timeout", 0.0))
        heartbeat = int(kwargs.get("heartbeat", 0))
        self.connect_calls.append(
            {
                "url": url,
                "timeout": timeout,
                "heartbeat": heartbeat,
            }
        )
        return self._connection


class TestRabbitMqBroker:
    async def test_factory_declare_publish_and_close(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        channel = FakeChannel()
        connection = FakeConnection(channel)
        fake_aio_pika = FakeAioPikaModule(connection)
        monkeypatch.setattr(rabbitmq_module, "_import_aio_pika", lambda: fake_aio_pika)

        broker = await create_rabbitmq_broker(
            RabbitMqSettings(
                url="amqp://guest:guest@localhost:5672/",
                prefetch_count=25,
            )
        )

        queue = await broker.declare_queue("events", durable=True)
        assert queue.name == "events"

        await broker.publish({"type": "event", "value": 1}, queue_name="events")

        published_message, routing_key = channel.default_exchange.published[0]
        assert routing_key == "events"
        assert published_message.content_type == "application/json"
        assert published_message.body == b'{"type": "event", "value": 1}'
        assert channel.qos_prefetch_count == 25

        health = await broker.health_check()
        assert health.healthy is True

        await broker.close()
        assert connection.is_closed is True
        assert channel.is_closed is True
        assert broker.is_connected is False

    async def test_health_check_unhealthy(self) -> None:
        broker = RabbitMqBroker(
            _connection=type("Conn", (), {"is_closed": True})(),
            _channel=type("Channel", (), {"is_closed": True})(),
            prefetch_count=1,
        )

        status = await broker.health_check()

        assert status.healthy is False
        assert status.details == {"error_type": "RuntimeError"}
