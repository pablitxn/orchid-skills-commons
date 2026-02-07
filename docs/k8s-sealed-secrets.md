# Recomendaciones de Sealed Secrets para Kubernetes

## Objetivo

Gestionar configuracion sensible de `romy-skills`, `youtube-mcp` y servicios del ecosistema sin exponer secretos en Git.

## Principios

- Nunca commitear `Secret` en texto plano.
- Committear solo recursos `SealedSecret`.
- Separar secretos por entorno y namespace.
- Minimizar alcance de cada secreto (principio de menor privilegio).

## Inventario minimo sugerido

| Proyecto | Secretos sugeridos |
| --- | --- |
| `romy-skills` | `DATABASE_URL`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `ORCHID_LANGFUSE_PUBLIC_KEY`, `ORCHID_LANGFUSE_SECRET_KEY` |
| `youtube-mcp` | `YOUTUBE_RAG__BLOB_STORAGE__ACCESS_KEY`, `YOUTUBE_RAG__BLOB_STORAGE__SECRET_KEY`, `YOUTUBE_RAG__VECTOR_DB__API_KEY`, `YOUTUBE_RAG__LLM__API_KEY`, `YOUTUBE_RAG__TELEMETRY__LANGFUSE__PUBLIC_KEY`, `YOUTUBE_RAG__TELEMETRY__LANGFUSE__SECRET_KEY` |

## Convenciones recomendadas

- Nombre del secreto: `<app>-runtime-secrets`.
- Namespace por entorno:
  - `orchid-dev`
  - `orchid-staging`
  - `orchid-prod`
- Un `SealedSecret` por app y entorno.
- Etiquetas minimas: `app`, `environment`, `managed-by=sealed-secrets`.

## Flujo operativo (por entorno)

### 1. Crear Secret temporal local

```bash
kubectl -n orchid-staging create secret generic romy-skills-runtime-secrets \
  --from-literal=DATABASE_URL="$DATABASE_URL" \
  --from-literal=OTEL_EXPORTER_OTLP_ENDPOINT="$OTEL_EXPORTER_OTLP_ENDPOINT" \
  --dry-run=client -o yaml > /tmp/romy-skills-secret.yaml
```

### 2. Sellar con clave del cluster destino

```bash
kubeseal \
  --controller-name=sealed-secrets \
  --controller-namespace=kube-system \
  --format=yaml \
  < /tmp/romy-skills-secret.yaml \
  > infrastructure/overlays/staging/romy-skills-sealed-secret.yaml
```

### 3. Borrar archivo temporal en claro

```bash
rm /tmp/romy-skills-secret.yaml
```

### 4. Aplicar manifiestos

```bash
kubectl apply -k infrastructure/overlays/staging
kubectl -n orchid-staging get sealedsecret,secrets
```

## Template recomendado

```yaml
apiVersion: bitnami.com/v1alpha1
kind: SealedSecret
metadata:
  name: romy-skills-runtime-secrets
  namespace: orchid-staging
  labels:
    app: romy-skills
    environment: staging
    managed-by: sealed-secrets
spec:
  encryptedData:
    DATABASE_URL: <sealed-value>
    OTEL_EXPORTER_OTLP_ENDPOINT: <sealed-value>
  template:
    metadata:
      name: romy-skills-runtime-secrets
      namespace: orchid-staging
      labels:
        app: romy-skills
        environment: staging
        managed-by: sealed-secrets
    type: Opaque
```

## Rotacion de secretos

### Frecuencia recomendada

- Credenciales de DB y storage: cada 90 dias.
- API keys de proveedores externos: cada 30-90 dias (segun politica).

### Procedimiento

1. Crear nuevo secreto en claro local.
2. Generar nuevo `SealedSecret`.
3. Deploy en staging y validar health checks.
4. Promover a prod en ventana controlada.
5. Revocar credenciales viejas.

## Controles de seguridad adicionales

- Restringir acceso RBAC a `secrets` y `sealedsecrets`.
- Activar auditoria de cambios (Git + Kubernetes events).
- Evitar reutilizar misma credencial entre entornos.
- En CI, bloquear commits que incluyan patrones de secretos en texto plano.

## Checklist rapido de aceptacion

- [ ] No hay `Secret` en claro versionado.
- [ ] Cada entorno tiene su propio `SealedSecret`.
- [ ] Rotacion documentada y probada.
- [ ] Runbook de recuperacion ante filtracion disponible.
