# Integration Test Matrix

This project includes end-to-end integration tests for core shared resources.

## Run

```bash
uv sync --extra all --extra dev
uv run pytest -m integration
```

## Matrix

| Area | Test file | External dependency |
| --- | --- | --- |
| SQLite | `tests/integration/connectors/test_sqlite_integration.py` | None |
| PostgreSQL | `tests/integration/connectors/test_postgres_integration.py` | Docker (or external DSN) |
| MinIO | `tests/integration/connectors/test_minio_integration.py` | Docker (or external endpoint) |
| Qdrant | `tests/integration/connectors/test_qdrant_integration.py` | Docker (or external endpoint) |
| Observability smoke | `tests/integration/observability/test_observability_integration.py` | `observability` extra |

## Service Selection

PostgreSQL fixture behavior:
- If `ORCHID_POSTGRES_DSN` is set, tests use that DSN.
- Otherwise, tests start `postgres:16-alpine` via testcontainers.

MinIO fixture behavior:
- If `ORCHID_MINIO_ENDPOINT` is set, tests use provided endpoint and credentials.
- Otherwise, tests start `minio/minio:latest` via testcontainers.

Qdrant fixture behavior:
- If `ORCHID_QDRANT_URL` or `ORCHID_QDRANT_HOST` is set, tests use provided endpoint.
- Otherwise, tests start `qdrant/qdrant:v1.9.0` via testcontainers.

Optional MinIO env vars:
- `ORCHID_MINIO_ACCESS_KEY` (default: `minioadmin`)
- `ORCHID_MINIO_SECRET_KEY` (default: `minioadmin`)
- `ORCHID_MINIO_BUCKET` (default: generated `orchid-integration-*`)
- `ORCHID_MINIO_SECURE` (default: `false`)
- `ORCHID_MINIO_REGION` (optional)
- `ORCHID_MINIO_IMAGE` (default: `minio/minio:latest`)

Optional PostgreSQL env vars:
- `ORCHID_POSTGRES_DSN` (external DSN override)
- `ORCHID_POSTGRES_IMAGE` (default: `postgres:16-alpine`)

Optional Qdrant env vars:
- `ORCHID_QDRANT_URL` (external URL override)
- `ORCHID_QDRANT_HOST` (external host override)
- `ORCHID_QDRANT_PORT` (default: `6333`)
- `ORCHID_QDRANT_GRPC_PORT` (default: `6334`)
- `ORCHID_QDRANT_USE_SSL` (default: `false`)
- `ORCHID_QDRANT_API_KEY` (optional)
- `ORCHID_QDRANT_TIMEOUT_SECONDS` (default: `10.0`)
- `ORCHID_QDRANT_PREFER_GRPC` (default: `false`)
- `ORCHID_QDRANT_COLLECTION_PREFIX` (default: generated per test session)
- `ORCHID_QDRANT_IMAGE` (default: `qdrant/qdrant:v1.9.0`)

## Error/Transient Coverage

Integration tests intentionally include:
- SQLite lock conflict (`database is locked`) case.
- PostgreSQL command timeout (`pg_sleep`) to validate transient retry/timeout behavior.
- MinIO missing object typed error (`BlobNotFoundError`).
