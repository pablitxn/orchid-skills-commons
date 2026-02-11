"""End-to-end integration tests for the RabbitMQ broker provider."""

from __future__ import annotations

import pytest

from orchid_commons.db import create_rabbitmq_broker

pytestmark = pytest.mark.integration


async def test_rabbitmq_publish_and_health(rabbitmq_settings) -> None:
    broker = await create_rabbitmq_broker(rabbitmq_settings)
    try:
        queue = await broker.declare_queue("integration_test_queue", durable=False)

        await broker.publish(
            {"event": "test", "data": "hello"},
            queue_name="integration_test_queue",
        )

        # Consume one message to verify it arrived
        message = await queue.get(timeout=5.0)
        assert message is not None
        await message.ack()

        assert (await broker.health_check()).healthy is True
    finally:
        await broker.close()
