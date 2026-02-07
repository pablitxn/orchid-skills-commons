# Prometheus Metrics Convention

This package exposes a standard Prometheus layer for Orchid resources and runtime with
the `orchid_*` prefix.

## Standard Metrics

| Metric | Type | Labels | Description |
| --- | --- | --- | --- |
| `orchid_resource_latency_seconds` | Histogram | `resource`, `operation`, `status` | Resource/runtime operation latency. |
| `orchid_resource_throughput_total` | Counter | `resource`, `operation`, `status` | Operation throughput counter. |
| `orchid_resource_errors_total` | Counter | `resource`, `operation`, `error_type` | Error counter by operation and exception class. |
| `orchid_postgres_pool_usage_connections` | Gauge | `state` (`used`,`idle`,`min`,`max`) | Current PostgreSQL pool usage snapshot. |

## Naming and Labels Convention

- Metric names use lowercase snake_case and `orchid_` prefix.
- Labels use lowercase snake_case.
- `resource` examples: `runtime`, `postgres`, `sqlite`, `s3`, `minio`, `cloudflare_r2`.
- `operation` examples: `startup`, `shutdown`, `execute`, `fetchone`, `upload`, `presign_get`.
- `status` values: `success`, `error`.

## Local Scrape

### Background HTTP exporter

```python
from orchid_commons import configure_prometheus_metrics, start_prometheus_http_server

configure_prometheus_metrics()  # sets process default recorder
start_prometheus_http_server(port=9464, host="0.0.0.0")
```

Prometheus scrape target:

```yaml
scrape_configs:
  - job_name: orchid-commons
    static_configs:
      - targets: ["localhost:9464"]
```

### ASGI bridge (`/metrics`)

```python
from orchid_commons import create_prometheus_asgi_app

metrics_app = create_prometheus_asgi_app()
# mount at /metrics in FastAPI/Starlette:
# app.mount("/metrics", metrics_app)
```

## Grafana Base Queries

- Throughput by operation:
  - `sum(rate(orchid_resource_throughput_total{status="success"}[5m])) by (resource, operation)`
- Error rate by operation:
  - `sum(rate(orchid_resource_errors_total[5m])) by (resource, operation, error_type)`
- Latency p95:
  - `histogram_quantile(0.95, sum(rate(orchid_resource_latency_seconds_bucket[5m])) by (le, resource, operation))`
- PostgreSQL pool used/idle:
  - `orchid_postgres_pool_usage_connections{state=~"used|idle"}`
