# Plan Maestro: Integracion de `orchid_commons` en youtube-mcp, matrix-orchid-bot y romy-skills

## Objetivo

Implementar `orchid_commons` en los tres repos enlazados para:

1. Estandarizar configuracion (`appsettings` + placeholders).
2. Estandarizar ciclo de vida de recursos (`ResourceManager`).
3. Estandarizar logging/observabilidad/health checks.
4. Identificar y normalizar recursos de plataforma por servicio.
5. Ejecutar migracion con trabajo paralelizable y trazable por PR.
6. Estandarizar calidad de codigo (`uv` + `ruff` + `pytest`) como baseline comun.

## Repos en alcance

- `youtube-mcp`
- `orchid-matrix-bot` (repo real: matrix-orchid-bot)
- `romy-skills`
- `orchid_skills_commons_py` (este repo), solo para gaps de compatibilidad necesarios.

## Diagnostico (estado actual, revisado en codigo)

### youtube-mcp (as-is)

- Tiene settings propios en `youtube-mcp/src/commons/settings/models.py` + loader propio `youtube-mcp/src/commons/settings/loader.py`.
- Tiene telemetry propia en `youtube-mcp/src/commons/telemetry/*.py` (4 archivos).
- Usa providers locales para blob y vector:
  - `youtube-mcp/src/commons/infrastructure/blob/minio_provider.py`
  - `youtube-mcp/src/commons/infrastructure/vectordb/qdrant_provider.py`
- Wiring central en `youtube-mcp/src/infrastructure/factory.py`.
- Startup/shutdown en `youtube-mcp/src/api/dependencies.py` inicializa Langfuse local.
- Contrato blob actual es multi-bucket por operacion (videos/chunks/frames), visible en `youtube-mcp/src/application/services/storage.py`.
- Health checks actuales son de disponibilidad basica en `youtube-mcp/src/api/openapi/routes/health.py`.

Impacto cuantitativo:
- Referencias a telemetry/infra local en `src`: 37 ocurrencias.
- Referencias directas a modulo telemetry local: 23 ocurrencias.

### romy-skills (as-is)

- Settings via env `ROMY_BOT_*` en `romy-skills/src/infrastructure/config/settings.py`.
- Logging propio en `romy-skills/src/infrastructure/config/logging.py`.
- DB acoplada a SQLite:
  - Conexion singleton `aiosqlite` en `romy-skills/src/infrastructure/db/connection.py`
  - 15 repositorios en `romy-skills/src/infrastructure/db/repositories`
  - 14 repositorios importan `aiosqlite`.
- SQL con placeholders `?`: 72 ocurrencias en repositorios.
- Migrations actuales SQL-lite centricas en `romy-skills/src/infrastructure/db/migrations/*.sql`.
- API readiness check toca SQLite directo en `romy-skills/src/adapters/api/routes/health.py`.

### orchid-matrix-bot (as-is)

- Settings por env en `orchid-matrix-bot/src/config/settings.py`.
- Config de bots en YAML + expansion de env en `orchid-matrix-bot/src/config/loader.py`.
- Logging con `structlog` en `orchid-matrix-bot/src/logging/setup.py`.
- Orquestacion runtime en `orchid-matrix-bot/src/services/bot_manager.py`.
- Recursos de plataforma reales:
  - Auth/token service account (`orchid-matrix-bot/src/services/auth_manager.py`)
  - Cliente HTTP a orchid-core (`orchid-matrix-bot/src/services/orchid_client.py`)
  - Conexiones Matrix por bot (`orchid-matrix-bot/src/services/matrix_bot.py`)
  - Endpoint de notificaciones webhook (`orchid-matrix-bot/src/services/notification_server.py`)

Impacto cuantitativo:
- 20 archivos Python.
- 39 ocurrencias ligadas a `structlog/setup_logging/get_logger`.

## Recursos de plataforma a normalizar

### youtube-mcp

- `minio` blob (obligatorio)
- `qdrant` vector (temporal, adapter local)
- `mongodb` document DB (temporal, adapter local)
- `langfuse` (si enabled)
- `otel` (si enabled)

### romy-skills

- `postgres` SQL (objetivo primario)
- `langfuse` (si enabled)
- `otel` (si enabled)
- `sqlite` solo como fallback temporal durante cutover

### orchid-matrix-bot

- `orchid_auth` (token service account)
- `orchid_api` (cliente HTTP a orchid-core)
- `matrix_clients` (conexion de bots Matrix)
- `notification_webhook` (si enabled)
- `langfuse`/`otel` opcionales segun entorno

## Arquitectura objetivo comun

1. `load_config(config_dir="config", env=<env>)` como loader base.
2. `bootstrap_logging_from_app_settings(...)` para formato estructurado consistente.
3. `bootstrap_observability(...)` + `create_langfuse_client(...)` en startup.
4. `ResourceSettings.from_app_settings(...)` + `ResourceManager.startup(...)`.
5. Health centralizado via `manager.health_payload(...)` y checks opcionales.
6. Shutdown ordenado via `manager.close_all()` + `shutdown_observability()`.

Referencia del baseline comun de calidad y runtime:
- `docs/commons-first-python-quality-standard.md`

## Gaps detectados y decisiones tecnicas previas

1. `youtube-mcp` usa multi-bucket por operacion; `S3BlobStorage/MinioProfile` en commons usa bucket fijo por instancia.
Decidir entre:
- Opcion A (recomendada): crear adapter multi-bucket en `youtube-mcp` que componga 3 instancias commons.
- Opcion B: extender commons con `MultiBucketS3Router` reutilizable.

2. `youtube-mcp` y `orchid-matrix-bot` usan logging API no compatible 1:1 con stdlib.
Decidir entre:
- Opcion A (recomendada): capa de compatibilidad temporal por repo y migracion gradual de llamadas.
- Opcion B: refactor masivo de llamadas de logging en un solo PR (alto riesgo).

3. `romy-skills` requiere migracion SQL (SQLite -> PostgreSQL) y eso es cambio de comportamiento, no solo wiring.
Decidir:
- Cutover por fases con fallback temporal a SQLite.

## Plan de trabajo paralelizable (streams)

### Stream S0 - Baseline y gobierno tecnico (bloqueante corto)

- [ ] Definir version objetivo de `orchid_commons` para los 3 repos.
- [ ] Definir plantilla unica `appsettings.json` + `appsettings.{env}.json`.
- [ ] Definir convencion de env (`ORCHID_ENV`) y placeholders.
- [ ] Adoptar oficialmente `docs/commons-first-python-quality-standard.md`.
- [ ] Definir checklist de PR y gate de QA comun (`uv sync`, `ruff check`, `ruff format --check`, `pytest`).

Dependencias: ninguna.  
Salida: ADR corta + templates de config.

### Stream S1 - youtube-mcp (puede correr en paralelo con S2 y S3)

- [ ] Y1: agregar dependencia `orchid_commons` y wiring base.
- [ ] Y2: introducir `load_config` + mapping desde settings de negocio actuales.
- [ ] Y3: migrar logging a commons (con capa de compatibilidad temporal si hace falta).
- [ ] Y4: migrar Langfuse/OTEL a commons (`bootstrap_observability`, `create_langfuse_client`).
- [ ] Y5: reemplazar MinIO local por commons (adapter multi-bucket requerido).
- [ ] Y6: integrar `ResourceManager` en startup/shutdown y exponer health agregado real.
- [ ] Y7: mantener Qdrant y MongoDB con adapters temporales + health checks.
- [ ] Y8: limpiar codigo legacy de telemetry/blob local.

Dependencias: S0 + decision multi-bucket.  
Bloqueos esperados: contrato blob multi-bucket.

### Stream S2 - romy-skills (paralelo)

- [ ] R1: agregar `config/appsettings*.json` para servicio/logging/observability/resources.
- [ ] R2: migrar bootstrap de logging a commons en API/MCP/main.
- [ ] R3: crear runtime bootstrap con `ResourceManager` y recurso `postgres`.
- [ ] R4: migrar capa de conexion y repositorios de `aiosqlite` a `PostgresProvider`.
- [ ] R5: adaptar SQL placeholders (`?` -> `$1..$n`) y mapeo de filas (`Row` -> `dict`).
- [ ] R6: adaptar migrations a PostgreSQL (dialecto/indices/JSON).
- [ ] R7: readiness/health via `manager.health_payload`.
- [ ] R8: eliminar dependencias residuales de SQLite cuando staging valide.

Dependencias: S0.  
Bloqueos esperados: conversion SQL y paridad de datos.

### Stream S3 - orchid-matrix-bot (paralelo)

- [ ] M1: agregar `config/appsettings*.json` para service/logging/observability/resources.
- [ ] M2: mantener YAML de bots, pero mover settings runtime base a `load_config`.
- [ ] M3: integrar logging commons con estrategia de compatibilidad para llamadas structlog-style.
- [ ] M4: crear wrappers de recursos con `health_check/close` para:
  - `orchid_auth`
  - `orchid_api`
  - `matrix_clients`
  - `notification_webhook`
- [ ] M5: usar `ResourceManager` dentro de `BotManager`.
- [ ] M6: reemplazar `/health/live` y `/health/ready` por payload agregado del manager.
- [ ] M7: habilitar observabilidad opcional (OTEL/Langfuse) sin romper stdio/daemon mode.

Dependencias: S0 + decision logging compat.

### Stream S4 - Hardening y rollout cross-repo

- [ ] H1: smoke tests de startup/shutdown en los 3 repos.
- [ ] H2: contract tests de health payload uniforme.
- [ ] H3: validacion de campos de logging obligatorios.
- [ ] H4: validacion de trazas/metrics en entorno dev.
- [ ] H5: runbook de rollback por repo.
- [ ] H6: despliegue progresivo dev -> staging -> prod.

Dependencias: S1 + S2 + S3.

## Orden recomendado de PRs (para merge continuo)

1. PR-00 (S0): plantillas appsettings + convenciones.
2. PR-01 (youtube): wiring de config + manager (sin migrar blob aun).
3. PR-02 (romy): bootstrap commons + postgres runtime skeleton.
4. PR-03 (matrix): config commons + manager skeleton.
5. PR-04 (youtube): adapter multi-bucket + migracion blob.
6. PR-05 (youtube): migracion observabilidad y limpieza telemetry local.
7. PR-06 (romy): migracion repositorios lote A.
8. PR-07 (romy): migracion repositorios lote B + readiness.
9. PR-08 (matrix): logging compat + health agregada.
10. PR-09 (cross): hardening, docs, rollback y checklists finales.

## Criterios de aceptacion por repo

### youtube-mcp

- [ ] Startup usa `ResourceManager`.
- [ ] Blob opera sobre commons sin regresion funcional en ingest/query/delete.
- [ ] Health endpoint usa checks reales (no solo instanciacion).
- [ ] Logging + Langfuse + OTEL salen via commons.
- [ ] Modulos locales legacy de telemetry/blob eliminados o deprecados.

### romy-skills

- [ ] No hay imports `aiosqlite` en runtime principal.
- [ ] Repositorios corren sobre PostgresProvider.
- [ ] Migrations ejecutan correctamente en PostgreSQL.
- [ ] Health/readiness dependen de `manager.health_payload`.
- [ ] Logging/observabilidad unificados con commons.

### orchid-matrix-bot

- [ ] Config base cargada por `load_config`.
- [ ] `BotManager` controla lifecycle de recursos via manager.
- [ ] Health readiness refleja estado real de Orchid API + bots Matrix.
- [ ] Logging estructurado unificado sin romper comportamiento actual.
- [ ] Observabilidad opcional disponible por entorno.
- [ ] Pipeline/base local usa `uv` + `ruff` + `pytest` como gate minimo.

## Riesgos principales y mitigaciones

1. Riesgo: ruptura por contrato blob multi-bucket en youtube.
Mitigacion: adapter dedicado + tests de compatibilidad por metodo.

2. Riesgo: migracion SQL en romy produce drift funcional.
Mitigacion: migracion por lotes + parity tests por repositorio + rollback plan.

3. Riesgo: logging en matrix depende fuerte de `structlog`.
Mitigacion: capa compat temporal y migracion incremental de llamadas.

4. Riesgo: doble inicializacion de observabilidad.
Mitigacion: centralizar bootstrap en lifespan/startup unico por proceso.

## Checklist de tracking (tablero)

- [ ] S0 cerrado
- [ ] S1 cerrado
- [ ] S2 cerrado
- [ ] S3 cerrado
- [ ] S4 cerrado

### Estado rapido

| Stream | Estado | Owner | Bloqueado por |
| --- | --- | --- | --- |
| S0 | TODO | TBD | - |
| S1 (youtube) | TODO | TBD | Decision multi-bucket |
| S2 (romy) | TODO | TBD | - |
| S3 (matrix) | TODO | TBD | Decision logging compat |
| S4 (hardening) | TODO | TBD | S1 + S2 + S3 |

## Comandos de verificacion sugeridos

### commons

```bash
uv sync --extra all --extra dev
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

### youtube-mcp

```bash
cd youtube-mcp
uv sync --extra dev
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

### orchid-matrix-bot

```bash
cd orchid-matrix-bot
uv sync --extra dev
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

### romy-skills

```bash
cd romy-skills
uv sync --extra dev
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

## Definicion de Done global

- [ ] Los 3 repos cargan config desde `appsettings` (con placeholders resueltos).
- [ ] Los 3 repos tienen lifecycle de recursos con `ResourceManager`.
- [ ] Logs incluyen `service`, `env`, `trace_id`, `span_id`, `request_id`.
- [ ] Health endpoint de cada repo reporta checks de recursos reales.
- [ ] Runbooks de rollback validados en staging.
- [ ] Codigo legacy duplicado (telemetry/blob/db wrappers) removido o marcado deprecado con fecha.
- [ ] Gate minimo de calidad activo en todos los repos: `uv` + `ruff` + `pytest`.
