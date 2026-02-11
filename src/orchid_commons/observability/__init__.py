"""Observability helpers for tracing and telemetry backends."""

from orchid_commons.observability.http import (
    create_aiohttp_observability_middleware,
    create_fastapi_correlation_dependency,
    create_fastapi_observability_middleware,
    http_request_scope,
)
from orchid_commons.observability.http_errors import (
    APIError,
    ErrorResponse,
    create_aiohttp_error_middleware,
    create_fastapi_error_middleware,
)
from orchid_commons.observability.langfuse import (
    LangfuseClient,
    LangfuseClientSettings,
    create_langfuse_client,
    get_default_langfuse_client,
    reset_default_langfuse_client,
    set_default_langfuse_client,
)
from orchid_commons.observability.metrics import reset_metrics_recorder
from orchid_commons.observability.observable import ObservableMixin
from orchid_commons.observability.otel import (
    ObservabilityHandle,
    OpenTelemetryMetricsRecorder,
    OtlpRetrySettings,
    bootstrap_observability,
    get_observability_handle,
    request_span,
    request_span_async,
    shutdown_observability,
    start_span,
)

__all__ = [
    "APIError",
    "ErrorResponse",
    "LangfuseClient",
    "LangfuseClientSettings",
    "ObservabilityHandle",
    "ObservableMixin",
    "OpenTelemetryMetricsRecorder",
    "OtlpRetrySettings",
    "bootstrap_observability",
    "create_aiohttp_error_middleware",
    "create_aiohttp_observability_middleware",
    "create_fastapi_correlation_dependency",
    "create_fastapi_error_middleware",
    "create_fastapi_observability_middleware",
    "create_langfuse_client",
    "get_default_langfuse_client",
    "get_observability_handle",
    "http_request_scope",
    "request_span",
    "request_span_async",
    "reset_default_langfuse_client",
    "reset_metrics_recorder",
    "set_default_langfuse_client",
    "shutdown_observability",
    "start_span",
]
