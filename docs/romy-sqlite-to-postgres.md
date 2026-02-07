# Romy Migration Map: SQLite -> PostgreSQL

## Objetivo

Migrar `romy-skills` desde SQLite (`aiosqlite`) a PostgreSQL (`asyncpg` via `orchid_commons`) con impacto controlado en:

- capa de repositorios,
- inicializacion de runtime,
- estrategia de rollout por entorno.

## Estado actual (as-is)

Arquitectura observada en `romy-skills`:

- Conexion central en `src/infrastructure/db/connection.py` con `aiosqlite`.
- Repositorios en `src/infrastructure/db/repositories/` acoplados a:
  - placeholders `?`,
  - `aiosqlite.Row`,
  - commits explicitos por operacion.
- Configuracion via `ROMY_BOT_*` en `src/infrastructure/config/settings.py`.

## Estado objetivo (to-be)

- `ResourceManager` como orquestador de recursos.
- `ResourceSettings.from_app_settings(...)` + `load_config(...)`.
- Provider `PostgresProvider` como backend SQL principal.
- Configuracion tipada por `appsettings.json` + `appsettings.<env>.json`.

## Mapa de migracion por componente

| Componente | As-is | To-be | Accion |
| --- | --- | --- | --- |
| Configuracion | `ROMY_BOT_DB_PATH` y settings pydantic locales | `config/appsettings*.json` + placeholders `${DATABASE_URL}` | Agregar config files y cargar con `load_config`. |
| Conexion DB | Singleton `aiosqlite` global | `ResourceManager` + `PostgresProvider` | Reemplazar acceso directo por recurso inyectado. |
| SQL placeholders | `?` | `$1`, `$2`, ... | Refactor de queries en repositorios. |
| Tipo de fila | `aiosqlite.Row` | `dict[str, Any]` | Adaptar mapeo `_row_to_model`. |
| Migraciones | `executescript` sobre SQLite | `run_migrations(...)` sobre PostgreSQL | Reusar SQL compatible con PG o separar dialectos. |
| Health | query local SQLite | `postgres.health_check()` | Integrar en startup checks. |

## Paso a paso recomendado

### 1. Agregar config de recursos en appsettings

```json
{
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

```bash
export DATABASE_URL="postgresql://romy:romy@localhost:5432/romy"
```

### 2. Inicializar recursos con commons

```python
from orchid_commons import ResourceManager, ResourceSettings, load_config

app_settings = load_config(config_dir="config", env="production")
resource_settings = ResourceSettings.from_app_settings(app_settings)

manager = ResourceManager()
await manager.startup(resource_settings, required=["postgres"])
postgres = manager.get("postgres")
```

### 3. Migrar queries de repositorios

Antes (SQLite):

```python
await sqlite.execute(
    "INSERT INTO publications(id, title) VALUES (?, ?)",
    (pub_id, title),
    commit=True,
)
```

Despues (PostgreSQL):

```python
await postgres.execute(
    "INSERT INTO publications(id, title) VALUES($1, $2)",
    (pub_id, title),
)
```

### 4. Ejecutar migraciones SQL en PostgreSQL

```python
executed = await postgres.run_migrations("src/infrastructure/db/migrations")
assert executed, "No migration files executed"
```

### 5. Validacion funcional y cierre

```python
health = await postgres.health_check()
assert health.healthy
await manager.close_all()
```

## Estrategia de cutover (sin downtime duro)

### Fase 0: baseline

- Congelar release y tomar backup de SQLite.
- Medir conteos por tabla para comparacion posterior.

### Fase 1: dual-readiness

- Desplegar soporte PostgreSQL en paralelo (sin apagar SQLite).
- Ejecutar migraciones PG y checks de conectividad.

### Fase 2: data copy y verificacion

- Copiar datos historicos SQLite -> PG.
- Verificar paridad de conteos y consultas clave por dominio.

### Fase 3: switch

- Cambiar config productiva a `resources.postgres`.
- Monitorear latencia y errores durante ventana de observacion.

### Fase 4: deprecacion SQLite

- Eliminar dependencias residuales a `aiosqlite`.
- Mantener backup de rollback por ventana acordada.

## Breaking changes y mitigaciones

| Riesgo | Causa | Mitigacion |
| --- | --- | --- |
| SQL invalido en runtime | Placeholders `?` no validos en asyncpg | Script de chequeo + tests sobre repositorios migrados. |
| Errores de mapeo de filas | Cambio de `Row` a `dict` | Aislar mapping en repositorio y cubrir con tests unitarios. |
| Semantica de commit inconsistente | `commit=True` en SQLite no aplica igual en PG | Usar `transaction()` para operaciones multi-query. |
| Falta de indices/performance | Migraciones no equivalentes entre SQLite y PG | Revisar DDL/indices antes del cutover. |

## Checklist final de aceptacion

- [ ] Startup con `required=["postgres"]` en prod.
- [ ] Repositorios sin dependencias directas a `aiosqlite`.
- [ ] Migraciones ejecutadas y verificadas en PG.
- [ ] Health checks y cierre (`close_all`) exitosos.
- [ ] Runbook de rollback documentado y probado.
