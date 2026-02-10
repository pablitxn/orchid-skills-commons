"""Tests for Prometheus metrics recorder and exposition helpers."""

from __future__ import annotations

import pytest

from orchid_commons.observability.metrics import (
    NoopMetricsRecorder,
    PrometheusMetricsRecorder,
    configure_prometheus_metrics,
    create_prometheus_asgi_app,
    get_metrics_recorder,
    set_metrics_recorder,
)

prometheus_client = pytest.importorskip("prometheus_client")


def test_prometheus_metrics_registration_and_samples() -> None:
    registry = prometheus_client.CollectorRegistry()
    recorder = PrometheusMetricsRecorder(registry=registry)

    recorder.observe_operation(
        resource="Postgres",
        operation="fetchVal",
        duration_seconds=0.015,
        success=True,
    )
    recorder.observe_operation(
        resource="Postgres",
        operation="fetchVal",
        duration_seconds=0.022,
        success=False,
    )
    recorder.observe_error(
        resource="Postgres",
        operation="fetchVal",
        error_type="RuntimeError",
    )
    recorder.observe_postgres_pool(
        used_connections=3,
        idle_connections=1,
        min_connections=1,
        max_connections=10,
    )

    assert registry.get_sample_value(
        "orchid_resource_throughput_total",
        {"resource": "postgres", "operation": "fetchval", "status": "success"},
    ) == 1.0
    assert registry.get_sample_value(
        "orchid_resource_throughput_total",
        {"resource": "postgres", "operation": "fetchval", "status": "error"},
    ) == 1.0
    assert registry.get_sample_value(
        "orchid_resource_errors_total",
        {"resource": "postgres", "operation": "fetchval", "error_type": "runtimeerror"},
    ) == 1.0
    assert registry.get_sample_value(
        "orchid_resource_latency_seconds_count",
        {"resource": "postgres", "operation": "fetchval", "status": "success"},
    ) == 1.0
    assert registry.get_sample_value(
        "orchid_postgres_pool_usage_connections",
        {"state": "used"},
    ) == 3.0


def test_configure_prometheus_metrics_sets_default() -> None:
    previous = get_metrics_recorder()
    registry = prometheus_client.CollectorRegistry()
    try:
        recorder = configure_prometheus_metrics(registry=registry)
        assert get_metrics_recorder() is recorder

        set_metrics_recorder(None)
        assert isinstance(get_metrics_recorder(), NoopMetricsRecorder)
    finally:
        set_metrics_recorder(previous)


@pytest.mark.asyncio
async def test_create_prometheus_asgi_app_exposes_metrics() -> None:
    registry = prometheus_client.CollectorRegistry()
    recorder = PrometheusMetricsRecorder(registry=registry)
    recorder.observe_operation(
        resource="runtime",
        operation="startup",
        duration_seconds=0.005,
        success=True,
    )

    app = create_prometheus_asgi_app(registry=registry)
    sent_messages: list[dict[str, object]] = []

    async def send(message: dict[str, object]) -> None:
        sent_messages.append(message)

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    await app(
        {"type": "http", "method": "GET", "path": "/metrics", "headers": []},
        receive,
        send,
    )

    assert sent_messages[0]["type"] == "http.response.start"
    assert sent_messages[0]["status"] == 200
    assert sent_messages[1]["type"] == "http.response.body"
    payload = sent_messages[1]["body"]
    assert isinstance(payload, bytes)
    assert b"orchid_resource_throughput_total" in payload
