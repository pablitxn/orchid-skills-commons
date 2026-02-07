# youtube-mcp -> orchid_commons Extraction Map

## Objetivo

Documentar el pasaje de componentes compartibles de `youtube-mcp` hacia `orchid_commons`:

- Blob (`MinIO/S3`),
- Vector (estrategia de transicion),
- Observabilidad (logging, OTEL, Langfuse).

## Estado actual en `youtube-mcp`

| Area | Implementacion actual |
| --- | --- |
| Blob | `src/commons/infrastructure/blob/minio_provider.py` |
| Vector | `src/commons/infrastructure/vectordb/qdrant_provider.py` |
| Logging | `src/commons/telemetry/logger.py` |
| Langfuse | `src/commons/telemetry/langfuse_client.py` |
| Bootstrap de infra | `src/infrastructure/factory.py` y `src/api/dependencies.py` |

## Estado objetivo en commons

| Area | Commons target |
| --- | --- |
| Blob | `MinioProfile` / `S3BlobStorage` |
| Vector | Fase corta: adapter Qdrant local. Fase larga: provider comun (pgvector) |
| Logging | `bootstrap_logging_from_app_settings` |
| OTEL | `bootstrap_observability` |
| Langfuse | `create_langfuse_client` |
| Recursos | `ResourceManager` + `ResourceSettings.from_app_settings(...)` |

## Mapa de extraccion detallado

| Modulo actual | Destino | Estado sugerido |
| --- | --- | --- |
| `src/commons/infrastructure/blob/minio_provider.py` | `orchid_commons.blob.minio.MinioProfile` | Extraer ahora (bajo riesgo). |
| `src/commons/telemetry/logger.py` | `orchid_commons.logging.bootstrap_logging_from_app_settings` | Extraer ahora (bajo riesgo). |
| `src/commons/telemetry/langfuse_client.py` | `orchid_commons.observability.langfuse.create_langfuse_client` | Extraer ahora (riesgo medio por cambio de API). |
| `src/commons/infrastructure/vectordb/qdrant_provider.py` | Adapter temporal + roadmap a provider comun | Extraer por fases (riesgo medio/alto). |

## Plan por fases

### Fase 1: Blob

1. Reemplazar instanciacion de `MinioBlobStorage(...)` en `InfrastructureFactory`.
2. Construir `MinioSettings` desde config.
3. Inicializar `MinioProfile` y validar `health_check`.

Impacto esperado:

- Menos codigo propio de almacenamiento.
- Errores tipados unificados (`BlobNotFoundError`, `BlobAuthError`, etc.).

### Fase 2: Observabilidad

1. En startup/lifespan, cargar `appsettings` compartido.
2. Aplicar `bootstrap_logging_from_app_settings(...)`.
3. Aplicar `bootstrap_observability(...)`.
4. Reemplazar cliente Langfuse local por `create_langfuse_client(...)`.

Impacto esperado:

- Formato de logs estandar.
- Trazas OTEL y spans de recursos consistentes.

### Fase 3: Vector

Estado actual: `orchid_commons` todavia no expone provider vectorial equivalente a Qdrant.

Ruta recomendada:

1. Mantener `QdrantVectorDB` en `youtube-mcp` con adapter de frontera.
2. Estandarizar interfaz de vector en capa de aplicacion (puerto unico).
3. Cuando exista provider comun, migrar implementacion sin tocar casos de uso.
4. Evaluar convergencia a PostgreSQL + pgvector (solo cuando el provider comun este listo).

## Compatibilidad de contratos (puntos criticos)

| Tema | Antes | Despues | Mitigacion |
| --- | --- | --- | --- |
| Bucket por operacion | `upload(bucket, path, ...)` | `upload(key, ...)` con bucket fijo en settings | Adapter temporal con mapeo de bucket. |
| Presigned URL | `generate_presigned_url` custom | `presign(method="GET"|"PUT")` | Mantener helper de compatibilidad durante transicion. |
| Langfuse API | Estado global singleton | Cliente explicito (`LangfuseClient`) | Inyectar cliente en servicios LLM. |
| Vector provider | Qdrant directo | Qdrant adaptado / futuro provider comun | Evitar acoplamiento en servicios de dominio. |

## Checklist de validacion

- [ ] Ingestion y query siguen funcionando con blob extraido a commons.
- [ ] Logs contienen `service`, `env`, `trace_id`, `request_id`.
- [ ] Trazas OTEL llegan al collector configurado.
- [ ] Eventos Langfuse se registran sin warning de inicializacion duplicada.
- [ ] Tests unitarios de factory y servicios ajustados al nuevo wiring.

## Rollback

- Mantener feature flag de infraestructura para volver temporalmente a providers locales.
- Guardar adapters de compatibilidad hasta completar estabilizacion en staging.
- Versionar cambios de config por entorno para reversa rapida.
