"""Demo workload that emits Orchid Prometheus metrics and OTLP traces."""

from __future__ import annotations

import os
import random
import signal
import time
from dataclasses import dataclass

from orchid_commons import (
    bootstrap_observability,
    configure_prometheus_metrics,
    request_span,
    shutdown_observability,
    start_prometheus_http_server,
)
from orchid_commons.config.models import ObservabilitySettings


@dataclass(frozen=True)
class DemoConfig:
    service_name: str
    service_version: str
    environment: str
    otlp_endpoint: str
    metrics_port: int
    interval_seconds: float
    error_rate: float

    @classmethod
    def from_env(cls) -> DemoConfig:
        return cls(
            service_name=os.getenv("OTEL_SERVICE_NAME", "orchid-commons-demo"),
            service_version=os.getenv("OTEL_SERVICE_VERSION", "0.1.0"),
            environment=os.getenv("OTEL_ENVIRONMENT", "local"),
            otlp_endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317"),
            metrics_port=_env_int("METRICS_PORT", default=9464),
            interval_seconds=max(0.1, _env_float("DEMO_INTERVAL_SECONDS", default=2.0)),
            error_rate=min(1.0, max(0.0, _env_float("DEMO_ERROR_RATE", default=0.2))),
        )


def _env_float(name: str, *, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, *, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def main() -> None:
    config = DemoConfig.from_env()

    prom_recorder = configure_prometheus_metrics(prefix="orchid", set_default=False)
    start_prometheus_http_server(port=config.metrics_port, host="0.0.0.0")

    bootstrap_observability(
        ObservabilitySettings(
            enabled=True,
            otlp_endpoint=config.otlp_endpoint,
            sample_rate=1.0,
            otlp_insecure=True,
            metrics_export_interval_seconds=5.0,
        ),
        service_name=config.service_name,
        service_version=config.service_version,
        environment=config.environment,
    )

    should_stop = False

    def _stop_handler(signum: int, _frame: object) -> None:
        nonlocal should_stop
        should_stop = True
        print(f"received signal {signum}; shutting down", flush=True)

    signal.signal(signal.SIGINT, _stop_handler)
    signal.signal(signal.SIGTERM, _stop_handler)

    print(
        f"demo workload started | metrics=:{config.metrics_port}/metrics "
        f"| otlp={config.otlp_endpoint}",
        flush=True,
    )

    iteration = 0
    while not should_stop:
        iteration += 1
        duration_seconds = random.uniform(0.05, 0.8)
        success = random.random() >= config.error_rate
        status_code = 200 if success else 500

        try:
            with request_span(
                "demo.runtime.operation",
                method="GET",
                route="/demo/workload",
                request_id=f"demo-{iteration}",
                status_code=status_code,
                attributes={
                    "demo.iteration": iteration,
                    "demo.component": "workload-loop",
                },
            ):
                time.sleep(duration_seconds)
                if not success:
                    raise RuntimeError("simulated_runtime_error")
        except RuntimeError as exc:
            prom_recorder.observe_operation(
                resource="runtime",
                operation="loop_iteration",
                duration_seconds=duration_seconds,
                success=False,
            )
            prom_recorder.observe_error(
                resource="runtime",
                operation="loop_iteration",
                error_type=type(exc).__name__,
            )
        else:
            prom_recorder.observe_operation(
                resource="runtime",
                operation="loop_iteration",
                duration_seconds=duration_seconds,
                success=True,
            )

        used = random.randint(1, 6)
        idle = max(0, 8 - used)
        prom_recorder.observe_postgres_pool(
            used_connections=used,
            idle_connections=idle,
            min_connections=1,
            max_connections=8,
        )

        time.sleep(config.interval_seconds)

    shutdown_observability()


if __name__ == "__main__":
    main()
