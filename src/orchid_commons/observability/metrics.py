"""Prometheus metrics primitives for Orchid resources and runtime."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

from orchid_commons.runtime.errors import MissingDependencyError

_LABEL_NORMALIZER = re.compile(r"[^a-zA-Z0-9_]+")


def _import_prometheus_client() -> Any:
    try:
        import prometheus_client
    except ImportError as exc:  # pragma: no cover - depends on optional extras
        raise MissingDependencyError(
            "Prometheus metrics require optional dependency 'prometheus-client'. "
            "Install with: uv sync --extra observability"
        ) from exc
    return prometheus_client


def _sanitize_label(value: str, *, default: str = "unknown") -> str:
    normalized = _LABEL_NORMALIZER.sub("_", value.strip().lower()).strip("_")
    return normalized or default


def _collector_or_create(registry: Any, name: str, factory: Any) -> Any:
    names_to_collectors = getattr(registry, "_names_to_collectors", None)
    if isinstance(names_to_collectors, dict):
        collector = names_to_collectors.get(name)
        if collector is not None:
            return collector
    return factory()


class MetricsRecorder(Protocol):
    """Observer contract for runtime/resource metrics."""

    def observe_operation(
        self,
        *,
        resource: str,
        operation: str,
        duration_seconds: float,
        success: bool,
    ) -> None:
        """Record operation latency and throughput."""
        ...

    def observe_error(
        self,
        *,
        resource: str,
        operation: str,
        error_type: str,
    ) -> None:
        """Record operation error counters."""
        ...

    def observe_postgres_pool(
        self,
        *,
        used_connections: int,
        idle_connections: int,
        min_connections: int,
        max_connections: int,
    ) -> None:
        """Record PostgreSQL pool usage gauges."""
        ...


class NoopMetricsRecorder:
    """No-op recorder used when metrics are not configured."""

    def observe_operation(
        self,
        *,
        resource: str,
        operation: str,
        duration_seconds: float,
        success: bool,
    ) -> None:
        del resource, operation, duration_seconds, success

    def observe_error(
        self,
        *,
        resource: str,
        operation: str,
        error_type: str,
    ) -> None:
        del resource, operation, error_type

    def observe_postgres_pool(
        self,
        *,
        used_connections: int,
        idle_connections: int,
        min_connections: int,
        max_connections: int,
    ) -> None:
        del used_connections, idle_connections, min_connections, max_connections


class PrometheusMetricsRecorder:
    """Prometheus-backed recorder with standard orchid_* naming."""

    def __init__(
        self,
        *,
        registry: Any | None = None,
        prefix: str = "orchid",
    ) -> None:
        prometheus_client = _import_prometheus_client()
        self._registry = prometheus_client.REGISTRY if registry is None else registry
        self._prefix = _sanitize_label(prefix, default="orchid")
        self._latency = _collector_or_create(
            self._registry,
            f"{self._prefix}_resource_latency_seconds",
            lambda: prometheus_client.Histogram(
                f"{self._prefix}_resource_latency_seconds",
                "Resource operation latency in seconds.",
                labelnames=("resource", "operation", "status"),
                registry=self._registry,
                buckets=(
                    0.001,
                    0.005,
                    0.01,
                    0.025,
                    0.05,
                    0.1,
                    0.25,
                    0.5,
                    1.0,
                    2.5,
                    5.0,
                    10.0,
                ),
            ),
        )
        self._throughput = _collector_or_create(
            self._registry,
            f"{self._prefix}_resource_throughput_total",
            lambda: prometheus_client.Counter(
                f"{self._prefix}_resource_throughput_total",
                "Resource operation throughput counter.",
                labelnames=("resource", "operation", "status"),
                registry=self._registry,
            ),
        )
        self._errors = _collector_or_create(
            self._registry,
            f"{self._prefix}_resource_errors_total",
            lambda: prometheus_client.Counter(
                f"{self._prefix}_resource_errors_total",
                "Resource operation errors.",
                labelnames=("resource", "operation", "error_type"),
                registry=self._registry,
            ),
        )
        self._postgres_pool_usage = _collector_or_create(
            self._registry,
            f"{self._prefix}_postgres_pool_usage_connections",
            lambda: prometheus_client.Gauge(
                f"{self._prefix}_postgres_pool_usage_connections",
                "Current PostgreSQL pool usage.",
                labelnames=("state",),
                registry=self._registry,
            ),
        )

    def observe_operation(
        self,
        *,
        resource: str,
        operation: str,
        duration_seconds: float,
        success: bool,
    ) -> None:
        resource_label = _sanitize_label(resource)
        operation_label = _sanitize_label(operation)
        status_label = "success" if success else "error"
        duration = max(0.0, duration_seconds)
        self._latency.labels(
            resource=resource_label,
            operation=operation_label,
            status=status_label,
        ).observe(duration)
        self._throughput.labels(
            resource=resource_label,
            operation=operation_label,
            status=status_label,
        ).inc()

    def observe_error(
        self,
        *,
        resource: str,
        operation: str,
        error_type: str,
    ) -> None:
        self._errors.labels(
            resource=_sanitize_label(resource),
            operation=_sanitize_label(operation),
            error_type=_sanitize_label(error_type),
        ).inc()

    def observe_postgres_pool(
        self,
        *,
        used_connections: int,
        idle_connections: int,
        min_connections: int,
        max_connections: int,
    ) -> None:
        self._postgres_pool_usage.labels(state="used").set(max(0.0, float(used_connections)))
        self._postgres_pool_usage.labels(state="idle").set(max(0.0, float(idle_connections)))
        self._postgres_pool_usage.labels(state="min").set(max(0.0, float(min_connections)))
        self._postgres_pool_usage.labels(state="max").set(max(0.0, float(max_connections)))


_NOOP_RECORDER = NoopMetricsRecorder()
_DEFAULT_RECORDER: MetricsRecorder = _NOOP_RECORDER


def get_metrics_recorder() -> MetricsRecorder:
    """Return the process-level metrics recorder."""
    return _DEFAULT_RECORDER


def set_metrics_recorder(recorder: MetricsRecorder | None) -> MetricsRecorder:
    """Set process-level recorder. `None` switches back to no-op."""
    global _DEFAULT_RECORDER
    _DEFAULT_RECORDER = _NOOP_RECORDER if recorder is None else recorder
    return _DEFAULT_RECORDER


def configure_prometheus_metrics(
    *,
    registry: Any | None = None,
    prefix: str = "orchid",
    set_default: bool = True,
) -> PrometheusMetricsRecorder:
    """Build a Prometheus recorder and optionally set it as default."""
    recorder = PrometheusMetricsRecorder(registry=registry, prefix=prefix)
    if set_default:
        set_metrics_recorder(recorder)
    return recorder


def prometheus_content_type() -> str:
    """Return Prometheus exposition media type."""
    prometheus_client = _import_prometheus_client()
    return str(prometheus_client.CONTENT_TYPE_LATEST)


def render_prometheus_metrics(*, registry: Any | None = None) -> bytes:
    """Render current Prometheus metrics in exposition text format."""
    prometheus_client = _import_prometheus_client()
    resolved_registry = prometheus_client.REGISTRY if registry is None else registry
    return bytes(prometheus_client.generate_latest(resolved_registry))


@dataclass(frozen=True, slots=True)
class PrometheusHttpServer:
    """Handle for the background Prometheus HTTP exporter."""

    server: Any
    thread: Any


def start_prometheus_http_server(
    *,
    port: int = 9464,
    host: str = "0.0.0.0",
    registry: Any | None = None,
) -> PrometheusHttpServer:
    """Start Prometheus exporter in a background thread."""
    if not (1 <= port <= 65535):
        raise ValueError("port must be between 1 and 65535")

    prometheus_client = _import_prometheus_client()
    resolved_registry = prometheus_client.REGISTRY if registry is None else registry
    server, thread = prometheus_client.start_http_server(
        port=port,
        addr=host,
        registry=resolved_registry,
    )
    return PrometheusHttpServer(server=server, thread=thread)


async def _asgi_send_response(
    send: Any,
    *,
    status: int,
    body: bytes,
    content_type: str,
    content_length: int | None = None,
) -> None:
    response_length = len(body) if content_length is None else max(0, content_length)
    headers = [
        (b"content-type", content_type.encode("latin-1")),
        (b"content-length", str(response_length).encode("ascii")),
    ]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


def create_prometheus_asgi_app(*, registry: Any | None = None) -> Any:
    """Create a tiny ASGI app exposing Prometheus metrics at `/metrics` or `/`."""

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        del receive
        if scope.get("type") != "http":
            return

        method = str(scope.get("method", "GET")).upper()
        path = str(scope.get("path", "/"))

        if method not in {"GET", "HEAD"}:
            await _asgi_send_response(
                send,
                status=405,
                body=b"method not allowed",
                content_type="text/plain; charset=utf-8",
            )
            return

        if path not in {"/", "", "/metrics"}:
            await _asgi_send_response(
                send,
                status=404,
                body=b"not found",
                content_type="text/plain; charset=utf-8",
            )
            return

        payload = render_prometheus_metrics(registry=registry)
        await _asgi_send_response(
            send,
            status=200,
            body=b"" if method == "HEAD" else payload,
            content_type=prometheus_content_type(),
            content_length=len(payload),
        )

    return app

