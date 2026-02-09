"""Structured logging bootstrap and correlation context helpers."""

from __future__ import annotations

import contextvars
import json
import logging
import os
import random
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, TextIO

if TYPE_CHECKING:
    from orchid_commons.config.models import AppSettings

_UNSET = object()

_REQUEST_ID_HEADERS = ("x-request-id", "request-id", "x-correlation-id")
_TRACE_ID_HEADERS = ("x-trace-id", "trace-id")
_SPAN_ID_HEADERS = ("x-span-id", "span-id")

_REQUEST_ID_CTX: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "orchid_request_id",
    default=None,
)
_TRACE_ID_CTX: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "orchid_trace_id",
    default=None,
)
_SPAN_ID_CTX: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "orchid_span_id",
    default=None,
)

_STANDARD_RECORD_KEYS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
    }
)


@dataclass(frozen=True, slots=True)
class CorrelationIds:
    """Request-scoped correlation values used by structured logs."""

    request_id: str | None = None
    trace_id: str | None = None
    span_id: str | None = None


class SamplingFilter(logging.Filter):
    """Sampling filter for low-severity logs."""

    def __init__(self, sampling: float) -> None:
        super().__init__()
        self._sampling = sampling

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.WARNING:
            return True
        return random.random() < self._sampling


class JsonFormatter(logging.Formatter):
    """JSON formatter with required service and correlation fields."""

    def __init__(self, *, service: str, env: str) -> None:
        super().__init__()
        self._service = service
        self._env = env

    def format(self, record: logging.LogRecord) -> str:
        correlation = get_correlation_ids()
        payload: dict[str, Any] = {
            "timestamp": _format_timestamp(record.created),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": self._service,
            "env": self._env,
            "trace_id": correlation.trace_id,
            "span_id": correlation.span_id,
            "request_id": correlation.request_id,
        }

        payload.update(_extract_extra_fields(record))

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str, ensure_ascii=True)


class TextFormatter(logging.Formatter):
    """Plain text formatter that still includes the same correlation context."""

    def __init__(self, *, service: str, env: str) -> None:
        super().__init__("%(asctime)s %(levelname)s %(name)s %(message)s")
        self._service = service
        self._env = env

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        correlation = get_correlation_ids()
        return (
            f"{base} "
            f"service={self._service} env={self._env} "
            f"trace_id={correlation.trace_id or '-'} "
            f"span_id={correlation.span_id or '-'} "
            f"request_id={correlation.request_id or '-'}"
        )


def parse_traceparent(traceparent: str) -> tuple[str | None, str | None]:
    """Parse W3C traceparent and return (trace_id, span_id)."""
    parts = traceparent.strip().split("-")
    if len(parts) != 4:
        return None, None

    version, trace_id, span_id, flags = (part.lower() for part in parts)
    if len(version) != 2 or len(flags) != 2 or len(trace_id) != 32 or len(span_id) != 16:
        return None, None
    if trace_id == "0" * 32 or span_id == "0" * 16:
        return None, None

    for value in (version, trace_id, span_id, flags):
        try:
            int(value, 16)
        except ValueError:
            return None, None

    return trace_id, span_id


def extract_correlation_ids(headers: Mapping[str, str]) -> CorrelationIds:
    """Extract request/trace/span identifiers from incoming headers/metadata."""
    normalized = {str(key).lower(): str(value) for key, value in headers.items()}

    request_id = _first_present(normalized, _REQUEST_ID_HEADERS)
    trace_id = _first_present(normalized, _TRACE_ID_HEADERS)
    span_id = _first_present(normalized, _SPAN_ID_HEADERS)

    traceparent = normalized.get("traceparent")
    if traceparent and (trace_id is None or span_id is None):
        parsed_trace_id, parsed_span_id = parse_traceparent(traceparent)
        if trace_id is None:
            trace_id = parsed_trace_id
        if span_id is None:
            span_id = parsed_span_id

    return CorrelationIds(
        request_id=request_id,
        trace_id=trace_id,
        span_id=span_id,
    )


def get_correlation_ids() -> CorrelationIds:
    """Read correlation IDs from contextvars and active OpenTelemetry span."""
    trace_id = _TRACE_ID_CTX.get()
    span_id = _SPAN_ID_CTX.get()
    otel_trace_id, otel_span_id = _current_otel_trace_context()

    return CorrelationIds(
        request_id=_REQUEST_ID_CTX.get(),
        trace_id=trace_id or otel_trace_id,
        span_id=span_id or otel_span_id,
    )


@contextmanager
def correlation_scope(
    *,
    request_id: str | None | object = _UNSET,
    trace_id: str | None | object = _UNSET,
    span_id: str | None | object = _UNSET,
) -> Iterator[None]:
    """Temporarily bind correlation IDs for the current context."""
    tokens: list[tuple[contextvars.ContextVar[str | None], contextvars.Token[str | None]]] = []

    _bind_if_provided(_REQUEST_ID_CTX, request_id, tokens)
    _bind_if_provided(_TRACE_ID_CTX, trace_id, tokens)
    _bind_if_provided(_SPAN_ID_CTX, span_id, tokens)

    try:
        yield
    finally:
        for context_var, token in reversed(tokens):
            context_var.reset(token)


@contextmanager
def correlation_scope_from_headers(headers: Mapping[str, str]) -> Iterator[CorrelationIds]:
    """Bind correlation IDs extracted from incoming request headers/metadata."""
    correlation = extract_correlation_ids(headers)
    with correlation_scope(
        request_id=correlation.request_id,
        trace_id=correlation.trace_id,
        span_id=correlation.span_id,
    ):
        yield correlation


class StructlogCompatLogger:
    """Compatibility adapter for structlog-style event logging.

    This wrapper accepts calls like:

    - ``logger.info("event_name", user_id="u-1")``
    - ``logger.bind(component="worker").warning("event_name")``

    and emits regular ``logging`` records so they are formatted by the
    commons structured formatters.
    """

    __slots__ = ("_bound_fields", "_logger")

    def __init__(
        self,
        logger: logging.Logger,
        *,
        bound_fields: Mapping[str, Any] | None = None,
    ) -> None:
        self._logger = logger
        self._bound_fields = _coerce_event_fields(bound_fields)

    @property
    def logger(self) -> logging.Logger:
        """Return the wrapped standard-library logger."""
        return self._logger

    @property
    def bound_fields(self) -> dict[str, Any]:
        """Return a copy of currently bound context fields."""
        return dict(self._bound_fields)

    def bind(self, **new_values: Any) -> StructlogCompatLogger:
        """Return a new logger with additional bound context."""
        merged = dict(self._bound_fields)
        merged.update(_coerce_event_fields(new_values))
        return StructlogCompatLogger(self._logger, bound_fields=merged)

    def new(self, **new_values: Any) -> StructlogCompatLogger:
        """Return a new logger replacing any previously bound context."""
        return StructlogCompatLogger(self._logger, bound_fields=new_values)

    def unbind(self, *keys: str) -> StructlogCompatLogger:
        """Return a new logger with specific bound keys removed.

        Raises:
            KeyError: If any requested key is not currently bound.
        """
        merged = dict(self._bound_fields)
        for key in keys:
            key_text = str(key)
            if key_text not in merged:
                raise KeyError(key_text)
            del merged[key_text]
        return StructlogCompatLogger(self._logger, bound_fields=merged)

    def try_unbind(self, *keys: str) -> StructlogCompatLogger:
        """Return a new logger removing keys when present."""
        merged = dict(self._bound_fields)
        for key in keys:
            merged.pop(str(key), None)
        return StructlogCompatLogger(self._logger, bound_fields=merged)

    def is_enabled_for(self, level: int | str) -> bool:
        """Check whether a level is enabled on the wrapped logger."""
        try:
            resolved = _resolve_log_level(level)
        except ValueError:
            return False
        return self._logger.isEnabledFor(resolved)

    def log(self, level: int | str, event: object, *args: object, **event_fields: Any) -> None:
        """Emit a log entry with an arbitrary level."""
        resolved_level = _resolve_log_level(level)
        self._emit(resolved_level, event, *args, **event_fields)

    def debug(self, event: object, *args: object, **event_fields: Any) -> None:
        self._emit(logging.DEBUG, event, *args, **event_fields)

    def info(self, event: object, *args: object, **event_fields: Any) -> None:
        self._emit(logging.INFO, event, *args, **event_fields)

    def warning(self, event: object, *args: object, **event_fields: Any) -> None:
        self._emit(logging.WARNING, event, *args, **event_fields)

    def warn(self, event: object, *args: object, **event_fields: Any) -> None:
        self.warning(event, *args, **event_fields)

    def error(self, event: object, *args: object, **event_fields: Any) -> None:
        self._emit(logging.ERROR, event, *args, **event_fields)

    def exception(self, event: object, *args: object, **event_fields: Any) -> None:
        event_fields.setdefault("exc_info", True)
        self._emit(logging.ERROR, event, *args, **event_fields)

    def critical(self, event: object, *args: object, **event_fields: Any) -> None:
        self._emit(logging.CRITICAL, event, *args, **event_fields)

    def fatal(self, event: object, *args: object, **event_fields: Any) -> None:
        self.critical(event, *args, **event_fields)

    def msg(self, event: object, *args: object, **event_fields: Any) -> None:
        self.info(event, *args, **event_fields)

    def _emit(self, level: int, event: object, *args: object, **event_fields: Any) -> None:
        if not self._logger.isEnabledFor(level):
            return

        merged = dict(self._bound_fields)
        merged.update(_coerce_event_fields(event_fields))

        raw_extra = merged.pop("extra", None)
        exc_info = merged.pop("exc_info", None)
        stack_info = bool(merged.pop("stack_info", False))
        raw_stacklevel = merged.pop("stacklevel", 1)
        stacklevel = raw_stacklevel if isinstance(raw_stacklevel, int) and raw_stacklevel > 0 else 1

        request_id = merged.pop("request_id", _UNSET)
        trace_id = merged.pop("trace_id", _UNSET)
        span_id = merged.pop("span_id", _UNSET)

        payload_fields = _coerce_event_fields(raw_extra)
        payload_fields.update(merged)

        message = _render_event_message(event, args)
        payload_fields.setdefault("event", message)

        log_kwargs: dict[str, Any] = {
            "stacklevel": stacklevel + 2,  # account for wrapper methods
        }
        safe_extra = _sanitize_compat_extra(payload_fields)
        if safe_extra:
            log_kwargs["extra"] = safe_extra
        if exc_info is not None:
            log_kwargs["exc_info"] = exc_info
        if stack_info:
            log_kwargs["stack_info"] = True

        with correlation_scope(
            request_id=request_id,
            trace_id=trace_id,
            span_id=span_id,
        ):
            self._logger.log(level, message, **log_kwargs)


def get_structlog_compat_logger(
    name: str | None = None,
    *,
    logger: logging.Logger | None = None,
    **bound_fields: Any,
) -> StructlogCompatLogger:
    """Build a structlog-style compatibility logger over stdlib logging.

    Args:
        name: Optional logger name used when ``logger`` is not provided.
        logger: Existing stdlib logger to wrap.
        **bound_fields: Optional context fields bound at construction.

    Returns:
        Structlog-style adapter that emits stdlib log records.
    """
    target_logger = logger or (logging.getLogger(name) if name is not None else logging.getLogger())
    return StructlogCompatLogger(target_logger, bound_fields=bound_fields)


def bootstrap_logging(
    *,
    service: str,
    env: str | None = None,
    level: str = "INFO",
    log_format: str = "json",
    sampling: float | None = None,
    logger: logging.Logger | None = None,
    stream: TextIO | None = None,
    force: bool = True,
) -> logging.Logger:
    """Configure a logger with standard formatting and correlation fields."""
    resolved_env = env if env is not None else os.getenv("ORCHID_ENV", "development")
    target_logger = logger or logging.getLogger()

    if force:
        for handler in list(target_logger.handlers):
            target_logger.removeHandler(handler)

    handler = logging.StreamHandler(stream)
    handler.setFormatter(_build_formatter(log_format, service=service, env=resolved_env))

    if sampling is not None and sampling < 1.0:
        handler.addFilter(SamplingFilter(sampling))

    target_logger.addHandler(handler)
    target_logger.setLevel(level.upper())
    if target_logger is not logging.getLogger():
        target_logger.propagate = False
    return target_logger


def bootstrap_logging_from_app_settings(
    app_settings: AppSettings,
    *,
    env: str | None = None,
    logger: logging.Logger | None = None,
    stream: TextIO | None = None,
    force: bool = True,
) -> logging.Logger:
    """Bootstrap logging using values from typed appsettings."""
    return bootstrap_logging(
        service=app_settings.service.name,
        env=env,
        level=app_settings.logging.level,
        log_format=app_settings.logging.format,
        sampling=app_settings.logging.sampling,
        logger=logger,
        stream=stream,
        force=force,
    )


def _build_formatter(log_format: str, *, service: str, env: str) -> logging.Formatter:
    if log_format == "text":
        return TextFormatter(service=service, env=env)
    return JsonFormatter(service=service, env=env)


def _first_present(values: Mapping[str, str], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = _clean_optional_string(values.get(key))
        if value is not None:
            return value
    return None


def _bind_if_provided(
    context_var: contextvars.ContextVar[str | None],
    value: str | None | object,
    tokens: list[tuple[contextvars.ContextVar[str | None], contextvars.Token[str | None]]],
) -> None:
    if value is _UNSET:
        return
    token = context_var.set(_clean_optional_string(value))
    tokens.append((context_var, token))


def _clean_optional_string(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if text == "":
        return None
    return text


def _coerce_event_fields(values: Mapping[object, Any] | None) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    if values is None:
        return fields

    for raw_key, value in values.items():
        key = _clean_optional_string(raw_key)
        if key is None:
            continue
        fields[key] = value

    return fields


def _render_event_message(event: object, args: tuple[object, ...]) -> str:
    base_message = "" if event is None else str(event)
    if not args:
        return base_message

    if isinstance(event, str):
        try:
            return event % args
        except Exception:
            pass

    parts = [base_message, *(str(arg) for arg in args)]
    return " ".join(part for part in parts if part)


def _sanitize_compat_extra(fields: Mapping[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    collisions: dict[str, Any] = {}

    for key, value in fields.items():
        if key in _STANDARD_RECORD_KEYS:
            collisions[key] = value
            continue
        safe[key] = value

    if collisions:
        existing = safe.get("structlog_conflicts")
        if isinstance(existing, Mapping):
            merged = _coerce_event_fields(existing)
            merged.update(collisions)
            safe["structlog_conflicts"] = merged
        else:
            safe["structlog_conflicts"] = collisions

    return safe


def _resolve_log_level(level: int | str) -> int:
    if isinstance(level, int):
        return level

    normalized = level.strip().upper()
    if normalized.isdigit():
        return int(normalized)

    resolved = logging.getLevelName(normalized)
    if isinstance(resolved, int):
        return resolved

    raise ValueError(f"Unknown log level: {level!r}")


def _current_otel_trace_context() -> tuple[str | None, str | None]:
    try:
        from opentelemetry import trace as otel_trace
    except Exception:
        return None, None

    span = otel_trace.get_current_span()
    if span is None:
        return None, None

    span_context = span.get_span_context()
    if span_context is None or not getattr(span_context, "is_valid", False):
        return None, None

    return f"{span_context.trace_id:032x}", f"{span_context.span_id:016x}"


def _extract_extra_fields(record: logging.LogRecord) -> dict[str, Any]:
    extras: dict[str, Any] = {}
    for key, value in record.__dict__.items():
        if key in _STANDARD_RECORD_KEYS or key.startswith("_"):
            continue
        if key in {"service", "env", "trace_id", "span_id", "request_id"}:
            continue
        extras[key] = value
    return extras


def _format_timestamp(created: float) -> str:
    timestamp = datetime.fromtimestamp(created, tz=UTC)
    return timestamp.isoformat(timespec="milliseconds").replace("+00:00", "Z")
