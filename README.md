# orchid-skills-commons

Shared resource connections for Orchid ecosystem services and MCPs.

## Setup

This project uses [uv](https://docs.astral.sh/uv/) for dependency management.

### Install uv

```bash
# macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# or with Homebrew
brew install uv
```

### Install dependencies

```bash
# Install all dependencies (recommended for development)
uv sync --extra all --extra dev

# Install only specific extras
uv sync --extra db            # PostgreSQL + Redis + MongoDB support
uv sync --extra sql           # PostgreSQL-only support (legacy alias)
uv sync --extra redis         # Redis-only support
uv sync --extra mongodb       # MongoDB-only support
uv sync --extra rabbitmq      # RabbitMQ async support
uv sync --extra qdrant        # Qdrant vector DB support
uv sync --extra blob          # MinIO/S3 support
uv sync --extra observability # Prometheus + OpenTelemetry + Langfuse support
```

## Structured logging

`orchid_commons` includes a standard logging bootstrap for web services and MCP servers.

```python
import logging

from orchid_commons import bootstrap_logging_from_app_settings, load_config

settings = load_config(config_dir="config", env="development")
bootstrap_logging_from_app_settings(settings, env="development")

logger = logging.getLogger(__name__)
logger.info("service started")
```

Field naming and required structured fields are defined in:

- `docs/logging-fields.md`
- `docs/logging-compat-migration.md`

### Structlog-style compatibility bridge

For incremental migrations, use the compatibility adapter and keep existing
event-style calls:

```python
from orchid_commons import get_structlog_compat_logger

logger = get_structlog_compat_logger(__name__).bind(component="bot_manager")
logger.info("bot_started", bot_name="orchid-main")
```

## Prometheus Metrics

`orchid_commons` provides a Prometheus metrics layer for resource/runtime operations
using the `orchid_*` naming convention.

### Quick start

```python
from orchid_commons import (
    ResourceManager,
    ResourceSettings,
    SqliteSettings,
    configure_prometheus_metrics,
    start_prometheus_http_server,
)

configure_prometheus_metrics()
start_prometheus_http_server(port=9464)

manager = ResourceManager()
await manager.startup(ResourceSettings(sqlite=SqliteSettings()))
```

Scrape endpoint: `http://localhost:9464/metrics`

### ASGI `/metrics` bridge

```python
from orchid_commons import create_prometheus_asgi_app

metrics_app = create_prometheus_asgi_app()
# app.mount("/metrics", metrics_app)
```

Metric names, labels and Grafana starter queries:
- `docs/prometheus-metrics.md`

## OpenTelemetry (OTLP)

`orchid_commons` can bootstrap OpenTelemetry SDK (traces + metrics) and wire OTLP exporters
from `appsettings`.

### Config example

```json
{
  "observability": {
    "enabled": true,
    "otlp_endpoint": "http://otel-collector:4317",
    "sample_rate": 1.0,
    "otlp_timeout_seconds": 10.0,
    "retry_enabled": true,
    "retry_max_attempts": 3,
    "retry_initial_backoff_seconds": 0.2,
    "retry_max_backoff_seconds": 5.0,
    "metrics_export_interval_seconds": 30.0
  }
}
```

### Bootstrap

```python
from orchid_commons import bootstrap_observability, load_config

settings = load_config(config_dir="config", env="production")
bootstrap_observability(settings)
```

Resource operations already instrumented through the shared recorder (`ResourceManager`,
`SqliteResource`, `PostgresProvider`, `S3BlobStorage`), and you can instrument request handlers
with:

```python
from orchid_commons import request_span

with request_span("http.request", method="GET", route="/health"):
    ...
```

## HTTP Correlation + Request Spans

`orchid_commons` provides framework helpers to bind `request_id` / `trace_id` / `span_id`
and emit request spans with minimal boilerplate.

### FastAPI middleware

```python
from fastapi import FastAPI

from orchid_commons import create_fastapi_observability_middleware

app = FastAPI()
app.middleware("http")(create_fastapi_observability_middleware())
```

### FastAPI dependency (correlation only)

```python
from fastapi import Depends, FastAPI

from orchid_commons import create_fastapi_correlation_dependency

app = FastAPI()
correlation_dependency = create_fastapi_correlation_dependency()

@app.get("/health")
async def health(_: object = Depends(correlation_dependency)) -> dict[str, bool]:
    return {"ok": True}
```

### aiohttp middleware

```python
from aiohttp import web

from orchid_commons import create_aiohttp_observability_middleware

app = web.Application(
    middlewares=[
        create_aiohttp_observability_middleware(),
    ]
)
```

### Generic HTTP hook

```python
from orchid_commons import http_request_scope

status_code: int | None = None
with http_request_scope(
    method=request.method,
    route=request.path,
    headers=request.headers,
    status_code=lambda: status_code,
) as correlation:
    response = await handler(request)
    status_code = response.status
```

## Aggregated health checks (`/health`)

`ResourceManager` can aggregate readiness/liveness checks across all registered resources and
optional observability backends.

```python
from orchid_commons import ResourceManager

manager = ResourceManager()
# ... register/bootstrap resources

report = await manager.health_report()
payload = report.to_dict()  # JSON-serializable
# or directly:
# payload = await manager.health_payload()
```

Returned payload includes:
- per-resource check status + latency (`sqlite`, `postgres`, `redis`, `mongodb`, `rabbitmq`, `qdrant`, `minio`, `r2`, etc.)
- aggregate status (`ok`, `degraded`, `down`)
- readiness/liveness booleans
- optional `otel`/`langfuse` checks when enabled


### Local observability stack example

A complete local stack (Prometheus + Grafana + OTel Collector + Jaeger + demo app) is available at:

- `examples/observability/README.md`

## Blob API (S3-compatible)

`S3BlobStorage` implements the common `BlobStorage` contract for MinIO/S3-compatible
providers (including Cloudflare R2 endpoints).

### Contract

- `upload(key, data, *, content_type=None, metadata=None) -> None`
- `download(key) -> BlobObject`
- `exists(key) -> bool`
- `delete(key) -> None`
- `presign(key, *, method="GET"|"PUT", expires=timedelta(...)) -> str`
- `health_check() -> HealthStatus`

### Typed errors

- `BlobNotFoundError` for missing bucket/object (`404` / `NoSuchKey` / `NoSuchBucket`)
- `BlobAuthError` for auth/permission failures (`401/403` / `AccessDenied`)
- `BlobTransientError` for retryable failures (timeouts, network, `5xx`, `429`)
- `BlobOperationError` for other non-transient backend failures

## MinIO profile (local/dev)

`MinioProfile` builds on top of `S3BlobStorage` and adds:

- local/dev constructor defaults (`minio_local_dev_settings` or `MinioSettings.local_dev`)
- bucket bootstrap helper (`create_bucket_if_missing`)
- MinIO-specific health check with endpoint + bucket details

### Quick start with docker-compose

```bash
docker compose -f docker-compose.minio.yml up -d
```

```python
from orchid_commons import ResourceManager, ResourceSettings, MinioSettings

manager = ResourceManager()
settings = ResourceSettings(minio=MinioSettings.local_dev(bucket="orchid-dev"))

await manager.startup(settings, required=["minio"])
blob = manager.get("minio")  # MinioProfile

await blob.upload("hello.txt", b"hello")
status = await blob.health_check()
```

Integration matrix details (dependencies, containers, env overrides):
- `docs/integration-tests.md`

## Development

### Run tests

```bash
uv run pytest
```

### Run integration tests

```bash
uv sync --extra all --extra dev
uv run pytest -m integration
```

By default, PostgreSQL and MinIO tests use Docker testcontainers.
You can point tests to existing services through env vars documented in:
- `docs/integration-tests.md`

### Run linter

```bash
uv run ruff check .
```

### Format code

```bash
uv run ruff format .
```

### Type checking

```bash
uv run mypy src
```

### Run all checks

```bash
uv run ruff check . && uv run ruff format --check . && uv run pytest
```

## Commons-first service standard

Use this baseline when implementing or migrating Orchid Python services
(including `matrix-orchid-bot`):

- Standard definition: `docs/commons-first-python-quality-standard.md`
- Reusable starter template: `examples/quality/python-service-template/README.md`

## Extras

| Extra           | Description                        | Dependencies                          |
| --------------- | ---------------------------------- | ------------------------------------- |
| `db`            | SQL + cache + document databases   | asyncpg, redis, motor, aio-pika, qdrant-client |
| `sql`           | PostgreSQL async support           | asyncpg                               |
| `redis`         | Redis async cache support          | redis                                 |
| `mongodb`       | MongoDB async support              | motor                                 |
| `rabbitmq`      | RabbitMQ async messaging           | aio-pika                              |
| `qdrant`        | Qdrant vector database             | qdrant-client                         |
| `blob`          | Object storage (MinIO/S3)          | minio                                 |
| `observability` | Tracing and metrics                | prometheus-client, opentelemetry-api, opentelemetry-sdk, opentelemetry-exporter-otlp, langfuse |
| `all`           | All of the above                   | db + blob + observability             |
| `dev`           | Development tools                  | pytest, ruff, mypy                    |

## Database Providers

- `SqliteResource` (`aiosqlite`) for local/dev and lightweight deployments.
  Uses a single shared connection — suitable for CLI tools, MCPs, and
  single-tenant apps. **Not recommended** for multi-request HTTP servers
  under concurrent load (use `PostgresProvider` instead).
- `PostgresProvider` (`asyncpg` pool) for production-like workloads.
- `RedisCache` (`redis.asyncio`) for key/value cache workflows.
- `MongoDbResource` (`motor`) for document storage workflows.
- `RabbitMqBroker` (`aio-pika`) for queue publishing/consumption primitives.
- `QdrantVectorStore` (`qdrant-client`) for vector indexing/search primitives.
- SQL providers share a common query API (`execute`, `executemany`, `fetchone`, `fetchall`,
  `transaction`, `health_check`, `close`), while Redis/MongoDB/RabbitMQ/Qdrant add native helpers.

### Vector contract (shared)

`QdrantVectorStore` implements a common vector contract (`VectorStore`) with:

- `upsert(collection_name, points) -> int`
- `search(collection_name, query_vector, *, limit=10, filters=None, score_threshold=None, with_payload=True, with_vectors=False) -> list[VectorSearchResult]`
- `delete(collection_name, *, ids=None, filters=None) -> int`
- `count(collection_name, *, filters=None) -> int`
- `health_check() -> HealthStatus`
- `close() -> None`

Typed vector errors:
- `VectorAuthError`
- `VectorNotFoundError`
- `VectorTransientError`
- `VectorValidationError`
- `VectorOperationError`

## Integration and Migration Docs

- Playbook Romy + youtube-mcp: `docs/integration-playbook-romy-youtube.md`
- Romy SQLite -> PostgreSQL map: `docs/romy-sqlite-to-postgres.md`
- youtube-mcp extraction map: `docs/youtube-mcp-extraction-map.md`
- Kubernetes Sealed Secrets recommendations: `docs/k8s-sealed-secrets.md`

## Cloudflare R2 (S3-compatible)

`orchid_commons` now supports a dedicated R2 profile on top of the same S3-compatible client flow used by MinIO.

### Environment variables

```bash
# Required auth
ORCHID_R2_ACCESS_KEY=...
ORCHID_R2_SECRET_KEY=...

# One of these endpoint options is required
ORCHID_R2_ACCOUNT_ID=<cloudflare-account-id>
# ORCHID_R2_ENDPOINT=<custom-or-account-endpoint>

# Optional
ORCHID_R2_BUCKET=orchid
ORCHID_R2_CREATE_BUCKET_IF_MISSING=false
ORCHID_R2_SECURE=true
ORCHID_R2_REGION=auto
```

### Notes vs MinIO / AWS S3

- Endpoint derivation: if `ORCHID_R2_ENDPOINT` is not set, endpoint is derived as `<account_id>.r2.cloudflarestorage.com`.
- Transport defaults: R2 defaults to `secure=true`.
- Region defaults: R2 defaults to `region=auto`.
- Auth + presigned URLs: R2 and MinIO both use the same S3-compatible client kwargs contract (`endpoint`, `access_key`, `secret_key`, `secure`, `region`), so presign flows stay identical from callers.

## Langfuse (LLM/Agent traces)

`orchid_commons` includes a Langfuse wrapper with:
- setup from `appsettings` or environment variables,
- decorators for span/generation instrumentation (sync + async),
- safe no-op fallback when disabled or credentials are missing,
- OTel correlation by propagating the active `trace_id`.

### Environment variables

```bash
ORCHID_LANGFUSE_ENABLED=true
ORCHID_LANGFUSE_PUBLIC_KEY=...
ORCHID_LANGFUSE_SECRET_KEY=...
ORCHID_LANGFUSE_BASE_URL=https://cloud.langfuse.com
ORCHID_LANGFUSE_ENVIRONMENT=production
ORCHID_LANGFUSE_RELEASE=1.2.3
ORCHID_LANGFUSE_SAMPLE_RATE=1.0
```

### Quick usage

```python
from orchid_commons import create_langfuse_client

langfuse = create_langfuse_client()

@langfuse.observe_generation(name="agent.answer", model="gpt-4.1-mini")
async def answer(prompt: str) -> str:
    return prompt.upper()
```

## License

MIT
