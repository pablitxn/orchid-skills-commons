# Auditoria Tecnica - orchid-skills-commons

**Fecha**: 2026-02-11  
**Repositorio**: `orchid_skills_commons_py`  
**Objetivo**: revisar exhaustivamente el paquete commons y definir mejoras concretas.

## Estado actual (medido)

- `uv run pytest -m 'not integration'`: **239 passed**
- `uv run pytest` (con extras completos): **1 failed, 265 passed, 2 skipped**
- `uv run ruff check .`: **8 errores**
- `uv run ruff format --check .`: **19 archivos por formatear**
- `uv run mypy src`: **21 errores**

## Hallazgos Prioritarios

### P0 - Criticos

1. **Qdrant roto con cliente actual**
   - Sintoma: `AsyncQdrantClient` no tiene `search` en versiones nuevas.
   - Evidencia: falla en `tests/integration/test_qdrant_integration.py`.
   - Archivo: `src/orchid_commons/db/qdrant.py`.
   - Impacto: la ruta principal de vector search falla en ejecucion real.

2. **`ResourceManager` no bootstrappea `r2`**
   - Existe `R2Settings` + `create_r2_profile`, pero factory builtin no se registra.
   - Archivo: `src/orchid_commons/runtime/manager.py`.
   - Impacto: configuracion `resources.r2` se ignora en startup normal.

3. **Riesgo de shutdown en multi-bucket**
   - `MultiBucketBlobRouter` comparte cliente entre storages, pero lo cierra por alias.
   - Archivo: `src/orchid_commons/blob/router.py`.
   - Impacto: potencial `ExceptionGroup` en clientes no idempotentes al cerrar.

### P1 - Altos

4. **Bootstrap pierde errores concurrentes**
   - En fallas multiples de factories, se lanza solo el primer error.
   - Archivo: `src/orchid_commons/runtime/manager.py`.
   - Impacto: diagnostico incompleto en incidentes de arranque.

5. **Runner de integracion no portable**
   - Usa `cwd` absoluto local.
   - Archivo: `tests/integration/run_e2e.py`.
   - Impacto: rompe ejecucion fuera de la maquina del autor.

6. **Deuda de tipado**
   - Errores por `SecretStr` y por `ClassVar` vs mixin.
   - Archivos: `src/orchid_commons/config/models.py`, `src/orchid_commons/observability/_observable.py`, `src/orchid_commons/db/*`.
   - Impacto: menor confiabilidad del contrato estatico.

7. **Deuda de lint/format**
   - Imports, `__all__` y un `zip()` sin `strict=`.
   - Archivos: `src/orchid_commons/__init__.py`, `src/orchid_commons/observability/__init__.py`, `tests/unit/test_http_errors.py`, etc.

### P2 - Medios

8. **CI solo manual**
   - Workflow con `workflow_dispatch` unicamente.
   - Archivo: `.github/workflows/ci.yml`.
   - Impacto: regresiones llegan a `main` sin validacion automatica.

9. **Cobertura baja en zonas de riesgo**
   - `db/qdrant`, `observability/http*` por debajo del resto del paquete.
   - Impacto: mayor probabilidad de regresiones en APIs de integracion.

10. **Caso E2E de Qdrant skippeado**
    - Archivo: `tests/integration/test_e2e_all_modules.py`.
    - Impacto: se oculto incompatibilidad de API de cliente.

## Plan de remediacion (checklist)

### Iteracion 1 (ahora)

- [x] Corregir compatibilidad de Qdrant con `qdrant-client` actual.
- [x] Registrar factory builtin para `r2`.
- [x] Corregir cierre de `MultiBucketBlobRouter`.
- [x] Hacer portable `tests/integration/run_e2e.py`.
- [x] Ajustar tests unitarios/integracion afectados.
- [x] Ejecutar validacion de tests para confirmar mejoras.

### Iteracion 2

- [ ] Corregir deuda `ruff`/`format` completa.
- [ ] Resolver errores `mypy` prioritarios.
- [ ] Mejorar reporte de errores concurrentes en bootstrap.

### Iteracion 3

- [ ] Endurecer CI (`push`, `pull_request`, `mypy`, lint, tests).
- [ ] Subir cobertura en `qdrant` y `observability/http*`.

## Notas de ejecucion

- Este documento es backlog vivo: se actualiza en cada iteracion con estado real.
