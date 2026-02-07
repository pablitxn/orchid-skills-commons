"""Health primitives shared by all resources."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Literal, Protocol, runtime_checkable


@dataclass(slots=True)
class HealthStatus:
    """Represents an infrastructure health check result."""

    healthy: bool
    latency_ms: float
    message: str | None = None
    details: dict[str, str] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        payload: dict[str, Any] = {
            "healthy": self.healthy,
            "latency_ms": max(0.0, float(self.latency_ms)),
        }
        if self.message is not None:
            payload["message"] = self.message
        if self.details:
            payload["details"] = dict(self.details)
        return payload


@dataclass(slots=True, frozen=True)
class HealthSummary:
    """Summary counters for an aggregated health report."""

    total: int
    healthy: int
    unhealthy: int

    def to_dict(self) -> dict[str, int]:
        return {
            "total": self.total,
            "healthy": self.healthy,
            "unhealthy": self.unhealthy,
        }


@dataclass(slots=True)
class HealthReport:
    """Aggregated readiness/liveness report across multiple checks."""

    status: Literal["ok", "degraded", "down"]
    healthy: bool
    readiness: bool
    liveness: bool
    latency_ms: float
    checks: dict[str, HealthStatus]
    summary: HealthSummary

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable payload suitable for `/health` endpoints."""
        return {
            "status": self.status,
            "healthy": self.healthy,
            "readiness": self.readiness,
            "liveness": self.liveness,
            "latency_ms": max(0.0, float(self.latency_ms)),
            "summary": self.summary.to_dict(),
            "checks": {name: status.to_dict() for name, status in self.checks.items()},
        }


HealthCheck = Callable[[], Awaitable[HealthStatus]]


async def aggregate_health_checks(
    checks: Mapping[str, HealthCheck],
    *,
    timeout_seconds: float | None = None,
) -> HealthReport:
    """Run health checks concurrently and aggregate readiness/liveness status."""
    if timeout_seconds is not None and timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be > 0")

    started = perf_counter()
    if not checks:
        return HealthReport(
            status="ok",
            healthy=True,
            readiness=True,
            liveness=True,
            latency_ms=(perf_counter() - started) * 1000,
            checks={},
            summary=HealthSummary(total=0, healthy=0, unhealthy=0),
        )

    names = list(checks.keys())
    results = await asyncio.gather(
        *(
            _run_health_check(
                check_name=name,
                check=checks[name],
                timeout_seconds=timeout_seconds,
            )
            for name in names
        )
    )
    statuses = {name: status for name, status in zip(names, results, strict=False)}

    total = len(statuses)
    healthy_count = sum(1 for status in statuses.values() if status.healthy)
    unhealthy_count = total - healthy_count
    readiness = unhealthy_count == 0
    degraded = 0 < healthy_count < total
    aggregate_status: Literal["ok", "degraded", "down"]
    if readiness:
        aggregate_status = "ok"
    elif degraded:
        aggregate_status = "degraded"
    else:
        aggregate_status = "down"

    return HealthReport(
        status=aggregate_status,
        healthy=readiness,
        readiness=readiness,
        liveness=True,
        latency_ms=(perf_counter() - started) * 1000,
        checks=statuses,
        summary=HealthSummary(total=total, healthy=healthy_count, unhealthy=unhealthy_count),
    )


async def _run_health_check(
    *,
    check_name: str,
    check: HealthCheck,
    timeout_seconds: float | None,
) -> HealthStatus:
    started = perf_counter()

    try:
        awaitable = check()
        status = (
            await awaitable
            if timeout_seconds is None
            else await asyncio.wait_for(awaitable, timeout=timeout_seconds)
        )
        if not isinstance(status, HealthStatus):
            raise TypeError(
                f"health_check for '{check_name}' returned {type(status).__name__}, "
                "expected HealthStatus"
            )

        latency_ms = status.latency_ms
        if latency_ms < 0:
            latency_ms = (perf_counter() - started) * 1000

        return HealthStatus(
            healthy=status.healthy,
            latency_ms=latency_ms,
            message=status.message,
            details=dict(status.details) if status.details else None,
        )
    except Exception as exc:
        return HealthStatus(
            healthy=False,
            latency_ms=(perf_counter() - started) * 1000,
            message=str(exc),
            details={"error_type": type(exc).__name__},
        )


@runtime_checkable
class Resource(Protocol):
    """Contract for managed resources with lifecycle and health check support."""

    async def health_check(self) -> HealthStatus:
        """Return current health status of this resource."""
        ...

    async def close(self) -> None:
        """Release any held connections or resources."""
        ...
