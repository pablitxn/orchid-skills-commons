"""Tests for OpenTelemetry observability bootstrap helpers."""

from __future__ import annotations

from contextlib import contextmanager

import pytest

import orchid_commons.observability.otel as otel
from orchid_commons.config.models import ObservabilitySettings
from orchid_commons.observability.metrics import get_metrics_recorder, set_metrics_recorder


class FakeInstrument:
    def __init__(self) -> None:
        self.calls: list[tuple[str, float | int, dict[str, object]]] = []

    def add(self, value: int | float, *, attributes: dict[str, object] | None = None) -> None:
        self.calls.append(("add", value, dict(attributes or {})))

    def record(
        self,
        value: int | float,
        *,
        attributes: dict[str, object] | None = None,
    ) -> None:
        self.calls.append(("record", value, dict(attributes or {})))


class FakeMeter:
    def __init__(self) -> None:
        self.instruments: dict[str, FakeInstrument] = {}

    def create_counter(self, name: str, **_: object) -> FakeInstrument:
        instrument = FakeInstrument()
        self.instruments[name] = instrument
        return instrument

    def create_histogram(self, name: str, **_: object) -> FakeInstrument:
        instrument = FakeInstrument()
        self.instruments[name] = instrument
        return instrument


class FakeSpan:
    def __init__(self, name: str, attributes: dict[str, object] | None = None) -> None:
        self.name = name
        self.attributes = dict(attributes or {})
        self.errors: list[Exception] = []
        self.status: object | None = None
        self.ended = False

    def set_attribute(self, key: str, value: object) -> None:
        self.attributes[key] = value

    def record_exception(self, exc: Exception) -> None:
        self.errors.append(exc)

    def set_status(self, status: object) -> None:
        self.status = status

    def end(self, end_time: int | None = None) -> None:
        del end_time
        self.ended = True


class FakeTracer:
    def __init__(self) -> None:
        self.spans: list[FakeSpan] = []

    def start_span(
        self,
        name: str,
        *,
        start_time: int | None = None,
        attributes: dict[str, object] | None = None,
    ) -> FakeSpan:
        del start_time
        span = FakeSpan(name, attributes=attributes)
        self.spans.append(span)
        return span

    @contextmanager
    def start_as_current_span(self, name: str):
        span = FakeSpan(name)
        self.spans.append(span)
        try:
            yield span
        finally:
            span.end()


class FakeTraceModule:
    def __init__(self) -> None:
        self.provider: object | None = None
        self.tracer = FakeTracer()

    def set_tracer_provider(self, provider: object) -> None:
        self.provider = provider

    def get_tracer(self, _: str) -> FakeTracer:
        return self.tracer


class FakeMetricsModule:
    def __init__(self) -> None:
        self.provider: object | None = None
        self.meter = FakeMeter()

    def set_meter_provider(self, provider: object) -> None:
        self.provider = provider

    def get_meter(self, _: str) -> FakeMeter:
        return self.meter


class FakeResourceValue:
    def __init__(self, attributes: dict[str, object]) -> None:
        self.attributes = attributes


class FakeResource:
    @staticmethod
    def create(attributes: dict[str, object]) -> FakeResourceValue:
        return FakeResourceValue(attributes)


class FakeTracerProvider:
    def __init__(self, *, resource: FakeResourceValue, sampler: object) -> None:
        self.resource = resource
        self.sampler = sampler
        self.processors: list[object] = []
        self.shutdown_called = False

    def add_span_processor(self, processor: object) -> None:
        self.processors.append(processor)

    def shutdown(self) -> None:
        self.shutdown_called = True


class FakeMeterProvider:
    def __init__(self, *, resource: FakeResourceValue, metric_readers: list[object]) -> None:
        self.resource = resource
        self.metric_readers = metric_readers
        self.shutdown_called = False

    def shutdown(self) -> None:
        self.shutdown_called = True


class FakeExporter:
    def __init__(self, *_: object, **__: object) -> None:
        self.shutdown_called = False

    def export(self, *_: object, **__: object) -> str:
        return "success"

    def shutdown(self, *_: object, **__: object) -> None:
        self.shutdown_called = True


class FakeBatchSpanProcessor:
    def __init__(self, exporter: object) -> None:
        self.exporter = exporter


class FakeMetricReader:
    def __init__(
        self,
        exporter: object,
        *,
        export_interval_millis: int,
        export_timeout_millis: int,
    ) -> None:
        self.exporter = exporter
        self.export_interval_millis = export_interval_millis
        self.export_timeout_millis = export_timeout_millis


@pytest.fixture(autouse=True)
def reset_observability_state() -> None:
    baseline = get_metrics_recorder()
    otel.shutdown_observability()
    yield
    otel.shutdown_observability()
    set_metrics_recorder(baseline)


def test_bootstrap_disabled_does_not_import_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        otel,
        "_import_otel_sdk_modules",
        lambda: pytest.fail("OTel SDK import should not happen when observability is disabled"),
    )

    handle = otel.bootstrap_observability(ObservabilitySettings(enabled=False))

    assert handle.enabled is False
    assert otel.get_observability_handle() is handle


def test_bootstrap_configures_otlp_and_sets_metrics_recorder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace_module = FakeTraceModule()
    metrics_module = FakeMetricsModule()

    monkeypatch.setattr(
        otel,
        "_import_otel_api_modules",
        lambda: {"trace": trace_module, "metrics": metrics_module},
    )
    monkeypatch.setattr(
        otel,
        "_import_otel_sdk_modules",
        lambda: {
            "trace": trace_module,
            "metrics": metrics_module,
            "OTLPMetricExporter": FakeExporter,
            "OTLPSpanExporter": FakeExporter,
            "MeterProvider": FakeMeterProvider,
            "MetricExportResult": type("MetricExportResult", (), {"SUCCESS": "success"}),
            "PeriodicExportingMetricReader": FakeMetricReader,
            "resource": type("ResourceModule", (), {"Resource": FakeResource}),
            "TracerProvider": FakeTracerProvider,
            "BatchSpanProcessor": FakeBatchSpanProcessor,
            "SpanExportResult": type("SpanExportResult", (), {"SUCCESS": "success"}),
            "TraceIdRatioBased": lambda sample_rate: ("sampler", sample_rate),
        },
    )

    settings = ObservabilitySettings(
        enabled=True,
        otlp_endpoint="http://collector:4317",
        sample_rate=0.25,
        otlp_timeout_seconds=8.0,
        retry_enabled=True,
        retry_max_attempts=4,
        retry_initial_backoff_seconds=0.1,
        retry_max_backoff_seconds=1.0,
        metrics_export_interval_seconds=12.0,
    )

    handle = otel.bootstrap_observability(
        settings,
        service_name="svc-test",
        service_version="1.2.3",
        environment="ci",
    )

    assert handle.enabled is True
    assert handle.otlp_endpoint == "http://collector:4317"
    assert isinstance(get_metrics_recorder(), otel.OpenTelemetryMetricsRecorder)

    assert isinstance(trace_module.provider, FakeTracerProvider)
    assert trace_module.provider.resource.attributes == {
        "service.name": "svc-test",
        "service.version": "1.2.3",
        "deployment.environment": "ci",
    }
    assert len(trace_module.provider.processors) == 1

    assert isinstance(metrics_module.provider, FakeMeterProvider)
    assert len(metrics_module.provider.metric_readers) == 1


def test_otel_metrics_recorder_emits_metrics_and_spans(monkeypatch: pytest.MonkeyPatch) -> None:
    trace_module = FakeTraceModule()
    metrics_module = FakeMetricsModule()

    monkeypatch.setattr(
        otel,
        "_import_otel_api_modules",
        lambda: {"trace": trace_module, "metrics": metrics_module},
    )

    recorder = otel.OpenTelemetryMetricsRecorder()
    recorder.observe_operation(
        resource="sqlite",
        operation="execute",
        duration_seconds=0.5,
        success=False,
    )
    recorder.observe_error(
        resource="sqlite",
        operation="execute",
        error_type="IntegrityError",
    )
    recorder.observe_postgres_pool(
        used_connections=3,
        idle_connections=2,
        min_connections=1,
        max_connections=10,
    )

    operation_counter = metrics_module.meter.instruments["orchid.resources.operations.total"]
    operation_latency = metrics_module.meter.instruments["orchid.resources.operations.duration"]
    errors_counter = metrics_module.meter.instruments["orchid.resources.operations.errors"]
    pool_histogram = metrics_module.meter.instruments["orchid.postgres.pool.connections"]

    assert operation_counter.calls[0][0] == "add"
    assert operation_counter.calls[0][2]["status"] == "error"
    assert operation_latency.calls[0][0] == "record"
    assert errors_counter.calls[0][2]["error.type"] == "IntegrityError"
    assert len(pool_histogram.calls) == 4

    span = trace_module.tracer.spans[-1]
    assert span.name == "resource.sqlite.execute"
    assert span.attributes["resource.status"] == "error"
    assert span.ended is True


def test_bootstrap_twice_without_shutdown_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        otel,
        "_import_otel_sdk_modules",
        lambda: pytest.fail("should not reach SDK import"),
    )

    otel.bootstrap_observability(ObservabilitySettings(enabled=False))

    with pytest.raises(RuntimeError, match="shutdown_observability"):
        otel.bootstrap_observability(ObservabilitySettings(enabled=False))


def test_shutdown_then_rebootstrap_applies_new_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        otel,
        "_import_otel_sdk_modules",
        lambda: pytest.fail("should not reach SDK import"),
    )

    handle_a = otel.bootstrap_observability(ObservabilitySettings(enabled=False))
    assert handle_a.enabled is False

    otel.shutdown_observability()

    handle_b = otel.bootstrap_observability(ObservabilitySettings(enabled=False))
    assert handle_b is not handle_a
    assert otel.get_observability_handle() is handle_b


def test_request_span_records_success_and_error_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    trace_module = FakeTraceModule()
    request_total = FakeInstrument()
    request_duration = FakeInstrument()

    monkeypatch.setattr(otel, "_import_otel_api_trace_module", lambda: trace_module)
    monkeypatch.setattr(
        otel,
        "_ensure_request_instruments",
        lambda: otel._RequestInstruments(total=request_total, duration_seconds=request_duration),
    )

    with otel.request_span("http.request", method="GET", route="/health", status_code=200):
        pass

    assert request_total.calls[0][2]["status"] == "success"

    with pytest.raises(RuntimeError):
        with otel.request_span("http.request", method="POST", route="/items", status_code=500):
            raise RuntimeError("boom")

    assert request_total.calls[1][2]["status"] == "error"


def test_request_span_resolves_status_code_lazily(monkeypatch: pytest.MonkeyPatch) -> None:
    trace_module = FakeTraceModule()
    request_total = FakeInstrument()
    request_duration = FakeInstrument()
    status_holder: dict[str, int | None] = {"value": None}

    monkeypatch.setattr(otel, "_import_otel_api_trace_module", lambda: trace_module)
    monkeypatch.setattr(
        otel,
        "_ensure_request_instruments",
        lambda: otel._RequestInstruments(total=request_total, duration_seconds=request_duration),
    )

    with otel.request_span(
        "http.request",
        method="PATCH",
        route="/lazy-status",
        status_code=lambda: status_holder["value"],
    ):
        status_holder["value"] = 204

    assert request_total.calls[0][2]["http.status_code"] == 204
    span = trace_module.tracer.spans[-1]
    assert span.attributes["http.status_code"] == 204
