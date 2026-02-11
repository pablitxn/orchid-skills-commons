# Infrastructure Stack

Local development infrastructure for testing orchid-mcp-commons.

## Services

| Service | Port(s) | UI/Console |
|---------|---------|------------|
| MinIO | 9000 (API), 9001 (Console) | http://localhost:9001 |
| MongoDB | 27017 | - |
| Qdrant | 6333 (REST), 6334 (gRPC) | http://localhost:6333/dashboard |
| Redis | 6379 | - |
| PostgreSQL | 5432 | - |
| RabbitMQ | 5672 (AMQP), 15672 (UI) | http://localhost:15672 |

## Usage

```bash
# Start all services
docker compose up -d

# Check status
docker compose ps

# View logs
docker compose logs -f

# Stop all services
docker compose down

# Stop and remove volumes (clean slate)
docker compose down -v
```

## Default Credentials

- **MinIO**: minioadmin / minioadmin
- **PostgreSQL**: postgres / postgres (database: orchid)
- **RabbitMQ**: guest / guest

## Pre-created Buckets

MinIO automatically creates these buckets on startup:
- `orchid-default`
- `orchid-videos`
- `orchid-documents`
- `orchid-vectors`

## Health Checks

All services have health checks configured. Use `docker compose ps` to verify all services are healthy before running tests.
