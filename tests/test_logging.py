"""Tests for structured logging helpers."""

from __future__ import annotations

import json
import logging
from io import StringIO

from orchid_commons.config import AppSettings
from orchid_commons.observability.logging import (
    bootstrap_logging,
    bootstrap_logging_from_app_settings,
    correlation_scope,
    correlation_scope_from_headers,
    get_correlation_ids,
    get_structlog_compat_logger,
    parse_traceparent,
)


def test_json_logs_include_required_fields() -> None:
    stream = StringIO()
    logger = logging.getLogger("tests.logging.required_fields")

    bootstrap_logging(
        service="skills-api",
        env="production",
        level="INFO",
        log_format="json",
        logger=logger,
        stream=stream,
    )

    with correlation_scope(
        request_id="req-123",
        trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
        span_id="00f067aa0ba902b7",
    ):
        logger.info("hello", extra={"operation": "bootstrap"})

    payload = json.loads(stream.getvalue().strip())

    assert payload["service"] == "skills-api"
    assert payload["env"] == "production"
    assert payload["trace_id"] == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert payload["span_id"] == "00f067aa0ba902b7"
    assert payload["request_id"] == "req-123"
    assert payload["operation"] == "bootstrap"


def test_correlation_scope_from_headers_uses_traceparent() -> None:
    headers = {
        "x-request-id": "req-abc",
        "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
    }

    with correlation_scope_from_headers(headers):
        correlation = get_correlation_ids()
        assert correlation.request_id == "req-abc"
        assert correlation.trace_id == "4bf92f3577b34da6a3ce929d0e0e4736"
        assert correlation.span_id == "00f067aa0ba902b7"

    assert get_correlation_ids().request_id is None
    assert get_correlation_ids().trace_id is None
    assert get_correlation_ids().span_id is None


def test_sampling_zero_drops_info_but_keeps_warning() -> None:
    stream = StringIO()
    logger = logging.getLogger("tests.logging.sampling")

    bootstrap_logging(
        service="skills-api",
        env="staging",
        level="INFO",
        log_format="json",
        sampling=0.0,
        logger=logger,
        stream=stream,
    )

    logger.info("sampled out")
    logger.warning("always keep warning")

    lines = [line for line in stream.getvalue().splitlines() if line.strip()]
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["level"] == "WARNING"
    assert payload["message"] == "always keep warning"


def test_bootstrap_from_app_settings_uses_logging_config() -> None:
    stream = StringIO()
    logger = logging.getLogger("tests.logging.from_app_settings")
    app_settings = AppSettings.model_validate(
        {
            "service": {
                "name": "mcp-gateway",
                "version": "1.0.0",
            },
            "logging": {
                "level": "DEBUG",
                "format": "text",
                "sampling": 1.0,
            },
        }
    )

    bootstrap_logging_from_app_settings(
        app_settings,
        env="development",
        logger=logger,
        stream=stream,
    )
    logger.debug("mcp ready")

    output = stream.getvalue()
    assert "service=mcp-gateway" in output
    assert "env=development" in output
    assert "mcp ready" in output


def test_parse_traceparent_rejects_invalid_format() -> None:
    trace_id, span_id = parse_traceparent("not-a-traceparent")
    assert trace_id is None
    assert span_id is None


def test_structlog_compat_logger_emits_required_fields() -> None:
    stream = StringIO()
    logger = logging.getLogger("tests.logging.structlog.required_fields")

    bootstrap_logging(
        service="matrix-bot",
        env="production",
        level="INFO",
        log_format="json",
        logger=logger,
        stream=stream,
    )

    compat = get_structlog_compat_logger(logger=logger).bind(component="bot_manager")
    compat.info(
        "bot_started",
        request_id="req-789",
        trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
        span_id="00f067aa0ba902b7",
        bot_name="orchid-main",
    )

    payload = json.loads(stream.getvalue().strip())

    assert payload["service"] == "matrix-bot"
    assert payload["env"] == "production"
    assert payload["message"] == "bot_started"
    assert payload["event"] == "bot_started"
    assert payload["component"] == "bot_manager"
    assert payload["bot_name"] == "orchid-main"
    assert payload["request_id"] == "req-789"
    assert payload["trace_id"] == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert payload["span_id"] == "00f067aa0ba902b7"


def test_structlog_compat_exception_includes_stack() -> None:
    stream = StringIO()
    logger = logging.getLogger("tests.logging.structlog.exception")

    bootstrap_logging(
        service="matrix-bot",
        env="staging",
        level="INFO",
        log_format="json",
        logger=logger,
        stream=stream,
    )

    compat = get_structlog_compat_logger(logger=logger)

    try:
        raise RuntimeError("boom")
    except RuntimeError:
        compat.exception("bot_failed", bot_name="orchid-main")

    payload = json.loads(stream.getvalue().strip())
    assert payload["level"] == "ERROR"
    assert payload["message"] == "bot_failed"
    assert payload["bot_name"] == "orchid-main"
    assert "exception" in payload


def test_structlog_compat_moves_logrecord_collisions() -> None:
    stream = StringIO()
    logger = logging.getLogger("tests.logging.structlog.collisions")

    bootstrap_logging(
        service="matrix-bot",
        env="development",
        level="INFO",
        log_format="json",
        logger=logger,
        stream=stream,
    )

    compat = get_structlog_compat_logger(logger=logger)
    compat.info(
        "collision_event",
        name="legacy-name",
        module="legacy-module",
    )

    payload = json.loads(stream.getvalue().strip())
    assert payload["message"] == "collision_event"
    assert payload["event"] == "collision_event"
    assert payload["structlog_conflicts"] == {
        "name": "legacy-name",
        "module": "legacy-module",
    }


def test_structlog_compat_bind_new_unbind() -> None:
    stream = StringIO()
    logger = logging.getLogger("tests.logging.structlog.bind")

    bootstrap_logging(
        service="matrix-bot",
        env="development",
        level="INFO",
        log_format="json",
        logger=logger,
        stream=stream,
    )

    compat = get_structlog_compat_logger(logger=logger, service_name="matrix")
    rebound = compat.bind(bot_name="orchid-main").unbind("service_name")
    reset = rebound.new(component="queue")
    reset.info("queue_ready")

    payload = json.loads(stream.getvalue().strip())
    assert payload["message"] == "queue_ready"
    assert payload["component"] == "queue"
    assert "bot_name" not in payload
    assert "service_name" not in payload
