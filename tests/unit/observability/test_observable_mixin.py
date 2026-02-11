"""Tests for ObservableMixin."""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any, ClassVar
from unittest.mock import MagicMock

import pytest

from orchid_commons.observability._observable import ObservableMixin
from orchid_commons.observability.metrics import NoopMetricsRecorder

# ── Plain class subclass ──────────────────────────────────────────────


class PlainResource(ObservableMixin):
    _resource_name = "plain"

    def __init__(self, *, metrics: Any = None) -> None:
        self._metrics = metrics


# ── Dataclass (slots=True) subclass ───────────────────────────────────


@dataclass(slots=True)
class SlottedResource(ObservableMixin):
    _resource_name: ClassVar[str] = "slotted"

    name: str
    _metrics: Any = None


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture()
def recorder() -> MagicMock:
    return MagicMock(spec=NoopMetricsRecorder)


# ── _metrics_recorder tests ──────────────────────────────────────────


class TestMetricsRecorder:
    def test_returns_injected_recorder(self, recorder: MagicMock) -> None:
        resource = PlainResource(metrics=recorder)
        assert resource._metrics_recorder() is recorder

    def test_falls_back_to_global(self) -> None:
        resource = PlainResource()
        result = resource._metrics_recorder()
        # Should return the global singleton (a NoopMetricsRecorder by default)
        assert result is not None

    def test_slotted_returns_injected(self, recorder: MagicMock) -> None:
        resource = SlottedResource(name="test", _metrics=recorder)
        assert resource._metrics_recorder() is recorder


# ── _observe_operation tests ─────────────────────────────────────────


class TestObserveOperation:
    def test_records_success(self, recorder: MagicMock) -> None:
        resource = PlainResource(metrics=recorder)
        started = perf_counter()
        resource._observe_operation("ping", started, success=True)

        recorder.observe_operation.assert_called_once()
        call_kwargs = recorder.observe_operation.call_args.kwargs
        assert call_kwargs["resource"] == "plain"
        assert call_kwargs["operation"] == "ping"
        assert call_kwargs["success"] is True
        assert call_kwargs["duration_seconds"] >= 0

    def test_records_failure(self, recorder: MagicMock) -> None:
        resource = PlainResource(metrics=recorder)
        started = perf_counter()
        resource._observe_operation("query", started, success=False)

        call_kwargs = recorder.observe_operation.call_args.kwargs
        assert call_kwargs["success"] is False

    def test_slotted_uses_class_resource_name(self, recorder: MagicMock) -> None:
        resource = SlottedResource(name="test", _metrics=recorder)
        started = perf_counter()
        resource._observe_operation("connect", started, success=True)

        call_kwargs = recorder.observe_operation.call_args.kwargs
        assert call_kwargs["resource"] == "slotted"


# ── _observe_error tests ─────────────────────────────────────────────


class TestObserveError:
    def test_records_operation_failure_and_error(self, recorder: MagicMock) -> None:
        resource = PlainResource(metrics=recorder)
        started = perf_counter()
        exc = ValueError("something went wrong")

        resource._observe_error("insert", started, exc)

        # Should call observe_operation with success=False
        op_call = recorder.observe_operation.call_args
        assert op_call.kwargs["resource"] == "plain"
        assert op_call.kwargs["operation"] == "insert"
        assert op_call.kwargs["success"] is False

        # Should call observe_error with error_type
        err_call = recorder.observe_error.call_args
        assert err_call.kwargs["resource"] == "plain"
        assert err_call.kwargs["operation"] == "insert"
        assert err_call.kwargs["error_type"] == "ValueError"

    def test_slotted_observe_error(self, recorder: MagicMock) -> None:
        resource = SlottedResource(name="test", _metrics=recorder)
        started = perf_counter()
        exc = RuntimeError("fail")

        resource._observe_error("close", started, exc)

        err_call = recorder.observe_error.call_args
        assert err_call.kwargs["resource"] == "slotted"
        assert err_call.kwargs["error_type"] == "RuntimeError"


# ── Instance-level _resource_name override ────────────────────────────


class TestInstanceOverride:
    def test_instance_resource_name_overrides_class(self, recorder: MagicMock) -> None:
        resource = PlainResource(metrics=recorder)
        resource._resource_name = "custom"
        started = perf_counter()
        resource._observe_operation("ping", started, success=True)

        call_kwargs = recorder.observe_operation.call_args.kwargs
        assert call_kwargs["resource"] == "custom"
