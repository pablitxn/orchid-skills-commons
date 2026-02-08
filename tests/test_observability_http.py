"""Tests for HTTP observability helpers."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest

import orchid_commons.observability.http as http_observability
from orchid_commons.observability.logging import get_correlation_ids

_TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"


class FakeFastApiRequest:
    def __init__(
        self,
        *,
        headers: dict[str, str] | None = None,
        method: str = "GET",
        path: str = "/health",
        route: str | None = "/health",
    ) -> None:
        self.headers = headers or {}
        self.method = method
        self.url = SimpleNamespace(path=path)
        self.scope: dict[str, object] = {"path": path}
        if route is not None:
            self.scope["route"] = SimpleNamespace(path=route)
        self.state = SimpleNamespace()


class FakeFastApiResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.headers: dict[str, str] = {}


class FakeAiohttpRequest(dict[str, object]):
    def __init__(
        self,
        *,
        headers: dict[str, str] | None = None,
        method: str = "GET",
        path: str = "/health",
        canonical: str | None = "/health",
    ) -> None:
        super().__init__()
        self.headers = headers or {}
        self.method = method
        self.path = path
        self.rel_url = SimpleNamespace(path=path)
        resource = SimpleNamespace(canonical=canonical)
        self.match_info = SimpleNamespace(route=SimpleNamespace(resource=resource))


class FakeAiohttpResponse:
    def __init__(self, status: int) -> None:
        self.status = status
        self.headers: dict[str, str] = {}


class FakeHttpException(Exception):
    def __init__(self, status: int) -> None:
        self.status = status
        super().__init__(f"http status {status}")


def _install_span_recorder(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    @contextmanager
    def fake_request_span(span_name: str, **kwargs: Any):
        call = {"span_name": span_name, "kwargs": kwargs}
        calls.append(call)
        try:
            yield None
        finally:
            status_code = kwargs.get("status_code")
            call["resolved_status_code"] = status_code() if callable(status_code) else status_code

    monkeypatch.setattr(http_observability, "request_span", fake_request_span)
    return calls


def test_http_request_scope_binds_and_clears_correlation() -> None:
    headers = {
        "x-request-id": "req-123",
        "traceparent": _TRACEPARENT,
    }

    with http_observability.http_request_scope(
        method="GET",
        route="/health",
        headers=headers,
        status_code=200,
    ) as correlation:
        assert correlation.request_id == "req-123"
        assert correlation.trace_id == "4bf92f3577b34da6a3ce929d0e0e4736"
        assert correlation.span_id == "00f067aa0ba902b7"
        current = get_correlation_ids()
        assert current.request_id == "req-123"
        assert current.trace_id == "4bf92f3577b34da6a3ce929d0e0e4736"
        assert current.span_id == "00f067aa0ba902b7"

    assert get_correlation_ids().request_id is None
    assert get_correlation_ids().trace_id is None
    assert get_correlation_ids().span_id is None


async def test_fastapi_middleware_binds_context_and_sets_response_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    span_calls = _install_span_recorder(monkeypatch)
    middleware = http_observability.create_fastapi_observability_middleware(
        span_name="http.fastapi.request"
    )
    request = FakeFastApiRequest(
        headers={"x-request-id": "req-fastapi", "traceparent": _TRACEPARENT},
        method="post",
        path="/items/1",
        route="/items/{item_id}",
    )

    async def call_next(_: FakeFastApiRequest) -> FakeFastApiResponse:
        correlation = get_correlation_ids()
        assert correlation.request_id == "req-fastapi"
        assert correlation.trace_id == "4bf92f3577b34da6a3ce929d0e0e4736"
        assert correlation.span_id == "00f067aa0ba902b7"
        return FakeFastApiResponse(status_code=201)

    response = await middleware(request, call_next)

    assert response.headers["x-request-id"] == "req-fastapi"
    assert request.state.request_id == "req-fastapi"
    assert request.state.trace_id == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert request.state.span_id == "00f067aa0ba902b7"
    assert span_calls[0]["span_name"] == "http.fastapi.request"
    assert span_calls[0]["kwargs"]["method"] == "POST"
    assert span_calls[0]["kwargs"]["route"] == "/items/{item_id}"
    assert span_calls[0]["kwargs"]["request_id"] == "req-fastapi"
    assert span_calls[0]["resolved_status_code"] == 201
    assert get_correlation_ids().request_id is None


async def test_fastapi_middleware_generates_request_id_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    span_calls = _install_span_recorder(monkeypatch)
    monkeypatch.setattr(http_observability, "_new_request_id", lambda: "generated-request-id")
    middleware = http_observability.create_fastapi_observability_middleware()
    request = FakeFastApiRequest(headers={}, path="/generated")

    async def call_next(_: FakeFastApiRequest) -> FakeFastApiResponse:
        assert get_correlation_ids().request_id == "generated-request-id"
        return FakeFastApiResponse(status_code=204)

    response = await middleware(request, call_next)

    assert response.headers["x-request-id"] == "generated-request-id"
    assert request.state.request_id == "generated-request-id"
    assert span_calls[0]["kwargs"]["request_id"] == "generated-request-id"


async def test_fastapi_correlation_dependency_binds_and_clears_scope() -> None:
    request = FakeFastApiRequest(headers={"x-request-id": "req-dependency"})
    dependency = http_observability.create_fastapi_correlation_dependency()
    generator = dependency(request)

    correlation = await anext(generator)
    assert correlation.request_id == "req-dependency"
    assert get_correlation_ids().request_id == "req-dependency"
    assert request.state.request_id == "req-dependency"

    await generator.aclose()
    assert get_correlation_ids().request_id is None


async def test_aiohttp_middleware_binds_context_and_sets_request_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    span_calls = _install_span_recorder(monkeypatch)
    middleware = http_observability.create_aiohttp_observability_middleware(decorate=False)
    request = FakeAiohttpRequest(
        headers={"x-correlation-id": "req-aiohttp", "traceparent": _TRACEPARENT},
        method="PUT",
        path="/workers/9",
        canonical="/workers/{worker_id}",
    )

    async def handler(_: FakeAiohttpRequest) -> FakeAiohttpResponse:
        correlation = get_correlation_ids()
        assert correlation.request_id == "req-aiohttp"
        return FakeAiohttpResponse(status=202)

    response = await middleware(request, handler)

    assert request["request_id"] == "req-aiohttp"
    assert request["trace_id"] == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert request["span_id"] == "00f067aa0ba902b7"
    assert response.headers["x-request-id"] == "req-aiohttp"
    assert span_calls[0]["kwargs"]["method"] == "PUT"
    assert span_calls[0]["kwargs"]["route"] == "/workers/{worker_id}"
    assert span_calls[0]["resolved_status_code"] == 202
    assert get_correlation_ids().request_id is None


async def test_aiohttp_middleware_tracks_exception_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    span_calls = _install_span_recorder(monkeypatch)
    middleware = http_observability.create_aiohttp_observability_middleware(decorate=False)
    request = FakeAiohttpRequest(
        headers={"x-request-id": "req-aio-error"},
        method="GET",
        path="/error",
        canonical="/error",
    )

    async def handler(_: FakeAiohttpRequest) -> FakeAiohttpResponse:
        raise FakeHttpException(status=404)

    with pytest.raises(FakeHttpException):
        await middleware(request, handler)

    assert span_calls[0]["resolved_status_code"] == 404
    assert get_correlation_ids().request_id is None
