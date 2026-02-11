"""Tests for generic HTTP error handling middleware."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import pytest

from orchid_commons.observability.http_errors import (
    APIError,
    ErrorResponse,
    _build_error_body,
    _dispatch_exception,
    _log_error,
    _resolve_request_id,
    create_fastapi_error_middleware,
)
from orchid_commons.observability.logging import correlation_scope
from orchid_commons.runtime.errors import OrchidCommonsError

# ---------------------------------------------------------------------------
# Fakes (reuse pattern from test_observability_http)
# ---------------------------------------------------------------------------


class FakeFastApiRequest:
    def __init__(
        self,
        *,
        headers: dict[str, str] | None = None,
        method: str = "GET",
        path: str = "/health",
    ) -> None:
        self.headers = headers or {}
        self.method = method
        self.url = SimpleNamespace(path=path)
        self.scope: dict[str, object] = {"path": path}
        self.state = SimpleNamespace()


class FakeFastApiResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.headers: dict[str, str] = {}


# ---------------------------------------------------------------------------
# APIError hierarchy
# ---------------------------------------------------------------------------


class TestAPIError:
    def test_inherits_from_orchid_commons_error(self) -> None:
        err = APIError(code="TEST", message="boom")
        assert isinstance(err, OrchidCommonsError)
        assert isinstance(err, Exception)

    def test_default_status_code(self) -> None:
        err = APIError(code="BAD", message="bad request")
        assert err.status_code == 400

    def test_custom_status_and_details(self) -> None:
        err = APIError(code="GONE", message="gone", status_code=410, details={"id": "x"})
        assert err.status_code == 410
        assert err.details == {"id": "x"}

    def test_details_default_to_empty_dict(self) -> None:
        err = APIError(code="X", message="x")
        assert err.details == {}

    def test_str_is_message(self) -> None:
        err = APIError(code="X", message="hello")
        assert str(err) == "hello"


# ---------------------------------------------------------------------------
# ErrorResponse dataclass
# ---------------------------------------------------------------------------


class TestErrorResponse:
    def test_frozen(self) -> None:
        resp = ErrorResponse(code="X", message="x")
        with pytest.raises(AttributeError):
            resp.code = "Y"  # type: ignore[misc]

    def test_defaults(self) -> None:
        resp = ErrorResponse(code="X", message="x")
        assert resp.status_code == 400
        assert resp.details == {}
        assert resp.log_level == logging.WARNING


# ---------------------------------------------------------------------------
# _resolve_request_id
# ---------------------------------------------------------------------------


class TestResolveRequestId:
    def test_from_request_state(self) -> None:
        req = FakeFastApiRequest()
        req.state.request_id = "state-id"
        assert _resolve_request_id(req) == "state-id"

    def test_from_correlation_context(self) -> None:
        req = FakeFastApiRequest()
        # state has no request_id attribute set beyond SimpleNamespace default
        with correlation_scope(request_id="corr-id"):
            assert _resolve_request_id(req) == "corr-id"

    def test_fallback_unknown(self) -> None:
        req = FakeFastApiRequest()
        assert _resolve_request_id(req) == "unknown"

    def test_from_dict_style_request(self) -> None:
        req: dict[str, Any] = {"request_id": "dict-id"}
        assert _resolve_request_id(req) == "dict-id"


# ---------------------------------------------------------------------------
# _build_error_body
# ---------------------------------------------------------------------------


class TestBuildErrorBody:
    def test_structure(self) -> None:
        body = _build_error_body("req-1", "NOT_FOUND", "gone", {"id": "42"})
        assert body == {
            "error": {
                "code": "NOT_FOUND",
                "message": "gone",
                "details": {"id": "42"},
                "request_id": "req-1",
            }
        }


# ---------------------------------------------------------------------------
# _dispatch_exception
# ---------------------------------------------------------------------------


class MyAppError(Exception):
    pass


class MySpecificError(MyAppError):
    pass


def _handle_app_error(exc: Exception) -> ErrorResponse:
    return ErrorResponse(code="APP_ERROR", message=str(exc), status_code=422)


def _handle_specific_error(exc: Exception) -> ErrorResponse:
    return ErrorResponse(code="SPECIFIC", message=str(exc), status_code=409)


class TestDispatchException:
    def test_api_error_builtin(self) -> None:
        exc = APIError(code="AUTH", message="unauthorized", status_code=401)
        resp = _dispatch_exception(exc, [], "catch-all")
        assert resp.code == "AUTH"
        assert resp.status_code == 401
        assert resp.log_level == logging.WARNING

    def test_api_error_5xx_uses_error_level(self) -> None:
        exc = APIError(code="FAIL", message="fail", status_code=503)
        resp = _dispatch_exception(exc, [], "catch-all")
        assert resp.log_level == logging.ERROR

    def test_handler_matches_isinstance(self) -> None:
        exc = MyAppError("oops")
        handlers = [(MyAppError, _handle_app_error)]
        resp = _dispatch_exception(exc, handlers, "catch-all")
        assert resp.code == "APP_ERROR"
        assert resp.status_code == 422

    def test_handler_order_respects_specificity(self) -> None:
        """More specific handler first should win."""
        exc = MySpecificError("specific")
        handlers = [
            (MySpecificError, _handle_specific_error),
            (MyAppError, _handle_app_error),
        ]
        resp = _dispatch_exception(exc, handlers, "catch-all")
        assert resp.code == "SPECIFIC"
        assert resp.status_code == 409

    def test_handler_order_general_first_catches_specific(self) -> None:
        """If general handler is first, it catches specific exceptions too."""
        exc = MySpecificError("specific")
        handlers = [
            (MyAppError, _handle_app_error),
            (MySpecificError, _handle_specific_error),
        ]
        resp = _dispatch_exception(exc, handlers, "catch-all")
        assert resp.code == "APP_ERROR"

    def test_catch_all_no_leak(self) -> None:
        exc = RuntimeError("internal secret")
        resp = _dispatch_exception(exc, [], "Something went wrong")
        assert resp.code == "INTERNAL_ERROR"
        assert resp.message == "Something went wrong"
        assert "internal secret" not in resp.message
        assert resp.status_code == 500

    def test_catch_all_uses_critical_log_level(self) -> None:
        exc = RuntimeError("boom")
        resp = _dispatch_exception(exc, [], "oops")
        assert resp.log_level == logging.CRITICAL


# ---------------------------------------------------------------------------
# _log_error
# ---------------------------------------------------------------------------


class TestLogError:
    def test_warning_for_4xx(self, caplog: pytest.LogCaptureFixture) -> None:
        resp = ErrorResponse(code="BAD", message="bad", status_code=400, log_level=logging.WARNING)
        with caplog.at_level(logging.WARNING, logger="orchid_commons.observability.http_errors"):
            _log_error(resp, ValueError("bad"))
        assert any("Client error" in r.message for r in caplog.records)

    def test_error_for_5xx(self, caplog: pytest.LogCaptureFixture) -> None:
        resp = ErrorResponse(code="FAIL", message="fail", status_code=500, log_level=logging.ERROR)
        with caplog.at_level(logging.ERROR, logger="orchid_commons.observability.http_errors"):
            _log_error(resp, RuntimeError("fail"))
        assert any("Server error" in r.message for r in caplog.records)

    def test_traceback_for_catch_all(self, caplog: pytest.LogCaptureFixture) -> None:
        resp = ErrorResponse(
            code="INTERNAL_ERROR", message="oops", status_code=500, log_level=logging.CRITICAL
        )
        try:
            raise RuntimeError("boom")
        except RuntimeError as exc:
            with caplog.at_level(logging.ERROR, logger="orchid_commons.observability.http_errors"):
                _log_error(resp, exc)
        records = [r for r in caplog.records if "Unexpected error" in r.message]
        assert len(records) == 1
        assert records[0].exc_info is not None


# ---------------------------------------------------------------------------
# FastAPI middleware integration
# ---------------------------------------------------------------------------


class TestFastApiErrorMiddleware:
    async def test_passthrough_success(self) -> None:
        middleware = create_fastapi_error_middleware()
        req = FakeFastApiRequest()
        req.state.request_id = "req-ok"

        async def call_next(_: Any) -> FakeFastApiResponse:
            return FakeFastApiResponse(status_code=200)

        resp = await middleware(req, call_next)
        assert resp.status_code == 200

    async def test_handles_api_error(self) -> None:
        middleware = create_fastapi_error_middleware()
        req = FakeFastApiRequest()
        req.state.request_id = "req-api"

        async def call_next(_: Any) -> FakeFastApiResponse:
            raise APIError(
                code="BAD_INPUT", message="invalid", status_code=422, details={"field": "x"}
            )

        resp = await middleware(req, call_next)
        assert resp.status_code == 422
        body = resp.body.decode()
        assert "BAD_INPUT" in body
        assert "req-api" in body

    async def test_handles_registered_exception(self) -> None:
        def handle_value_error(exc: Exception) -> ErrorResponse:
            return ErrorResponse(code="VAL_ERR", message=str(exc), status_code=400)

        middleware = create_fastapi_error_middleware(
            handlers=[(ValueError, handle_value_error)],
        )
        req = FakeFastApiRequest()
        req.state.request_id = "req-val"

        async def call_next(_: Any) -> FakeFastApiResponse:
            raise ValueError("bad value")

        resp = await middleware(req, call_next)
        assert resp.status_code == 400
        body = resp.body.decode()
        assert "VAL_ERR" in body

    async def test_catch_all_returns_500(self) -> None:
        middleware = create_fastapi_error_middleware(catch_all_message="Something broke")
        req = FakeFastApiRequest()
        req.state.request_id = "req-500"

        async def call_next(_: Any) -> FakeFastApiResponse:
            raise RuntimeError("secret internal error")

        resp = await middleware(req, call_next)
        assert resp.status_code == 500
        body = resp.body.decode()
        assert "INTERNAL_ERROR" in body
        assert "Something broke" in body
        assert "secret internal error" not in body

    async def test_request_id_from_correlation_context(self) -> None:
        middleware = create_fastapi_error_middleware()
        req = FakeFastApiRequest()
        # No request_id on state

        async def call_next(_: Any) -> FakeFastApiResponse:
            raise APIError(code="X", message="x")

        with correlation_scope(request_id="corr-123"):
            resp = await middleware(req, call_next)

        body = resp.body.decode()
        assert "corr-123" in body

    async def test_request_id_unknown_fallback(self) -> None:
        middleware = create_fastapi_error_middleware()
        req = FakeFastApiRequest()

        async def call_next(_: Any) -> FakeFastApiResponse:
            raise APIError(code="X", message="x")

        resp = await middleware(req, call_next)
        body = resp.body.decode()
        assert "unknown" in body
