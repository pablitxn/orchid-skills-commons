"""Qdrant vector database provider."""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any

from orchid_commons.config.resources import QdrantSettings
from orchid_commons.observability.metrics import MetricsRecorder, get_metrics_recorder
from orchid_commons.runtime.errors import MissingDependencyError
from orchid_commons.runtime.health import HealthStatus


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


@dataclass(slots=True)
class QdrantVectorStore:
    """Managed Qdrant client with common vector operations."""

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
            api_key=settings.api_key,
            timeout=settings.timeout_seconds,
            prefer_grpc=settings.prefer_grpc,
        )
        store = cls(_client=client, collection_prefix=settings.collection_prefix)
        await store.health_check()
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
        return _collection_name(self.collection_prefix, collection_name)

    async def create_collection(
        self,
        collection_name: str,
        *,
        vector_size: int,
        distance: str = "cosine",
    ) -> None:
        """Create a collection with vector params."""
        started = perf_counter()
        models = _import_qdrant_models()

        distance_key = distance.strip().upper()
        distance_map = {
            "COSINE": models.Distance.COSINE,
            "DOT": models.Distance.DOT,
            "EUCLID": models.Distance.EUCLID,
            "MANHATTAN": models.Distance.MANHATTAN,
        }
        resolved_distance = distance_map.get(distance_key)
        if resolved_distance is None:
            raise ValueError("distance must be one of: cosine, dot, euclid, manhattan")

        try:
            await self._client.create_collection(
                collection_name=self.scoped_collection(collection_name),
                vectors_config=models.VectorParams(
                    size=vector_size,
                    distance=resolved_distance,
                ),
            )
        except Exception as exc:
            self._observe_error("create_collection", started, exc)
            raise

        self._observe_operation("create_collection", started, success=True)

    async def upsert(
        self,
        collection_name: str,
        points: list[dict[str, Any] | Any],
    ) -> None:
        """Upsert points into a collection."""
        started = perf_counter()
        models = _import_qdrant_models()

        normalized_points: list[Any] = []
        for point in points:
            if isinstance(point, dict):
                normalized_points.append(
                    models.PointStruct(
                        id=point["id"],
                        vector=point["vector"],
                        payload=point.get("payload"),
                    )
                )
            else:
                normalized_points.append(point)

        try:
            await self._client.upsert(
                collection_name=self.scoped_collection(collection_name),
                points=normalized_points,
            )
        except Exception as exc:
            self._observe_error("upsert", started, exc)
            raise

        self._observe_operation("upsert", started, success=True)

    async def search(
        self,
        collection_name: str,
        query_vector: list[float],
        *,
        limit: int = 10,
        score_threshold: float | None = None,
        with_payload: bool = True,
        with_vectors: bool = False,
    ) -> list[dict[str, Any]]:
        """Search nearest vectors and return normalized result dictionaries."""
        started = perf_counter()
        try:
            results = await self._client.search(
                collection_name=self.scoped_collection(collection_name),
                query_vector=query_vector,
                limit=limit,
                score_threshold=score_threshold,
                with_payload=with_payload,
                with_vectors=with_vectors,
            )
        except Exception as exc:
            self._observe_error("search", started, exc)
            raise

        self._observe_operation("search", started, success=True)
        normalized: list[dict[str, Any]] = []
        for result in results:
            normalized.append(
                {
                    "id": getattr(result, "id", None),
                    "score": getattr(result, "score", None),
                    "payload": getattr(result, "payload", None),
                    "vector": getattr(result, "vector", None),
                }
            )
        return normalized

    async def delete_ids(self, collection_name: str, ids: list[int | str]) -> None:
        """Delete points by id."""
        started = perf_counter()
        models = _import_qdrant_models()
        try:
            await self._client.delete(
                collection_name=self.scoped_collection(collection_name),
                points_selector=models.PointIdsList(points=ids),
            )
        except Exception as exc:
            self._observe_error("delete_ids", started, exc)
            raise

        self._observe_operation("delete_ids", started, success=True)

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
            self._observe_error("close", started, exc)
            raise
        finally:
            self._closed = True

        self._observe_operation("close", started, success=True)

    def _observe_operation(self, operation: str, started: float, *, success: bool) -> None:
        self._metrics_recorder().observe_operation(
            resource="qdrant",
            operation=operation,
            duration_seconds=perf_counter() - started,
            success=success,
        )

    def _observe_error(self, operation: str, started: float, exc: Exception) -> None:
        self._observe_operation(operation, started, success=False)
        self._metrics_recorder().observe_error(
            resource="qdrant",
            operation=operation,
            error_type=type(exc).__name__,
        )

    def _metrics_recorder(self) -> MetricsRecorder:
        return get_metrics_recorder() if self._metrics is None else self._metrics


async def create_qdrant_vector_store(settings: QdrantSettings) -> QdrantVectorStore:
    """Factory used by ResourceManager bootstrap."""
    return await QdrantVectorStore.create(settings)
