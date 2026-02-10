# Auditoría de Código — orchid-skills-commons

**Fecha**: 2026-02-10
**Paquete**: `orchid-skills-commons` v0.1.0
**Alcance**: Revisión completa del código fuente (`src/orchid_commons/`), tests, configuración y CI

---

## Metodología

Se aplicaron tres capas de auditoría:

1. Lectura estática de código con referencias a líneas concretas.
2. Ejecución de tests unitarios.
3. Ejecución de validaciones de calidad estática (`ruff`, `mypy`).

Comandos principales ejecutados:

- `pytest -q tests/unit`
- `ruff check src tests`
- `mypy src`

---

## Estado actual de calidad

### Tests unitarios

- 167 tests pasaron
- 10 tests fallaron

Falla principal: rutas de fixtures incorrectas (ver hallazgo #2).

### Lint (`ruff`)

- 9 errores reportados, 8 corregibles automáticamente
- Imports no ordenados, imports no usados, variable desempaquetada no usada

### Tipado (`mypy`)

- 46 errores
- Imports opcionales sin stubs (`redis`, `aio_pika`, `motor`, `qdrant_client`, `aiohttp`)
- Inconsistencias en contratos de tipo internos
- Discrepancias con firmas de cliente MinIO y protocolos declarados

---

## Hallazgos

### 1. Fuga de recursos si `startup` falla en bootstrap parcial

**Severidad: Alta** | **Esfuerzo: Pequeño**

`ResourceManager.startup` no hace rollback si falla un factory intermedio:

- `src/orchid_commons/runtime/manager.py:118`
- `src/orchid_commons/runtime/manager.py:126`

`bootstrap_resources` registra recursos en caliente (`manager.py:382–385`). Si el tercer factory falla, los dos primeros quedan abiertos en `_resources` sin cierre.

Se reprodujo localmente:

```text
RuntimeError boom
resources_after_failure ['a']
```

Además, `close_all()` tiene un problema relacionado: `self._resources.clear()` (`manager.py:167`) se ejecuta **antes** de verificar errores, perdiendo las referencias a todos los resources (incluyendo los que fallaron), haciendo imposible reintentar el cierre.

**Recomendación**:
- En el `except` de `startup`, ejecutar `await close_all()` en modo best-effort antes de relanzar.
- En `close_all()`, solo limpiar los resources que cerraron exitosamente.
- Agregar test de regresión para bootstrap parcial.

---

### 2. Suite unitaria inestable por fixtures mal referenciados

**Severidad: Alta** | **Esfuerzo: Pequeño**

Path esperado por tests:
- `tests/unit/test_config_loader.py:18`
- `tests/unit/test_settings.py:20`

Path real en repo: `tests/fixtures/config`

**Recomendación**: Corregir rutas y extraer helper común de fixtures para evitar duplicación de paths frágiles.

---

### 3. Modelos de Settings duplicados

**Severidad: Alta** | **Esfuerzo: Grande**

Existen **dos jerarquías paralelas** de settings:

- `config/models.py` — Modelos Pydantic (`PostgresSettings`, `RedisSettings`, etc.) con validación, `frozen=True`, y `Field()` descriptors.
- `config/resources.py` — Dataclasses con los **mismos campos** sin validación Pydantic.

`ResourceSettings.from_app_settings()` (líneas 498–612 de `resources.py`) convierte manualmente campo por campo entre ambas representaciones: ~115 líneas de boilerplate que deben mantenerse sincronizadas a mano.

Además, `_r2_endpoint_from_account()` está duplicada en ambos archivos (`config/models.py:11` y `config/resources.py:15`).

**Archivos afectados**:
- `src/orchid_commons/config/models.py`
- `src/orchid_commons/config/resources.py`
- `src/orchid_commons/runtime/manager.py`

**Recomendación**: Eliminar los dataclasses de `resources.py` y usar directamente los modelos Pydantic de `models.py` como settings del `ResourceManager`.

---

### 4. `aiosqlite` como dependencia obligatoria

**Severidad: Alta** | **Esfuerzo: Pequeño**

En `pyproject.toml`:

```toml
dependencies = [
  "aiosqlite>=0.20.0",  # ← forzada para todos los consumidores
  "pydantic>=2.0",
]
```

Todos los demás DB providers están correctamente en extras opcionales. `aiosqlite` debería seguir el mismo patrón.

**Recomendación**: Mover a un nuevo extra `sqlite` y agregarlo al grupo `db`.

---

### 5. Estado global mutable y bootstrap no re-configurable

**Severidad: Media** | **Esfuerzo: Medio**

Múltiples variables globales de módulo:

| Módulo | Variable |
|--------|----------|
| `metrics.py` | `_DEFAULT_RECORDER` |
| `otel.py` | `_OBSERVABILITY_HANDLE`, `_REQUEST_INSTRUMENTS` |
| `langfuse.py` | `_DEFAULT_LANGFUSE_CLIENT` |
| `manager.py` | `_RESOURCE_FACTORIES`, `_BUILTIN_FACTORIES_REGISTERED` |

Problema concreto: `bootstrap_observability()` retorna el handle existente si ya fue llamado (`otel.py:236`), sin actualizar settings. Si se inicia con `enabled=False` y luego se llama con `enabled=True`, la segunda llamada devuelve el handle disabled.

Se reprodujo localmente:

```text
first False
second False True
```

**Recomendación**:
- Definir política clara: permitir rebootstrap (`force_reconfigure=True`) o exigir `shutdown_observability()` antes.
- Agregar funciones `reset_*()` explícitas para testing.
- A largo plazo, considerar un patrón de registry inyectable.

---

### 6. Semántica ambigua de `limit=0` en MongoDB `find_many`

**Severidad: Media** | **Esfuerzo: Pequeño**

- `bounded_limit = max(0, limit)` → `src/orchid_commons/db/mongodb.py:134`
- Si `limit=0`, no se aplica `.limit(...)` → `mongodb.py:139`
- Pero luego `to_list(length=bounded_limit or 1_000)` → `mongodb.py:141`

Resultado: `limit=0` devuelve hasta 1000 docs silenciosamente.

**Recomendación**: Definir semántica explícita:
- Opción A: `limit=0` es inválido → `ValueError`
- Opción B: `limit: int | None`, donde `None` = sin límite

---

### 7. Contrato de tipos inconsistente en `HealthStatus.details`

**Severidad: Media** | **Esfuerzo: Pequeño**

Definición actual (`runtime/health.py:19`):

```python
details: dict[str, str] | None = None
```

Productores que violan el tipo:
- `rabbitmq.py:165`: `{"prefetch_count": self.prefetch_count}` → `int`
- `redis.py:158`: `{"key_prefix": self.key_prefix or None}` → `None`
- `blob/router.py:209`: payload con `bool`/`object`

**Recomendación**: Cambiar a `dict[str, Any] | None`.

---

### 8. Boilerplate de métricas repetido en 7 clases

**Severidad: Media** | **Esfuerzo: Medio**

Cada resource repite ~20 líneas idénticas de observabilidad:

```python
def _observe_operation(self, operation, started, *, success): ...
def _observe_error(self, operation, started, exc): ...
def _metrics_recorder(self) -> MetricsRecorder: ...
```

Afecta: `SqliteResource`, `PostgresProvider`, `RedisCache`, `MongoDbResource`, `RabbitMqBroker`, `QdrantVectorStore`, `S3BlobStorage`.

**Recomendación**: Extraer un mixin `ObservableMixin` que encapsule la lógica. Cada resource solo declara su `_resource_name`.

---

### 9. Jerarquías de excepciones separadas

**Severidad: Media** | **Esfuerzo: Pequeño**

```
Exception
├── OrchidCommonsError (runtime/errors.py)
│   ├── MissingDependencyError
│   ├── ResourceNotFoundError
│   └── ...
│
└── ConfigError (config/errors.py)          ← NO hereda de OrchidCommonsError
    ├── ConfigFileNotFoundError
    └── ...
```

Impide capturar todas las excepciones del paquete con un solo `except OrchidCommonsError`.

**Recomendación**: `ConfigError` debe heredar de `OrchidCommonsError`.

---

### 10. CI solo con trigger manual

**Severidad: Media** | **Esfuerzo: Pequeño**

`.github/workflows/ci.yml` solo se dispara con `workflow_dispatch`. No hay CI automático en push/PR.

**Recomendación**:

```yaml
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]
  workflow_dispatch:
```

---

### 11. Helpers SQL duplicados en DB providers

**Severidad: Baja** | **Esfuerzo: Pequeño**

`_read_sql_file()` y `_collect_migration_files()` están copiados idénticamente en `sqlite.py:18–25` y `postgres.py:47–54`.

**Recomendación**: Extraer a `db/_sql_utils.py`.

---

### 12. `bootstrap_resources()` es secuencial

**Severidad: Baja** | **Esfuerzo: Pequeño**

`runtime/manager.py:382–386` inicializa cada resource secuencialmente. Para servicios con múltiples resources, `asyncio.gather()` paralelizaría la inicialización.

---

### 13. SQLite single connection

**Severidad: Media** | **Esfuerzo: Medio**

`SqliteResource` usa una sola conexión compartida. En un servidor async con múltiples requests concurrentes puede causar `database is locked` o serialización implícita. Aceptable para CLI/MCPs single-tenant, no escala para HTTP multi-request.

**Recomendación**: Documentar la limitación explícitamente.

---

### 14. `assert` en código de producción

**Severidad: Baja** | **Esfuerzo: Trivial**

`src/orchid_commons/db/qdrant.py:499`: `assert filters is not None` — se elimina con `python -O`.

**Recomendación**: Reemplazar con guard explícito.

---

### 15. Falta `py.typed` marker

**Severidad: Baja** | **Esfuerzo: Trivial**

Sin `py.typed` en `src/orchid_commons/`, consumidores downstream no pueden usar type checking contra los tipos exportados (PEP 561).

**Recomendación**: Crear `src/orchid_commons/py.typed` (archivo vacío).

---

### 16. `PgVectorSettings` sin factory asociada

**Severidad: Baja** | **Esfuerzo: Pequeño**

`PgVectorSettings` existe en `ResourceSettings` (`config/resources.py:251–256`) pero ninguna factory la procesa en `_ensure_builtin_factories()`. Es dead config.

**Recomendación**: Implementar la factory o eliminar el settings.

---

### 17. `_RetryingExporter` usa `time.sleep()`

**Severidad: Baja** | **Esfuerzo: Trivial**

`otel.py:92` bloquea el thread con `time.sleep()`. Es correcto para el thread del `BatchSpanProcessor`, pero no es obvio.

**Recomendación**: Documentar que es intencional.

---

### 18. Deuda de calidad estática (lint + typing)

**Severidad: Baja** | **Esfuerzo: Medio**

- `ruff`: imports no usados/no ordenados.
- `mypy`: errores por optional deps sin stubs, protocolos demasiado estrictos frente a firmas reales de MinIO.

**Recomendación**:
- Definir baseline de tipado por módulo.
- Configurar `mypy` con `ignore_missing_imports` selectivo para optional deps.

---

## Plan de remediación

### Fase 1 — Crítico (corto plazo)

1. Arreglar rollback en `ResourceManager.startup` y fix `close_all()` (#1).
2. Corregir paths de fixtures en tests (#2).
3. Eliminar settings duplicados (#3).
4. Mover `aiosqlite` a extra opcional (#4).
5. Activar CI en push/PR (#10).

### Fase 2 — Estabilidad de contrato

1. Definir semántica de `limit` en MongoDB (#6).
2. Ajustar tipo de `HealthStatus.details` (#7).
3. Unificar jerarquía de excepciones (#9).
4. Documentar política de rebootstrap en observabilidad (#5).

### Fase 3 — Higiene y reducción de deuda

1. Extraer mixin de métricas (#8).
2. Extraer SQL helpers compartidos (#11).
3. Limpiar warnings de `ruff` y reducir ruido de `mypy` (#18).
4. Agregar `py.typed` (#15).
5. Resolver `PgVectorSettings` (#16) y `assert` en qdrant (#14).

---

## Resumen por prioridad

| Prioridad | # | Hallazgo | Esfuerzo |
|-----------|---|----------|----------|
| **Alta** | 1 | Fuga de recursos en startup parcial + `close_all()` | Pequeño |
| **Alta** | 2 | Fixtures mal referenciados en tests | Pequeño |
| **Alta** | 3 | Settings duplicados (models.py vs resources.py) | Grande |
| **Alta** | 4 | `aiosqlite` como dependencia obligatoria | Pequeño |
| **Media** | 5 | Estado global mutable / bootstrap no re-configurable | Medio |
| **Media** | 6 | Semántica ambigua de `limit=0` en MongoDB | Pequeño |
| **Media** | 7 | `HealthStatus.details` tipado como `dict[str, str]` | Pequeño |
| **Media** | 8 | Boilerplate de métricas en 7 clases | Medio |
| **Media** | 9 | Jerarquías de excepciones separadas | Pequeño |
| **Media** | 10 | CI solo manual | Pequeño |
| **Media** | 13 | SQLite single connection (documentar) | Pequeño |
| **Baja** | 11 | SQL helpers duplicados | Pequeño |
| **Baja** | 12 | Bootstrap secuencial de resources | Pequeño |
| **Baja** | 14 | `assert` en qdrant.py | Trivial |
| **Baja** | 15 | Falta `py.typed` marker | Trivial |
| **Baja** | 16 | `PgVectorSettings` sin factory | Pequeño |
| **Baja** | 17 | `time.sleep()` en retry exporter (documentar) | Trivial |
| **Baja** | 18 | Deuda de calidad estática (ruff + mypy) | Medio |

---

## Riesgos residuales

- No se ejecutaron tests de integración en esta corrida.
- No se auditó rendimiento/carga bajo stress real.
- No se revisaron contratos de versionado semver ni estrategia de deprecaciones API.
