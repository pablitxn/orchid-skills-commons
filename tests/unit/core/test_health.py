"""Tests for aggregated health reporting and `/health` payload serialization."""

from __future__ import annotations

from dataclasses import dataclass

from orchid_commons import HealthStatus, ResourceManager, aggregate_health_checks


async def _healthy_sql_check() -> HealthStatus:
    return HealthStatus(healthy=True, latency_ms=4.0, message="sql ok")


async def _healthy_blob_check() -> HealthStatus:
    return HealthStatus(
        healthy=True,
        latency_ms=8.5,
        message="bucket reachable",
        details={"provider": "minio"},
    )


async def _failing_check() -> HealthStatus:
    raise RuntimeError("dependency unavailable")


class StaticHealthResource:
    def __init__(self, status: HealthStatus) -> None:
        self._status = status

    async def health_check(self) -> HealthStatus:
        return self._status


@dataclass(slots=True)
class FakeObservabilityHandle:
    enabled: bool = True
    otlp_endpoint: str | None = "http://collector:4317"
    tracer_provider: object | None = None
    meter_provider: object | None = None


@dataclass(slots=True, frozen=True)
class FakeLangfuseSettings:
    enabled: bool = True


class FakeLangfuseClient:
    def __init__(
        self,
        *,
        enabled: bool,
        settings_enabled: bool,
        disabled_reason: str | None = None,
    ) -> None:
        self.settings = FakeLangfuseSettings(enabled=settings_enabled)
        self.enabled = enabled
        self.disabled_reason = disabled_reason
        self.flush_calls = 0

    def flush(self) -> None:
        self.flush_calls += 1


async def test_aggregate_health_checks_is_serializable_for_endpoint() -> None:
    report = await aggregate_health_checks(
        {
            "sqlite": _healthy_sql_check,
            "minio": _healthy_blob_check,
        }
    )

    assert report.status == "ok"
    assert report.readiness is True
    assert report.healthy is True
    assert report.summary.total == 2
    assert report.summary.healthy == 2
    assert report.summary.unhealthy == 0

    payload = report.to_dict()
    assert payload["status"] == "ok"
    assert payload["readiness"] is True
    assert payload["summary"] == {"total": 2, "healthy": 2, "unhealthy": 0}
    assert payload["checks"]["sqlite"]["healthy"] is True
    assert payload["checks"]["minio"]["details"] == {"provider": "minio"}


async def test_aggregate_health_checks_reports_partial_degradation() -> None:
    report = await aggregate_health_checks(
        {
            "sqlite": _healthy_sql_check,
            "postgres": _failing_check,
        }
    )

    assert report.status == "degraded"
    assert report.readiness is False
    assert report.liveness is True
    assert report.summary.total == 2
    assert report.summary.healthy == 1
    assert report.summary.unhealthy == 1
    assert report.checks["postgres"].healthy is False
    assert report.checks["postgres"].details == {"error_type": "RuntimeError"}


async def test_resource_manager_health_report_includes_optional_backends() -> None:
    manager = ResourceManager()
    manager.register("sqlite", StaticHealthResource(HealthStatus(healthy=True, latency_ms=2.0)))

    observability = FakeObservabilityHandle(
        enabled=True,
        tracer_provider=object(),
        meter_provider=object(),
    )
    langfuse = FakeLangfuseClient(enabled=True, settings_enabled=True)

    report = await manager.health_report(
        observability_handle=observability,
        langfuse_client=langfuse,
    )

    assert set(report.checks.keys()) == {"sqlite", "otel", "langfuse"}
    assert report.checks["otel"].healthy is True
    assert report.checks["langfuse"].healthy is True
    assert langfuse.flush_calls == 1

    payload = await manager.health_payload(
        observability_handle=observability,
        langfuse_client=langfuse,
    )
    assert payload["checks"]["otel"]["healthy"] is True
    assert payload["checks"]["langfuse"]["healthy"] is True


async def test_resource_manager_health_report_degrades_when_langfuse_unavailable() -> None:
    manager = ResourceManager()
    manager.register("sqlite", StaticHealthResource(HealthStatus(healthy=True, latency_ms=1.0)))

    report = await manager.health_report(
        observability_handle=FakeObservabilityHandle(enabled=False),
        langfuse_client=FakeLangfuseClient(
            enabled=False,
            settings_enabled=True,
            disabled_reason="missing credentials",
        ),
    )

    assert report.status == "degraded"
    assert report.checks["langfuse"].healthy is False
    assert report.checks["langfuse"].details == {"disabled_reason": "missing credentials"}
