# Playbook de Integracion y Migracion (Romy + youtube-mcp)

## Objetivo

Estandarizar la adopcion de `orchid_commons` en:

- `romy-skills` (migracion de SQLite a PostgreSQL).
- `youtube-mcp` (extraccion de blob, vector y observabilidad hacia commons).

Este playbook esta pensado para que un integrador pueda completar el onboarding en menos de 30 minutos y ejecutar un rollout controlado por entorno.

## Prerrequisitos

- Python `>=3.11`.
- `uv` instalado.
- Docker (para PostgreSQL/MinIO local).
- Acceso a secretos por entorno (idealmente via Sealed Secrets).

## Onboarding del integrador (<30 min)

| Minuto | Resultado esperado |
| --- | --- |
| 0-5 | Clonar repos, instalar deps y validar `uv run pytest` en `orchid_skills_commons_py`. |
| 5-10 | Entender contratos de `ResourceManager`, `load_config` y perfiles blob/obs en `README.md`. |
| 10-20 | Levantar un servicio piloto con `appsettings` + `ResourceSettings.from_app_settings(...)`. |
| 20-25 | Ejecutar smoke checks de recursos (`health_check`) y cierre (`close_all`). |
| 25-30 | Completar checklist de rollout del entorno objetivo (dev/staging/prod). |

## Guia 1: Integracion paso a paso para `romy-skills`

### 1. Introducir config tipada con `appsettings`

Crear `config/appsettings.json` y variantes por entorno:

```json
{
  "service": {
    "name": "romy-skills",
    "version": "0.1.0",
    "host": "0.0.0.0",
    "port": 8000
  },
  "logging": {
    "level": "INFO",
    "format": "json"
  },
  "observability": {
    "enabled": true,
    "otlp_endpoint": "${OTEL_EXPORTER_OTLP_ENDPOINT}",
    "sample_rate": 1.0
  },
  "resources": {
    "postgres": {
      "dsn": "${DATABASE_URL}",
      "min_pool_size": 2,
      "max_pool_size": 20,
      "command_timeout_seconds": 30
    }
  }
}
```

### 2. Bootstrapping de runtime con commons

En el startup del servicio:

```python
from orchid_commons import (
    ResourceManager,
    ResourceSettings,
    bootstrap_logging_from_app_settings,
    bootstrap_observability,
    load_config,
)

app_settings = load_config(config_dir="config", env="production")
bootstrap_logging_from_app_settings(app_settings, env="production")
bootstrap_observability(app_settings)

resource_settings = ResourceSettings.from_app_settings(app_settings)
manager = ResourceManager()
await manager.startup(resource_settings, required=["postgres"])
postgres = manager.get("postgres")
```

### 3. Migrar capa de acceso a datos

- Reemplazar dependencias directas a `aiosqlite` por `PostgresProvider`.
- Cambiar placeholders SQL de `?` a `$1`, `$2`, ... .
- Usar `fetchone/fetchall` (devuelven `dict`) en vez de `aiosqlite.Row`.

### 4. Ejecutar migraciones y smoke tests

- `await postgres.run_migrations("src/infrastructure/db/migrations")`
- `health = await postgres.health_check()`
- `await manager.close_all()`

### 5. Validar salida funcional

- API/MCP arranca sin SQLite.
- Repositorios de dominio leen/escriben en PostgreSQL.
- Logs estructurados y trazas visibles en el backend de observabilidad.

Detalles completos del mapa de migracion:

- `docs/romy-sqlite-to-postgres.md`

## Guia 2: Integracion paso a paso para `youtube-mcp`

### 1. Config unificada con `appsettings`

Unificar carga de configuracion para runtime compartido (`service`, `logging`, `observability`, `resources`) y conservar secciones propias de negocio (`chunking`, `llm`, `youtube`, etc.).

### 2. Extraer blob MinIO al perfil comun

- Reemplazar `src/commons/infrastructure/blob/minio_provider.py`.
- Usar `MinioProfile` (`orchid_commons.blob.minio`) como implementacion base.
- Mantener un adapter fino para compatibilidad de firma si hace falta.

### 3. Extraer observabilidad al bootstrap comun

- Reemplazar `configure_logging` local por `bootstrap_logging_from_app_settings`.
- Reemplazar bootstrap OTEL/Langfuse local por:
  - `bootstrap_observability(...)`
  - `create_langfuse_client(...)`

### 4. Vector DB por fases

- Fase corta: mantener Qdrant con adapter interno.
- Fase media: exponer un contrato vectorial comun.
- Fase larga: mover embeddings + retrieval a PostgreSQL/pgvector cuando exista provider comun.

Mapa detallado:

- `docs/youtube-mcp-extraction-map.md`

## Checklist de rollout por entorno

### Dev

- [ ] `appsettings.development.json` apunta a recursos no productivos.
- [ ] `create_bucket_if_missing` habilitado solo para entornos locales.
- [ ] Smoke tests de startup/shutdown y health checks verdes.

### Staging

- [ ] Sealed Secrets aplicados y desencriptados en namespace correcto.
- [ ] Migraciones SQL ejecutadas antes del deploy de app.
- [ ] Dashboards y alertas de errores/latencia validados.
- [ ] Prueba de rollback (revert de deployment + verificacion de conectividad) realizada.

### Prod

- [ ] `required=[...]` en `ResourceManager.startup(...)` cubre recursos criticos.
- [ ] Feature flags / toggles de cutover validados.
- [ ] Rotacion de secretos documentada y probada.
- [ ] Runbook de incidente y responsables de guardia publicados.

## Breaking changes y mitigaciones

| Cambio | Impacto | Mitigacion |
| --- | --- | --- |
| Placeholder SQL `?` -> `$n` en PostgreSQL | Queries de Romy fallan al migrar si no se actualizan. | Migrar repositorios por lotes y cubrir con tests de repositorio. |
| Resultado de consultas (`aiosqlite.Row` -> `dict`) | Codigo que indexa por posicion puede romperse. | Normalizar acceso por nombre de campo en repositorios. |
| Contrato blob de `youtube-mcp` usa `bucket` por llamada | `MinioProfile` fija bucket en settings. | Adapter de compatibilidad temporal para no romper servicios. |
| Inicializacion ad-hoc de telemetry en `youtube-mcp` | Riesgo de doble inicializacion o datos inconsistentes. | Centralizar bootstrap en startup/lifespan con commons. |
| Secrets via env sueltos | Riesgo operacional en k8s. | Migrar a Sealed Secrets por entorno y namespace. |

## Recomendaciones de Sealed Secrets

Guia operativa:

- `docs/k8s-sealed-secrets.md`
