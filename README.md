# orchid-mcp-commons

Shared resource connections for Orchid ecosystem services and MCPs.

`orchid_commons` centralizes:
- typed configuration loading (`appsettings*.json` + environment placeholders),
- async resource lifecycle management,
- data/storage connectors (SQL, cache, document, queue, vector, blob),
- observability primitives (logging, Prometheus, OpenTelemetry, Langfuse),
- framework helpers for HTTP correlation and request tracing.

## Requirements

- Python `>=3.11`

## Installation

```bash
uv add orchid-mcp-commons
# or
pip install orchid-mcp-commons
```

Install with extras when you need specific integrations:

```bash
# broad runtime profile
uv add "orchid-mcp-commons[all]"

# focused profiles
uv add "orchid-mcp-commons[postgres]"
uv add "orchid-mcp-commons[blob]"
uv add "orchid-mcp-commons[observability]"
```

## Optional extras

| Extra | Description | Key packages |
| --- | --- | --- |
| `sqlite` | SQLite resource | `aiosqlite` |
| `sql` | PostgreSQL alias (legacy name) | `asyncpg` |
| `postgres` | PostgreSQL resource | `asyncpg` |
| `redis` | Redis cache resource | `redis` |
| `mongodb` | MongoDB resource | `motor` |
| `rabbitmq` | RabbitMQ broker resource | `aio-pika` |
| `qdrant` | Qdrant vector store | `qdrant-client` |
| `pgvector` | pgvector helpers (with PostgreSQL) | `pgvector`, `asyncpg` |
| `blob` | MinIO/S3 + R2 + multi-bucket router | `minio` |
| `http` | HTTP framework integrations | `fastapi`, `starlette`, `aiohttp` |
| `observability` | Metrics/tracing/Langfuse | `prometheus-client`, `opentelemetry-*`, `langfuse` |
| `db` | Combined data connectors | sqlite + postgres + redis + mongodb + rabbitmq + qdrant + pgvector |
| `all` | Runtime umbrella profile | `db` + `blob` + `http` + `observability` |
| `dev` | Local QA/tooling | `pytest`, `ruff`, `mypy`, `pip-audit`, `pyright`, `pylint`, `testcontainers` |

## Quick start

### 1) Load config and bootstrap resources

```python
from orchid_commons import (
    ResourceManager,
    bootstrap_logging_from_app_settings,
    load_config,
)

settings = load_config(config_dir="config", env="development")
bootstrap_logging_from_app_settings(settings, env="development")

manager = ResourceManager()
await manager.startup(settings.resources, required=["sqlite"])

sqlite = manager.get("sqlite")
row = await sqlite.fetchone("SELECT 1 AS ok")

await manager.close_all()
```

### 2) Minimal `appsettings.json`

```json
{
  "service": {
    "name": "orchid-service",
    "version": "1.0.0"
  },
  "resources": {
    "sqlite": {
      "db_path": "data/app.db"
    }
  }
}
```

### 3) Environment overrides with placeholders

```json
{
  "observability": {
    "otlp_endpoint": "${OTEL_EXPORTER_OTLP_ENDPOINT}"
  },
  "resources": {
    "postgres": {
      "dsn": "${DATABASE_URL}"
    }
  }
}
```

`load_config()` merge order:
1. `appsettings.json`
2. `appsettings.<env>.json`
3. placeholder resolution from environment variables

## ResourceManager

`ResourceManager` is the runtime entry point for lifecycle + health:
- `startup(settings, required=[...])`
- `get(name)` / `has(name)`
- `health_report()` and `health_payload()`
- `close_all()` with aggregated shutdown errors

Built-in resource names:
- `sqlite`
- `postgres`
- `redis`
- `mongodb`
- `rabbitmq`
- `qdrant`
- `minio`
- `r2`
- `multi_bucket`

## Storage and data connectors

### SQL, cache, document, queue, vector

Main providers:
- `SqliteResource`
- `PostgresProvider`
- `RedisCache`
- `MongoDbResource`
- `RabbitMqBroker`
- `QdrantVectorStore`

Vector contract (`VectorStore`) includes:
- `upsert(...)`
- `search(...)`
- `delete(...)`
- `count(...)`
- `health_check()`

### Blob (MinIO/S3-compatible + R2)

```python
from orchid_commons import MinioSettings, create_minio_profile

profile = await create_minio_profile(
    MinioSettings.local_dev(
        access_key="minioadmin",
        secret_key="minioadmin",
        bucket="orchid-dev",
    )
)

await profile.upload("hello.txt", b"hello")
obj = await profile.download("hello.txt")
```

Cloudflare R2 uses `R2Settings` + `create_r2_profile()` and the same S3-compatible flow.

### Multi-bucket router

```python
from orchid_commons.blob import create_multi_bucket_router
from orchid_commons.config import MultiBucketSettings

router = await create_multi_bucket_router(
    MultiBucketSettings(
        endpoint="localhost:9000",
        access_key="minioadmin",
        secret_key="minioadmin",
        buckets={
            "videos": "orchid-videos",
            "chunks": "orchid-chunks"
        },
        create_buckets_if_missing=True,
        secure=False,
    )
)

await router.upload("videos", "clip.mp4", b"...")
```

## Observability

### Structured logging

```python
import logging

from orchid_commons import bootstrap_logging_from_app_settings, load_config

settings = load_config(config_dir="config", env="development")
bootstrap_logging_from_app_settings(settings, env="development")

logger = logging.getLogger(__name__)
logger.info("service_started")
```

Compatibility adapter for event-style logging:

```python
from orchid_commons import get_structlog_compat_logger

logger = get_structlog_compat_logger(__name__).bind(component="bot_manager")
logger.info("bot_started", bot_name="orchid-main")
```

### Prometheus

```python
from orchid_commons import configure_prometheus_metrics, start_prometheus_http_server

configure_prometheus_metrics()
start_prometheus_http_server(port=9464)
```

Endpoint: `http://localhost:9464/metrics`

ASGI bridge:

```python
from orchid_commons import create_prometheus_asgi_app

metrics_app = create_prometheus_asgi_app()
# app.mount("/metrics", metrics_app)
```

### OpenTelemetry + Langfuse

```python
from orchid_commons import bootstrap_observability, load_config

settings = load_config(config_dir="config", env="production")
bootstrap_observability(settings)
```

Manual spans:

```python
from orchid_commons import request_span

with request_span("http.request", method="GET", route="/health"):
    pass
```

### HTTP correlation middleware

FastAPI:

```python
from fastapi import FastAPI
from orchid_commons import create_fastapi_observability_middleware

app = FastAPI()
app.middleware("http")(create_fastapi_observability_middleware())
```

aiohttp:

```python
from aiohttp import web
from orchid_commons import create_aiohttp_observability_middleware

app = web.Application(
    middlewares=[create_aiohttp_observability_middleware()],
)
```

## Health endpoint payload

```python
report = await manager.health_report()
payload = report.to_dict()
# or directly:
# payload = await manager.health_payload()
```

Includes:
- aggregate status (`ok`, `degraded`, `down`),
- readiness/liveness booleans,
- per-resource latency/status,
- optional `otel` / `langfuse` checks when enabled.

## Local examples

Infrastructure stack (MinIO, Postgres, Redis, MongoDB, RabbitMQ, Qdrant):

```bash
docker compose -f examples/infrastructure/docker-compose.yml up -d
```

Observability stack (Prometheus, Grafana, OTel Collector, Jaeger, demo app):

```bash
cd examples/observability
docker compose up --build
```

See:
- `examples/infrastructure/README.md`
- `examples/observability/README.md`

## Development

Install all runtime integrations + developer tooling:

```bash
uv sync --extra all --extra dev
```

Quality commands:

```bash
uv run pytest
uv run pytest -m integration
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pip-audit
uv build
```

Install pre-commit hooks (recommended):

```bash
uv run pre-commit install
uv run pre-commit run --all-files
```

CI-parity test command:

```bash
uv run pytest --cov=src --cov-report=term-missing --cov-report=xml
```

## Additional docs

- `docs/prometheus-metrics.md`
- `docs/logging-fields.md`
- `docs/logging-compat-migration.md`
- `docs/integration-tests.md`
- `docs/commons-first-python-quality-standard.md`
- `docs/integration-playbook-romy-youtube.md`
- `docs/k8s-sealed-secrets.md`
- `examples/quality/python-service-template/README.md`

## License

MIT
