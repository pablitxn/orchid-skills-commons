"""HTTP helpers for request correlation and request-span instrumentation."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import Any, TypeAlias
from uuid import uuid4

from orchid_commons.observability.logging import (
    CorrelationIds,
    correlation_scope,
    extract_correlation_ids,
)
from orchid_commons.observability.otel import request_span

AttributeValue: TypeAlias = str | bool | int | float
StatusCodeArg: TypeAlias = int | None | Callable[[], int | None]
FastApiCallNext: TypeAlias = Callable[[Any], Awaitable[Any]]
AiohttpHandler: TypeAlias = Callable[[Any], Awaitable[Any]]


@contextmanager
def http_request_scope(
    *,
    method: str | None,
    route: str | None,
    headers: Mapping[str, str] | None = None,
    span_name: str = "http.server.request",
    status_code: StatusCodeArg = None,
    generate_request_id: bool = True,
    attributes: Mapping[str, AttributeValue | None] | None = None,
) -> Iterator[CorrelationIds]:
    """Bind HTTP correlation IDs and instrument a request span."""
    correlation = _resolve_correlation(
        _coerce_headers(headers or {}),
        generate_request_id=generate_request_id,
    )
    with correlation_scope(
        request_id=correlation.request_id,
        trace_id=correlation.trace_id,
        span_id=correlation.span_id,
    ):
        with request_span(
            span_name,
            method=_clean_method(method),
            route=route,
            request_id=correlation.request_id,
            status_code=status_code,
            attributes=attributes,
        ):
            yield correlation


def create_fastapi_observability_middleware(
    *,
    span_name: str = "http.server.request",
    request_id_response_header: str = "x-request-id",
    set_response_request_id: bool = True,
    generate_request_id: bool = True,
    attributes: Mapping[str, AttributeValue | None] | None = None,
) -> Callable[[Any, FastApiCallNext], Awaitable[Any]]:
    """Build FastAPI middleware that binds correlation IDs and emits request spans."""

    async def middleware(request: Any, call_next: FastApiCallNext) -> Any:
        status_holder: dict[str, int | None] = {"value": None}
        with http_request_scope(
            method=getattr(request, "method", None),
            route=_resolve_fastapi_route(request),
            headers=_coerce_headers(getattr(request, "headers", {})),
            span_name=span_name,
            status_code=lambda: status_holder["value"],
            generate_request_id=generate_request_id,
            attributes=attributes,
        ) as correlation:
            _set_fastapi_request_state(request, correlation)
            try:
                response = await call_next(request)
            except Exception as exc:
                status_holder["value"] = _coerce_status_code(getattr(exc, "status_code", None))
                raise

            status_holder["value"] = _coerce_status_code(getattr(response, "status_code", None))

        if (
            set_response_request_id
            and correlation.request_id is not None
            and hasattr(response, "headers")
        ):
            _set_header_if_missing(response.headers, request_id_response_header, correlation.request_id)
        return response

    return middleware


def create_fastapi_correlation_dependency(
    *,
    generate_request_id: bool = True,
) -> Callable[[Any], AsyncIterator[CorrelationIds]]:
    """Create a FastAPI dependency that binds request correlation IDs."""

    async def dependency(request: Any) -> AsyncIterator[CorrelationIds]:
        correlation = _resolve_correlation(
            _coerce_headers(getattr(request, "headers", {})),
            generate_request_id=generate_request_id,
        )
        with correlation_scope(
            request_id=correlation.request_id,
            trace_id=correlation.trace_id,
            span_id=correlation.span_id,
        ):
            _set_fastapi_request_state(request, correlation)
            yield correlation

    return dependency


def create_aiohttp_observability_middleware(
    *,
    span_name: str = "http.server.request",
    request_id_response_header: str = "x-request-id",
    set_response_request_id: bool = True,
    generate_request_id: bool = True,
    decorate: bool = True,
    attributes: Mapping[str, AttributeValue | None] | None = None,
) -> Callable[[Any, AiohttpHandler], Awaitable[Any]]:
    """Build aiohttp middleware that binds correlation IDs and emits request spans."""

    async def middleware(request: Any, handler: AiohttpHandler) -> Any:
        status_holder: dict[str, int | None] = {"value": None}
        with http_request_scope(
            method=getattr(request, "method", None),
            route=_resolve_aiohttp_route(request),
            headers=_coerce_headers(getattr(request, "headers", {})),
            span_name=span_name,
            status_code=lambda: status_holder["value"],
            generate_request_id=generate_request_id,
            attributes=attributes,
        ) as correlation:
            _set_aiohttp_request_context(request, correlation)
            try:
                response = await handler(request)
            except Exception as exc:
                status_holder["value"] = _coerce_status_code(
                    getattr(exc, "status", None) or getattr(exc, "status_code", None)
                )
                raise

            status_holder["value"] = _coerce_status_code(getattr(response, "status", None))

        if (
            set_response_request_id
            and correlation.request_id is not None
            and hasattr(response, "headers")
        ):
            _set_header_if_missing(response.headers, request_id_response_header, correlation.request_id)
        return response

    if decorate:
        decorator = _load_aiohttp_middleware_decorator()
        if decorator is not None:
            return decorator(middleware)
    return middleware


def _resolve_correlation(
    headers: Mapping[str, str],
    *,
    generate_request_id: bool,
) -> CorrelationIds:
    correlation = extract_correlation_ids(headers)
    if correlation.request_id is not None or not generate_request_id:
        return correlation
    return CorrelationIds(
        request_id=_new_request_id(),
        trace_id=correlation.trace_id,
        span_id=correlation.span_id,
    )


def _new_request_id() -> str:
    return uuid4().hex


def _coerce_headers(headers: object) -> Mapping[str, str]:
    if isinstance(headers, Mapping):
        return {str(key): str(value) for key, value in headers.items()}

    items = getattr(headers, "items", None)
    if callable(items):
        try:
            return {str(key): str(value) for key, value in items()}
        except Exception:
            return {}
    return {}


def _clean_method(method: object) -> str | None:
    if method is None:
        return None
    value = str(method).strip()
    if value == "":
        return None
    return value.upper()


def _coerce_status_code(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _resolve_fastapi_route(request: Any) -> str | None:
    scope = getattr(request, "scope", None)
    if isinstance(scope, Mapping):
        route = scope.get("route")
        for candidate in (
            getattr(route, "path", None),
            getattr(route, "path_format", None),
            scope.get("path"),
        ):
            resolved = _clean_route_value(candidate)
            if resolved is not None:
                return resolved

    return _clean_route_value(
        getattr(
            getattr(request, "url", None),
            "path",
            None,
        )
    )


def _resolve_aiohttp_route(request: Any) -> str | None:
    match_info = getattr(request, "match_info", None)
    route = getattr(match_info, "route", None)
    resource = getattr(route, "resource", None)
    for candidate in (
        getattr(resource, "canonical", None),
        getattr(getattr(request, "rel_url", None), "path", None),
        getattr(request, "path", None),
    ):
        resolved = _clean_route_value(candidate)
        if resolved is not None:
            return resolved
    return None


def _clean_route_value(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if text == "":
        return None
    return text


def _set_fastapi_request_state(request: Any, correlation: CorrelationIds) -> None:
    state = getattr(request, "state", None)
    if state is None:
        return

    try:
        state.correlation_ids = correlation
        state.request_id = correlation.request_id
        state.trace_id = correlation.trace_id
        state.span_id = correlation.span_id
    except Exception:
        return


def _set_aiohttp_request_context(request: Any, correlation: CorrelationIds) -> None:
    try:
        request["correlation_ids"] = correlation
        request["request_id"] = correlation.request_id
        request["trace_id"] = correlation.trace_id
        request["span_id"] = correlation.span_id
    except Exception:
        return


def _set_header_if_missing(headers: Any, key: str, value: str) -> None:
    existing: str | None = None
    getter = getattr(headers, "get", None)
    if callable(getter):
        try:
            result = getter(key)
            if result is not None:
                existing = str(result)
        except Exception:
            existing = None

    if existing:
        return

    try:
        headers[key] = value
    except Exception:
        return


def _load_aiohttp_middleware_decorator() -> Callable[[Any], Any] | None:
    try:
        from aiohttp import web
    except ImportError:
        return None
    return web.middleware


__all__ = [
    "create_aiohttp_observability_middleware",
    "create_fastapi_correlation_dependency",
    "create_fastapi_observability_middleware",
    "http_request_scope",
]
