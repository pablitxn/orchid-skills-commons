# Observability Local Stack Example

This example provides a local observability stack for `orchid_commons` using:

- Prometheus (metrics scrape)
- Grafana (dashboard)
- OpenTelemetry Collector (OTLP ingest + forwarding)
- Jaeger (trace backend/UI)
- Demo app emitting `orchid_*` metrics and OTLP traces

## Run

From the repository root:

```bash
cd examples/observability
docker compose up --build
```

This single command builds and starts all services.

## Endpoints

- Demo metrics: `http://localhost:9464/metrics`
- Prometheus: `http://localhost:9090`
- Grafana: `http://localhost:3300` (`admin` / `admin`)
- Jaeger: `http://localhost:16686`

Grafana provisions:

- Prometheus datasource
- Jaeger datasource
- `Orchid Commons Local Observability` dashboard

## Data flow

1. Demo app records `orchid_*` Prometheus metrics via `PrometheusMetricsRecorder`.
2. Prometheus scrapes demo app metrics on `demo-app:9464`.
3. Demo app sends OTLP traces (and OTLP metrics) to `otel-collector:4317`.
4. OTel Collector forwards traces to Jaeger.

## Quick verification

1. Open Grafana and load dashboard **Orchid Commons Local Observability**.
2. Confirm panels show throughput, errors, p95 latency and pool gauges.
3. Open Jaeger and search service `orchid-commons-demo`.
4. Confirm traces for `demo.runtime.operation` spans.

## Stop

```bash
docker compose down
```

Add `-v` to remove volumes if needed.
