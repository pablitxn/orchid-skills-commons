"""Integration smoke tests for OpenTelemetry bootstrap and instrumentation."""

from __future__ import annotations

import pytest

from orchid_commons.config.models import ObservabilitySettings
from orchid_commons.metrics import get_metrics_recorder
from orchid_commons.observability.otel import (
    OpenTelemetryMetricsRecorder,
    bootstrap_observability,
    request_span,
    shutdown_observability,
)
from orchid_commons.sql import create_sqlite_resource

pytestmark = pytest.mark.integration


def _require_otel() -> None:
    pytest.importorskip("opentelemetry")
    pytest.importorskip("opentelemetry.sdk")


async def test_observability_smoke_for_request_and_resource_metrics(sqlite_settings) -> None:
    _require_otel()
    shutdown_observability()

    handle = bootstrap_observability(
        ObservabilitySettings(
            enabled=True,
            otlp_endpoint=None,
            retry_enabled=False,
            metrics_export_interval_seconds=60.0,
        ),
        service_name="orchid-integration",
        environment="test",
    )

    assert handle.enabled is True
    assert handle.tracer_provider is not None
    assert handle.meter_provider is not None
    assert isinstance(get_metrics_recorder(), OpenTelemetryMetricsRecorder)

    sqlite = await create_sqlite_resource(sqlite_settings)
    try:
        with request_span("integration.request", method="GET", route="/integration", status_code=200):
            await sqlite.execute(
                "CREATE TABLE IF NOT EXISTS telemetry(id INTEGER PRIMARY KEY, ok INTEGER NOT NULL)",
                commit=True,
            )
            await sqlite.execute("INSERT INTO telemetry(ok) VALUES (?)", (1,), commit=True)

        row = await sqlite.fetchone("SELECT COUNT(*) AS total FROM telemetry")
        assert row is not None
        assert row["total"] == 1
    finally:
        await sqlite.close()
        shutdown_observability()
