"""OpenTelemetry bootstrap helpers and request/resource instrumentation."""

from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from orchid_commons.observability.metrics import (
    MetricsRecorder,
    get_metrics_recorder,
    set_metrics_recorder,
)
from orchid_commons.runtime.errors import MissingDependencyError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator, Mapping

    from orchid_commons.config.models import AppSettings, ObservabilitySettings

AttributeValue = str | bool | int | float

_METER_NAME = "orchid_commons"
_TRACER_NAME = "orchid_commons"
_OBSERVABILITY_HANDLE: ObservabilityHandle | None = None
_REQUEST_INSTRUMENTS: _RequestInstruments | None = None


@dataclass(slots=True, frozen=True)
class OtlpRetrySettings:
    """Retry configuration for exporter wrappers."""

    enabled: bool
    max_attempts: int
    initial_backoff_seconds: float
    max_backoff_seconds: float


@dataclass(slots=True)
class ObservabilityHandle:
    """Runtime handle returned by ``bootstrap_observability``."""

    enabled: bool
    otlp_endpoint: str | None = None
    tracer_provider: Any | None = None
    meter_provider: Any | None = None
    previous_metrics_recorder: MetricsRecorder | None = None

    def shutdown(self) -> None:
        """Flush and close all configured OpenTelemetry providers."""
        if self.meter_provider is not None:
            self.meter_provider.shutdown()
        if self.tracer_provider is not None:
            self.tracer_provider.shutdown()


@dataclass(slots=True)
class _RequestInstruments:
    total: Any
    duration_seconds: Any


class _RetryingExporter:
    """Synchronous exporter wrapper with exponential-backoff retries."""

    def __init__(
        self,
        exporter: Any,
        *,
        success_value: Any,
        retry: OtlpRetrySettings,
    ) -> None:
        self._exporter = exporter
        self._success_value = success_value
        self._retry = retry

    def export(self, *args: Any, **kwargs: Any) -> Any:
        attempt = 1
        while True:
            result = self._exporter.export(*args, **kwargs)
            if not self._retry.enabled or result == self._success_value:
                return result
            if attempt >= self._retry.max_attempts:
                return result

            delay_seconds = min(
                self._retry.max_backoff_seconds,
                self._retry.initial_backoff_seconds * (2 ** (attempt - 1)),
            )
            # Intentional blocking sleep: runs inside BatchSpanProcessor's background
            # thread, not an async context, so time.sleep() is correct here.
            time.sleep(delay_seconds)
            attempt += 1

    def shutdown(self, *args: Any, **kwargs: Any) -> Any:
        return self._exporter.shutdown(*args, **kwargs)

    def force_flush(self, *args: Any, **kwargs: Any) -> Any:
        force_flush = getattr(self._exporter, "force_flush", None)
        if callable(force_flush):
            return force_flush(*args, **kwargs)
        return True

    def __getattr__(self, name: str) -> Any:
        return getattr(self._exporter, name)


class OpenTelemetryMetricsRecorder:
    """Metrics recorder implementation backed by OpenTelemetry metrics + traces."""

    def __init__(self) -> None:
        api_modules = _import_otel_api_modules()
        self._tracer = api_modules["trace"].get_tracer(_TRACER_NAME)
        meter = api_modules["metrics"].get_meter(_METER_NAME)

        self._operation_total = meter.create_counter(
            "orchid.resources.operations.total",
            description="Count of shared resource operations",
        )
        self._operation_duration_seconds = meter.create_histogram(
            "orchid.resources.operations.duration",
            unit="s",
            description="Latency of shared resource operations",
        )
        self._errors = meter.create_counter(
            "orchid.resources.operations.errors",
            description="Count of failed shared resource operations",
        )
        self._postgres_pool = meter.create_histogram(
            "orchid.postgres.pool.connections",
            unit="1",
            description="Snapshot of PostgreSQL pool connection counts",
        )

    def observe_operation(
        self,
        *,
        resource: str,
        operation: str,
        duration_seconds: float,
        success: bool,
    ) -> None:
        duration = max(0.0, duration_seconds)
        attributes: dict[str, AttributeValue] = {
            "resource.name": resource,
            "resource.operation": operation,
            "status": "success" if success else "error",
        }

        self._operation_total.add(1, attributes=attributes)
        self._operation_duration_seconds.record(duration, attributes=attributes)
        self._emit_operation_span(
            resource=resource,
            operation=operation,
            duration_seconds=duration,
            success=success,
        )

    def observe_error(
        self,
        *,
        resource: str,
        operation: str,
        error_type: str,
    ) -> None:
        attributes: dict[str, AttributeValue] = {
            "resource.name": resource,
            "resource.operation": operation,
            "error.type": error_type,
        }
        self._errors.add(1, attributes=attributes)

    def observe_postgres_pool(
        self,
        *,
        used_connections: int,
        idle_connections: int,
        min_connections: int,
        max_connections: int,
    ) -> None:
        snapshots = {
            "used": used_connections,
            "idle": idle_connections,
            "min": min_connections,
            "max": max_connections,
        }
        for state, value in snapshots.items():
            self._postgres_pool.record(
                max(0.0, float(value)),
                attributes={"state": state},
            )

    def _emit_operation_span(
        self,
        *,
        resource: str,
        operation: str,
        duration_seconds: float,
        success: bool,
    ) -> None:
        end_time_ns = time.time_ns()
        start_time_ns = end_time_ns - int(duration_seconds * 1_000_000_000)
        status_name = "success" if success else "error"
        span = self._tracer.start_span(
            f"resource.{resource}.{operation}",
            start_time=start_time_ns,
            attributes={
                "resource.name": resource,
                "resource.operation": operation,
                "resource.status": status_name,
                "operation.duration_seconds": duration_seconds,
            },
        )
        try:
            try:
                from opentelemetry.trace import Status, StatusCode
            except ImportError:
                pass
            else:
                code = StatusCode.OK if success else StatusCode.ERROR
                span.set_status(Status(code))
        finally:
            span.end(end_time=end_time_ns)


def bootstrap_observability(
    settings: AppSettings | ObservabilitySettings,
    *,
    service_name: str | None = None,
    service_version: str | None = None,
    environment: str | None = None,
) -> ObservabilityHandle:
    """Bootstrap OpenTelemetry SDK providers and configure OTLP export."""
    global _OBSERVABILITY_HANDLE, _REQUEST_INSTRUMENTS

    if _OBSERVABILITY_HANDLE is not None:
        return _OBSERVABILITY_HANDLE

    obs_settings, resolved_service_name, resolved_service_version, resolved_environment = (
        _resolve_observability_input(
            settings,
            service_name=service_name,
            service_version=service_version,
            environment=environment,
        )
    )

    if not obs_settings.enabled:
        _OBSERVABILITY_HANDLE = ObservabilityHandle(enabled=False)
        return _OBSERVABILITY_HANDLE

    modules = _import_otel_sdk_modules()
    trace_module = modules["trace"]
    metrics_module = modules["metrics"]
    resource_module = modules["resource"]

    resource_attributes: dict[str, AttributeValue] = {"service.name": resolved_service_name}
    if resolved_service_version is not None:
        resource_attributes["service.version"] = resolved_service_version
    if resolved_environment is not None:
        resource_attributes["deployment.environment"] = resolved_environment

    resource_factory = getattr(resource_module, "Resource", resource_module)
    resource = resource_factory.create(resource_attributes)
    retry_settings = OtlpRetrySettings(
        enabled=obs_settings.retry_enabled,
        max_attempts=obs_settings.retry_max_attempts,
        initial_backoff_seconds=obs_settings.retry_initial_backoff_seconds,
        max_backoff_seconds=obs_settings.retry_max_backoff_seconds,
    )

    tracer_provider = modules["TracerProvider"](
        resource=resource,
        sampler=modules["TraceIdRatioBased"](obs_settings.sample_rate),
    )

    metric_readers: list[Any] = []
    otlp_endpoint = obs_settings.otlp_endpoint
    if otlp_endpoint:
        span_exporter = modules["OTLPSpanExporter"](
            endpoint=otlp_endpoint,
            insecure=obs_settings.otlp_insecure,
            timeout=obs_settings.otlp_timeout_seconds,
        )
        span_exporter = _RetryingExporter(
            span_exporter,
            success_value=modules["SpanExportResult"].SUCCESS,
            retry=retry_settings,
        )
        tracer_provider.add_span_processor(modules["BatchSpanProcessor"](span_exporter))

        metric_exporter = modules["OTLPMetricExporter"](
            endpoint=otlp_endpoint,
            insecure=obs_settings.otlp_insecure,
            timeout=obs_settings.otlp_timeout_seconds,
        )
        metric_exporter = _RetryingExporter(
            metric_exporter,
            success_value=modules["MetricExportResult"].SUCCESS,
            retry=retry_settings,
        )
        metric_readers.append(
            modules["PeriodicExportingMetricReader"](
                metric_exporter,
                export_interval_millis=int(obs_settings.metrics_export_interval_seconds * 1000),
                export_timeout_millis=int(obs_settings.otlp_timeout_seconds * 1000),
            )
        )

    meter_provider = modules["MeterProvider"](
        resource=resource,
        metric_readers=metric_readers,
    )

    # Process-level bootstrap: expected once at service startup.
    trace_module.set_tracer_provider(tracer_provider)
    metrics_module.set_meter_provider(meter_provider)

    previous_recorder = get_metrics_recorder()
    set_metrics_recorder(OpenTelemetryMetricsRecorder())
    _REQUEST_INSTRUMENTS = None

    _OBSERVABILITY_HANDLE = ObservabilityHandle(
        enabled=True,
        otlp_endpoint=otlp_endpoint,
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
        previous_metrics_recorder=previous_recorder,
    )
    return _OBSERVABILITY_HANDLE


def shutdown_observability() -> None:
    """Shutdown providers configured by ``bootstrap_observability``."""
    global _OBSERVABILITY_HANDLE, _REQUEST_INSTRUMENTS
    if _OBSERVABILITY_HANDLE is None:
        return

    previous_recorder = _OBSERVABILITY_HANDLE.previous_metrics_recorder
    if previous_recorder is not None:
        set_metrics_recorder(previous_recorder)

    _OBSERVABILITY_HANDLE.shutdown()
    _OBSERVABILITY_HANDLE = None
    _REQUEST_INSTRUMENTS = None


def get_observability_handle() -> ObservabilityHandle | None:
    """Return the current observability handle, if bootstrap has been executed."""
    return _OBSERVABILITY_HANDLE


@contextmanager
def start_span(
    span_name: str,
    *,
    attributes: Mapping[str, AttributeValue | None] | None = None,
) -> Iterator[Any | None]:
    """Start a span when OpenTelemetry API is available; otherwise no-op."""
    trace_module = _import_otel_api_trace_module()
    if trace_module is None:
        yield None
        return

    tracer = trace_module.get_tracer(_TRACER_NAME)
    with tracer.start_as_current_span(span_name) as span:
        for key, value in (attributes or {}).items():
            if value is not None:
                span.set_attribute(key, value)
        yield span


@contextmanager
def request_span(
    span_name: str,
    *,
    method: str | None = None,
    route: str | None = None,
    request_id: str | None = None,
    status_code: int | None | Callable[[], int | None] = None,
    attributes: Mapping[str, AttributeValue | None] | None = None,
) -> Iterator[Any | None]:
    """Instrument a request-like operation with base span and metrics."""
    span_attributes: dict[str, AttributeValue | None] = dict(attributes or {})
    span_attributes["operation.type"] = "request"
    span_attributes["request.method"] = method.upper() if method else None
    span_attributes["request.route"] = route
    span_attributes["request.id"] = request_id

    started = time.perf_counter()
    with start_span(span_name, attributes=span_attributes) as span:
        try:
            yield span
        except Exception as exc:
            _mark_span_error(span, exc)
            resolved_status_code = _resolve_status_code(status_code)
            if resolved_status_code is not None and span is not None:
                span.set_attribute("http.status_code", resolved_status_code)
            _record_request_metrics(
                method=method,
                route=route,
                status_code=resolved_status_code,
                duration_seconds=time.perf_counter() - started,
                success=False,
            )
            raise
        else:
            resolved_status_code = _resolve_status_code(status_code)
            if resolved_status_code is not None and span is not None:
                span.set_attribute("http.status_code", resolved_status_code)
            _record_request_metrics(
                method=method,
                route=route,
                status_code=resolved_status_code,
                duration_seconds=time.perf_counter() - started,
                success=True,
            )


@asynccontextmanager
async def request_span_async(
    span_name: str,
    *,
    method: str | None = None,
    route: str | None = None,
    request_id: str | None = None,
    status_code: int | None = None,
    attributes: Mapping[str, AttributeValue | None] | None = None,
) -> AsyncIterator[Any | None]:
    """Async wrapper for ``request_span``."""
    with request_span(
        span_name,
        method=method,
        route=route,
        request_id=request_id,
        status_code=status_code,
        attributes=attributes,
    ) as span:
        yield span


def _resolve_observability_input(
    settings: AppSettings | ObservabilitySettings,
    *,
    service_name: str | None,
    service_version: str | None,
    environment: str | None,
) -> tuple[ObservabilitySettings, str, str | None, str | None]:
    if hasattr(settings, "observability") and hasattr(settings, "service"):
        app_settings = settings
        observability_settings = app_settings.observability
        resolved_service_name = (
            service_name
            or observability_settings.service_name
            or app_settings.service.name
            or "orchid-service"
        )
        resolved_service_version = service_version or app_settings.service.version
        return observability_settings, resolved_service_name, resolved_service_version, environment

    observability_settings = settings
    resolved_service_name = service_name or observability_settings.service_name or "orchid-service"
    return observability_settings, resolved_service_name, service_version, environment


def _import_otel_api_modules() -> dict[str, Any]:
    try:
        from opentelemetry import metrics, trace
    except ImportError as exc:  # pragma: no cover - depends on optional extras
        raise MissingDependencyError(
            "OpenTelemetry support requires optional observability dependencies. "
            "Install with: uv sync --extra observability"
        ) from exc
    return {"metrics": metrics, "trace": trace}


def _import_otel_sdk_modules() -> dict[str, Any]:
    try:
        from opentelemetry import metrics, trace
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
            OTLPMetricExporter,
        )
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import (
            MetricExportResult,
            PeriodicExportingMetricReader,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExportResult
        from opentelemetry.sdk.trace.sampling import TraceIdRatioBased
    except ImportError as exc:  # pragma: no cover - depends on optional extras
        raise MissingDependencyError(
            "OpenTelemetry bootstrap requires optional observability dependencies. "
            "Install with: uv sync --extra observability"
        ) from exc

    return {
        "metrics": metrics,
        "trace": trace,
        "OTLPMetricExporter": OTLPMetricExporter,
        "OTLPSpanExporter": OTLPSpanExporter,
        "MeterProvider": MeterProvider,
        "MetricExportResult": MetricExportResult,
        "PeriodicExportingMetricReader": PeriodicExportingMetricReader,
        "resource": Resource,
        "TracerProvider": TracerProvider,
        "BatchSpanProcessor": BatchSpanProcessor,
        "SpanExportResult": SpanExportResult,
        "TraceIdRatioBased": TraceIdRatioBased,
    }


def _import_otel_api_trace_module() -> Any | None:
    try:
        from opentelemetry import trace
    except ImportError:
        return None
    return trace


def _ensure_request_instruments() -> _RequestInstruments | None:
    global _REQUEST_INSTRUMENTS
    if _REQUEST_INSTRUMENTS is not None:
        return _REQUEST_INSTRUMENTS

    try:
        from opentelemetry import metrics
    except ImportError:
        return None

    meter = metrics.get_meter(_METER_NAME)
    _REQUEST_INSTRUMENTS = _RequestInstruments(
        total=meter.create_counter(
            "orchid.requests.total",
            description="Count of instrumented request operations",
        ),
        duration_seconds=meter.create_histogram(
            "orchid.requests.duration",
            unit="s",
            description="Duration of instrumented request operations",
        ),
    )
    return _REQUEST_INSTRUMENTS


def _record_request_metrics(
    *,
    method: str | None,
    route: str | None,
    status_code: int | None,
    duration_seconds: float,
    success: bool,
) -> None:
    instruments = _ensure_request_instruments()
    if instruments is None:
        return

    attributes: dict[str, AttributeValue] = {
        "status": "success" if success else "error",
    }
    if method is not None:
        attributes["request.method"] = method.upper()
    if route is not None:
        attributes["request.route"] = route
    if status_code is not None:
        attributes["http.status_code"] = status_code

    instruments.total.add(1, attributes=attributes)
    instruments.duration_seconds.record(max(0.0, duration_seconds), attributes=attributes)


def _resolve_status_code(
    status_code: int | None | Callable[[], int | None],
) -> int | None:
    value: int | None
    if callable(status_code):
        try:
            value = status_code()
        except Exception:
            return None
    else:
        value = status_code

    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _mark_span_error(span: Any | None, exc: Exception) -> None:
    if span is None:
        return

    span.record_exception(exc)
    try:
        from opentelemetry.trace import Status, StatusCode
    except ImportError:
        return
    span.set_status(Status(StatusCode.ERROR, str(exc)))
