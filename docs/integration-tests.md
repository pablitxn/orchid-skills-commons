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
| SQLite | `tests/integration/test_sqlite_integration.py` | None |
| PostgreSQL | `tests/integration/test_postgres_integration.py` | Docker (or external DSN) |
| MinIO | `tests/integration/test_minio_integration.py` | Docker (or external endpoint) |
| Observability smoke | `tests/integration/test_observability_integration.py` | `observability` extra |

## Service Selection

PostgreSQL fixture behavior:
- If `ORCHID_POSTGRES_DSN` is set, tests use that DSN.
- Otherwise, tests start `postgres:16-alpine` via testcontainers.

MinIO fixture behavior:
- If `ORCHID_MINIO_ENDPOINT` is set, tests use provided endpoint and credentials.
- Otherwise, tests start `minio/minio:latest` via testcontainers.

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

## Error/Transient Coverage

Integration tests intentionally include:
- SQLite lock conflict (`database is locked`) case.
- PostgreSQL command timeout (`pg_sleep`) to validate transient retry/timeout behavior.
- MinIO missing object typed error (`BlobNotFoundError`).

