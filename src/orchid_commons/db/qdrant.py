"""Qdrant vector database provider."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from time import perf_counter
from typing import Any, ClassVar

from orchid_commons.config.resources import QdrantSettings
from orchid_commons.db.vector import (
    VectorAuthError,
    VectorNotFoundError,
    VectorOperationError,
    VectorPoint,
    VectorSearchResult,
    VectorStore,
    VectorStoreError,
    VectorTransientError,
    VectorValidationError,
)
from orchid_commons.observability._observable import ObservableMixin
from orchid_commons.observability.metrics import MetricsRecorder
from orchid_commons.runtime.errors import MissingDependencyError
from orchid_commons.runtime.health import HealthStatus

_TRANSIENT_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
_AUTH_STATUS_CODES = {401, 403}
_NOT_FOUND_STATUS_CODES = {404}


def _import_qdrant_async_client() -> Any:
    try:
        from qdrant_client.async_qdrant_client import AsyncQdrantClient
    except ImportError as exc:  # pragma: no cover - exercised when extras are absent
        raise MissingDependencyError(
            "Qdrant provider requires optional dependency 'qdrant-client'. "
            "Install with: uv sync --extra qdrant (or --extra db)"
        ) from exc
    return AsyncQdrantClient


def _import_qdrant_models() -> Any:
    try:
        from qdrant_client import models
    except ImportError as exc:  # pragma: no cover - exercised when extras are absent
        raise MissingDependencyError(
            "Qdrant provider requires optional dependency 'qdrant-client'. "
            "Install with: uv sync --extra qdrant (or --extra db)"
        ) from exc
    return models


def _collection_name(prefix: str, name: str) -> str:
    if not prefix:
        return name
    return f"{prefix}_{name}"


def _extract_status_code(exc: Exception) -> int | None:
    for attr in ("status_code", "status", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value

    response = getattr(exc, "response", None)
    response_status = getattr(response, "status_code", None)
    if isinstance(response_status, int):
        return response_status

    return None


def _looks_transient(exc: Exception) -> bool:
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return True

    name = type(exc).__name__.lower()
    return any(
        token in name
        for token in (
            "timeout",
            "temporar",
            "connection",
            "connect",
            "unavailable",
            "retry",
        )
    )


def _translate_qdrant_error(
    *,
    operation: str,
    collection: str | None,
    exc: Exception,
) -> VectorStoreError:
    if isinstance(exc, VectorStoreError):
        return exc

    status_code = _extract_status_code(exc)
    message = str(exc) or type(exc).__name__

    if status_code in _AUTH_STATUS_CODES:
        return VectorAuthError(operation=operation, collection=collection, message=message)
    if status_code in _NOT_FOUND_STATUS_CODES:
        return VectorNotFoundError(operation=operation, collection=collection, message=message)
    if status_code in _TRANSIENT_STATUS_CODES or _looks_transient(exc):
        return VectorTransientError(operation=operation, collection=collection, message=message)

    return VectorOperationError(operation=operation, collection=collection, message=message)


def _normalize_vector(raw: Any) -> list[float] | None:
    if raw is None:
        return None

    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
        return [float(value) for value in raw]

    if isinstance(raw, Mapping):
        for value in raw.values():
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
                return [float(elem) for elem in value]

    return None


def _ensure_non_empty_collection_name(name: str) -> str:
    normalized = name.strip()
    if not normalized:
        raise VectorValidationError(
            operation="validate_collection",
            collection=None,
            message="collection_name must be a non-empty string",
        )
    return normalized


def _build_filter(filters: Mapping[str, Any], *, models: Any) -> Any:
    conditions: list[Any] = []

    for field, value in filters.items():
        if isinstance(value, Mapping):
            for operator, operator_value in value.items():
                if operator == "$gte":
                    conditions.append(
                        models.FieldCondition(
                            key=field,
                            range=models.Range(gte=operator_value),
                        )
                    )
                elif operator == "$gt":
                    conditions.append(
                        models.FieldCondition(
                            key=field,
                            range=models.Range(gt=operator_value),
                        )
                    )
                elif operator == "$lte":
                    conditions.append(
                        models.FieldCondition(
                            key=field,
                            range=models.Range(lte=operator_value),
                        )
                    )
                elif operator == "$lt":
                    conditions.append(
                        models.FieldCondition(
                            key=field,
                            range=models.Range(lt=operator_value),
                        )
                    )
                elif operator == "$in":
                    if not isinstance(operator_value, Sequence) or isinstance(
                        operator_value, (str, bytes, bytearray)
                    ):
                        raise VectorValidationError(
                            operation="build_filter",
                            collection=None,
                            message=f"filter operator '$in' expects a list-like value for '{field}'",
                        )
                    conditions.append(
                        models.FieldCondition(
                            key=field,
                            match=models.MatchAny(any=list(operator_value)),
                        )
                    )
                else:
                    raise VectorValidationError(
                        operation="build_filter",
                        collection=None,
                        message=(
                            f"unsupported filter operator '{operator}' for field '{field}'. "
                            "Allowed: $gte, $gt, $lte, $lt, $in"
                        ),
                    )
        else:
            conditions.append(
                models.FieldCondition(
                    key=field,
                    match=models.MatchValue(value=value),
                )
            )

    return models.Filter(must=conditions)


@dataclass(slots=True)
class QdrantVectorStore(ObservableMixin, VectorStore):
    """Managed Qdrant client with common vector operations."""

    _resource_name: ClassVar[str] = "qdrant"

    _client: Any
    collection_prefix: str = ""
    _metrics: MetricsRecorder | None = None
    _closed: bool = False

    @classmethod
    async def create(cls, settings: QdrantSettings) -> QdrantVectorStore:
        """Create and validate a Qdrant vector store from settings."""
        async_client_cls = _import_qdrant_async_client()
        client = async_client_cls(
            url=settings.url,
            host=settings.host,
            port=settings.port,
            grpc_port=settings.grpc_port,
            https=settings.use_ssl,
            api_key=settings.api_key.get_secret_value() if settings.api_key else None,
            timeout=settings.timeout_seconds,
            prefer_grpc=settings.prefer_grpc,
        )
        store = cls(_client=client, collection_prefix=settings.collection_prefix)
        status = await store.health_check()
        if not status.healthy:
            raise VectorOperationError(
                operation="create",
                collection=None,
                message=status.message or "qdrant health check failed during startup",
            )
        return store

    @property
    def client(self) -> Any:
        """Expose underlying async client for advanced usage."""
        return self._client

    @property
    def is_connected(self) -> bool:
        """Whether vector store can still serve requests."""
        return not self._closed

    def scoped_collection(self, collection_name: str) -> str:
        """Return collection name with optional prefix."""
        normalized = _ensure_non_empty_collection_name(collection_name)
        return _collection_name(self.collection_prefix, normalized)

    async def create_collection(
        self,
        collection_name: str,
        *,
        vector_size: int,
        distance: str = "cosine",
    ) -> None:
        """Create a collection with vector params."""
        if vector_size <= 0:
            raise VectorValidationError(
                operation="create_collection",
                collection=collection_name,
                message="vector_size must be > 0",
            )

        started = perf_counter()
        scoped_collection = self.scoped_collection(collection_name)
        models = _import_qdrant_models()

        distance_key = distance.strip().upper()
        distance_map = {
            "COSINE": models.Distance.COSINE,
            "DOT": models.Distance.DOT,
            "EUCLID": models.Distance.EUCLID,
            "EUCLIDEAN": models.Distance.EUCLID,
            "MANHATTAN": models.Distance.MANHATTAN,
        }
        resolved_distance = distance_map.get(distance_key)
        if resolved_distance is None:
            raise VectorValidationError(
                operation="create_collection",
                collection=collection_name,
                message="distance must be one of: cosine, dot, euclid, euclidean, manhattan",
            )

        try:
            await self._client.create_collection(
                collection_name=scoped_collection,
                vectors_config=models.VectorParams(
                    size=vector_size,
                    distance=resolved_distance,
                ),
            )
        except Exception as exc:
            translated = _translate_qdrant_error(
                operation="create_collection",
                collection=scoped_collection,
                exc=exc,
            )
            self._observe_error("create_collection", started, translated)
            raise translated from exc

        self._observe_operation("create_collection", started, success=True)

    async def upsert(
        self,
        collection_name: str,
        points: Sequence[VectorPoint | Mapping[str, Any] | Any],
    ) -> int:
        """Insert or update points into a collection."""
        started = perf_counter()
        scoped_collection = self.scoped_collection(collection_name)
        models = _import_qdrant_models()

        normalized_points: list[Any] = []
        for point in points:
            if isinstance(point, VectorPoint):
                normalized_points.append(
                    models.PointStruct(
                        id=point.id,
                        vector=list(point.vector),
                        payload=dict(point.payload),
                    )
                )
                continue

            if isinstance(point, Mapping):
                if "id" not in point:
                    raise VectorValidationError(
                        operation="upsert",
                        collection=collection_name,
                        message="point mapping requires key 'id'",
                    )
                if "vector" not in point:
                    raise VectorValidationError(
                        operation="upsert",
                        collection=collection_name,
                        message="point mapping requires key 'vector'",
                    )

                payload = point.get("payload")
                normalized_payload: Mapping[str, Any] | None
                if payload is None:
                    normalized_payload = None
                elif isinstance(payload, Mapping):
                    normalized_payload = dict(payload)
                else:
                    raise VectorValidationError(
                        operation="upsert",
                        collection=collection_name,
                        message="point payload must be a mapping",
                    )

                normalized_points.append(
                    models.PointStruct(
                        id=point["id"],
                        vector=list(point["vector"]),
                        payload=normalized_payload,
                    )
                )
                continue

            normalized_points.append(point)

        if not normalized_points:
            return 0

        try:
            await self._client.upsert(
                collection_name=scoped_collection,
                points=normalized_points,
            )
        except Exception as exc:
            translated = _translate_qdrant_error(
                operation="upsert",
                collection=scoped_collection,
                exc=exc,
            )
            self._observe_error("upsert", started, translated)
            raise translated from exc

        self._observe_operation("upsert", started, success=True)
        return len(normalized_points)

    async def search(
        self,
        collection_name: str,
        query_vector: Sequence[float],
        *,
        limit: int = 10,
        filters: Mapping[str, Any] | None = None,
        score_threshold: float | None = None,
        with_payload: bool = True,
        with_vectors: bool = False,
    ) -> list[VectorSearchResult]:
        """Search nearest vectors and return normalized typed results."""
        if limit <= 0:
            raise VectorValidationError(
                operation="search",
                collection=collection_name,
                message="limit must be > 0",
            )
        if not query_vector:
            raise VectorValidationError(
                operation="search",
                collection=collection_name,
                message="query_vector must contain at least one value",
            )

        started = perf_counter()
        scoped_collection = self.scoped_collection(collection_name)
        models = _import_qdrant_models()
        query_filter = _build_filter(filters, models=models) if filters else None

        try:
            results = await self._search_points(
                collection_name=scoped_collection,
                query_vector=list(query_vector),
                query_filter=query_filter,
                limit=limit,
                score_threshold=score_threshold,
                with_payload=with_payload,
                with_vectors=with_vectors,
            )
        except Exception as exc:
            translated = _translate_qdrant_error(
                operation="search",
                collection=scoped_collection,
                exc=exc,
            )
            self._observe_error("search", started, translated)
            raise translated from exc

        self._observe_operation("search", started, success=True)
        normalized: list[VectorSearchResult] = []
        for result in results:
            point_id = getattr(result, "id", None)
            if not isinstance(point_id, (int, str)):
                continue

            payload = getattr(result, "payload", None)
            normalized_payload = dict(payload) if isinstance(payload, Mapping) else {}

            raw_score = getattr(result, "score", 0.0)
            score = float(raw_score) if raw_score is not None else 0.0

            normalized.append(
                VectorSearchResult(
                    id=point_id,
                    score=score,
                    payload=normalized_payload,
                    vector=_normalize_vector(getattr(result, "vector", None)),
                )
            )
        return normalized

    async def _search_points(
        self,
        *,
        collection_name: str,
        query_vector: list[float],
        query_filter: Any,
        limit: int,
        score_threshold: float | None,
        with_payload: bool,
        with_vectors: bool,
    ) -> Sequence[Any]:
        """Run nearest-neighbor search across supported qdrant-client APIs."""
        search = getattr(self._client, "search", None)
        if callable(search):
            return await search(
                collection_name=collection_name,
                query_vector=query_vector,
                query_filter=query_filter,
                limit=limit,
                score_threshold=score_threshold,
                with_payload=with_payload,
                with_vectors=with_vectors,
            )

        query_points = getattr(self._client, "query_points", None)
        if callable(query_points):
            try:
                response = await query_points(
                    collection_name=collection_name,
                    query=query_vector,
                    query_filter=query_filter,
                    limit=limit,
                    score_threshold=score_threshold,
                    with_payload=with_payload,
                    with_vectors=with_vectors,
                )
            except Exception as exc:
                # New clients may hit /points/query, which older servers (<1.10)
                # don't expose. Fall back to legacy /search endpoint on 404.
                if _extract_status_code(exc) == 404:
                    legacy = await self._search_points_legacy(
                        collection_name=collection_name,
                        query_vector=query_vector,
                        query_filter=query_filter,
                        limit=limit,
                        score_threshold=score_threshold,
                        with_payload=with_payload,
                        with_vectors=with_vectors,
                    )
                    if legacy is not None:
                        return legacy
                raise

            points = getattr(response, "points", None)
            if isinstance(points, Sequence) and not isinstance(points, (str, bytes, bytearray)):
                return points
            if isinstance(response, Sequence) and not isinstance(response, (str, bytes, bytearray)):
                return response
            return []

        raise AttributeError("Qdrant client does not expose search/query_points methods")

    async def _search_points_legacy(
        self,
        *,
        collection_name: str,
        query_vector: list[float],
        query_filter: Any,
        limit: int,
        score_threshold: float | None,
        with_payload: bool,
        with_vectors: bool,
    ) -> Sequence[Any] | None:
        """Fallback using legacy `/search` HTTP endpoint for older servers."""
        http_client = getattr(self._client, "http", None)
        search_api = getattr(http_client, "search_api", None)
        search_points = getattr(search_api, "search_points", None)
        if not callable(search_points):
            return None

        models = _import_qdrant_models()
        search_request = models.SearchRequest(
            vector=query_vector,
            filter=query_filter,
            limit=limit,
            with_payload=with_payload,
            with_vector=with_vectors,
            score_threshold=score_threshold,
        )
        response = await search_points(
            collection_name=collection_name,
            search_request=search_request,
        )
        result = getattr(response, "result", None)
        if isinstance(result, Sequence) and not isinstance(result, (str, bytes, bytearray)):
            return result
        return None

    async def delete(
        self,
        collection_name: str,
        *,
        ids: Sequence[int | str] | None = None,
        filters: Mapping[str, Any] | None = None,
    ) -> int:
        """Delete points by ids or filters and return removed count."""
        if ids is not None and filters is not None:
            raise VectorValidationError(
                operation="delete",
                collection=collection_name,
                message="ids and filters are mutually exclusive",
            )
        if ids is None and filters is None:
            raise VectorValidationError(
                operation="delete",
                collection=collection_name,
                message="either ids or filters must be provided",
            )

        started = perf_counter()
        scoped_collection = self.scoped_collection(collection_name)
        models = _import_qdrant_models()

        try:
            if ids is not None:
                normalized_ids = list(ids)
                if not normalized_ids:
                    return 0
                await self._client.delete(
                    collection_name=scoped_collection,
                    points_selector=models.PointIdsList(points=normalized_ids),
                )
                self._observe_operation("delete", started, success=True)
                return len(normalized_ids)

            if filters is None:
                raise ValueError("filters must not be None")
            qdrant_filter = _build_filter(filters, models=models)
            count_before = await self.count(collection_name, filters=filters)
            await self._client.delete(
                collection_name=scoped_collection,
                points_selector=models.FilterSelector(filter=qdrant_filter),
            )
            count_after = await self.count(collection_name, filters=filters)
        except Exception as exc:
            translated = _translate_qdrant_error(
                operation="delete",
                collection=scoped_collection,
                exc=exc,
            )
            self._observe_error("delete", started, translated)
            raise translated from exc

        self._observe_operation("delete", started, success=True)
        return max(0, count_before - count_after)

    async def delete_ids(self, collection_name: str, ids: list[int | str]) -> int:
        """Compatibility wrapper: delete points by id."""
        return await self.delete(collection_name, ids=ids)

    async def delete_by_filter(
        self,
        collection_name: str,
        filters: Mapping[str, Any],
    ) -> int:
        """Delete points matching filters."""
        return await self.delete(collection_name, filters=filters)

    async def count(
        self,
        collection_name: str,
        *,
        filters: Mapping[str, Any] | None = None,
    ) -> int:
        """Count points in a collection."""
        started = perf_counter()
        scoped_collection = self.scoped_collection(collection_name)
        models = _import_qdrant_models()
        qdrant_filter = _build_filter(filters, models=models) if filters else None

        try:
            result = await self._client.count(
                collection_name=scoped_collection,
                count_filter=qdrant_filter,
                exact=True,
            )
        except Exception as exc:
            translated = _translate_qdrant_error(
                operation="count",
                collection=scoped_collection,
                exc=exc,
            )
            self._observe_error("count", started, translated)
            raise translated from exc

        self._observe_operation("count", started, success=True)
        return int(getattr(result, "count", 0))

    async def health_check(self) -> HealthStatus:
        """Verify Qdrant liveness by listing collections."""
        started = perf_counter()
        try:
            await self._client.get_collections()
            latency_ms = (perf_counter() - started) * 1000
            return HealthStatus(
                healthy=True,
                latency_ms=latency_ms,
                message="ok",
            )
        except Exception as exc:
            latency_ms = (perf_counter() - started) * 1000
            return HealthStatus(
                healthy=False,
                latency_ms=latency_ms,
                message=str(exc),
                details={"error_type": type(exc).__name__},
            )

    async def close(self) -> None:
        """Close Qdrant client if available."""
        started = perf_counter()
        try:
            close = getattr(self._client, "close", None)
            if callable(close):
                maybe_awaitable = close()
                if hasattr(maybe_awaitable, "__await__"):
                    await maybe_awaitable
        except Exception as exc:
            translated = _translate_qdrant_error(
                operation="close",
                collection=None,
                exc=exc,
            )
            self._observe_error("close", started, translated)
            raise translated from exc
        finally:
            self._closed = True

        self._observe_operation("close", started, success=True)


async def create_qdrant_vector_store(settings: QdrantSettings) -> QdrantVectorStore:
    """Factory used by ResourceManager bootstrap."""
    return await QdrantVectorStore.create(settings)
