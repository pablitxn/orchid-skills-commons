# Structlog Compatibility Migration

Use this guide to adopt `orchid_commons` logging without a one-shot refactor of
every `structlog` call site.

## Goal

Keep existing event-style calls (`logger.info("event", key=value)`) while moving
runtime formatting and required fields to commons.

## Phase 1: Bootstrap Commons Logging

Configure logging once at startup:

```python
from orchid_commons import bootstrap_logging_from_app_settings, load_config

settings = load_config(config_dir="config", env="production")
bootstrap_logging_from_app_settings(settings, env="production")
```

At this point, required fields (`service`, `env`, `trace_id`, `span_id`,
`request_id`) are emitted by commons formatters.

## Phase 2: Replace Logger Factory (Keep Call Sites)

Swap logger construction only:

```python
from orchid_commons import get_structlog_compat_logger

logger = get_structlog_compat_logger(__name__)
logger.info("bot_started", bot_name="orchid-main")
```

Supported compatibility surface:

- `debug/info/warning/warn/error/exception/critical/fatal/msg`
- `bind(...)`, `new(...)`, `unbind(...)`, `try_unbind(...)`
- event kwargs mapped into structured log fields
- per-call `request_id` / `trace_id` / `span_id` bound through commons correlation context

## Phase 3: Move Correlation to Request Boundaries

For web/MCP handlers, bind correlation IDs in middleware/dependencies using:

- `create_fastapi_observability_middleware`
- `create_aiohttp_observability_middleware`
- `http_request_scope`

Then stop passing `request_id` / `trace_id` / `span_id` on every log line.

## Phase 4: Gradual API Cleanup

After stabilization, optionally migrate hot paths to stdlib logger calls:

```python
import logging

logger = logging.getLogger(__name__)
logger.info("bot started", extra={"bot_name": "orchid-main"})
```

This phase is optional. The compatibility adapter can remain in place while
teams migrate module-by-module.
