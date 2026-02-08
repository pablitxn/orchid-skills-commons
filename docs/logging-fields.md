# Logging Convention (Orchid)

This document defines the baseline structured logging fields for Orchid services.

## Required fields

Every log line must include:

- `timestamp`: ISO-8601 UTC timestamp (`2026-02-07T12:34:56.789Z`)
- `level`: one of `DEBUG|INFO|WARNING|ERROR|CRITICAL`
- `logger`: logger name
- `message`: rendered message string
- `service`: Orchid service name (from `appsettings.service.name`)
- `env`: deployment environment (`development`, `staging`, `production`, etc.)
- `trace_id`: distributed trace id when available
- `span_id`: distributed span id when available
- `request_id`: request correlation id (`x-request-id`, etc.)

## Naming rules

- Use `snake_case` for all field names.
- Keep names stable across services and MCP servers.
- Prefer explicit domain keys in `extra`, for example:
  - `tenant_id`
  - `workflow_id`
  - `provider`
  - `resource_name`

## Configuration

Use `appsettings.*.json`:

```json
{
  "logging": {
    "level": "INFO",
    "format": "json",
    "sampling": 0.25
  }
}
```

- `level`: minimum logger level
- `format`: `json` (default) or `text`
- `sampling`: optional ratio (`0.0..1.0`) applied to low-severity logs (`DEBUG/INFO`)

## Web / MCP correlation binding

At request boundaries, extract and bind correlation IDs before handling business logic:

- `x-request-id`, `request-id`, or `x-correlation-id`
- `x-trace-id` / `x-span-id`
- `traceparent` (W3C), used as fallback source for `trace_id` and `span_id`

`orchid_commons` now includes HTTP helpers to avoid custom middleware wiring:

- FastAPI middleware: `create_fastapi_observability_middleware`
- FastAPI dependency: `create_fastapi_correlation_dependency`
- aiohttp middleware: `create_aiohttp_observability_middleware`
- Generic hook: `http_request_scope`
