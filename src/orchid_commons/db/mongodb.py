"""MongoDB resource provider backed by Motor async client."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import perf_counter
from typing import Any, ClassVar

from orchid_commons.config.resources import MongoDbSettings
from orchid_commons.observability._observable import ObservableMixin
from orchid_commons.observability.metrics import MetricsRecorder
from orchid_commons.runtime.errors import MissingDependencyError
from orchid_commons.runtime.health import HealthStatus


def _import_motor_asyncio() -> Any:
    try:
        from motor import motor_asyncio
    except ImportError as exc:  # pragma: no cover - exercised when extras are absent
        raise MissingDependencyError(
            "MongoDB provider requires optional dependency 'motor'. "
            "Install with: uv sync --extra mongodb (or --extra db)"
        ) from exc
    return motor_asyncio


@dataclass(slots=True)
class MongoDbResource(ObservableMixin):
    """Managed MongoDB resource with thin collection helpers."""

    _resource_name: ClassVar[str] = "mongodb"

    _client: Any
    _database: Any
    database_name: str
    ping_timeout_seconds: float = 2.0
    _metrics: MetricsRecorder | None = None
    _closed: bool = False

    @classmethod
    async def create(cls, settings: MongoDbSettings) -> MongoDbResource:
        """Create and validate a MongoDB resource from settings."""
        motor_asyncio = _import_motor_asyncio()
        client = motor_asyncio.AsyncIOMotorClient(
            settings.uri.get_secret_value(),
            serverSelectionTimeoutMS=settings.server_selection_timeout_ms,
            connectTimeoutMS=settings.connect_timeout_ms,
            appname=settings.app_name,
        )
        resource = cls(
            _client=client,
            _database=client[settings.database],
            database_name=settings.database,
            ping_timeout_seconds=settings.ping_timeout_seconds,
        )
        await resource.ping()
        return resource

    @property
    def client(self) -> Any:
        """Expose underlying Motor client for advanced usage."""
        return self._client

    @property
    def database(self) -> Any:
        """Expose active database handle."""
        return self._database

    @property
    def is_connected(self) -> bool:
        """Whether resource can still serve requests."""
        return not self._closed

    def collection(self, name: str) -> Any:
        """Return a collection handle by name."""
        return self._database[name]

    async def ping(self) -> bool:
        """Run MongoDB ping command."""
        started = perf_counter()
        try:
            await asyncio.wait_for(
                self._database.command("ping"),
                timeout=self.ping_timeout_seconds,
            )
        except Exception as exc:
            self._observe_error("ping", started, exc)
            raise

        self._observe_operation("ping", started, success=True)
        return True

    async def insert_one(self, collection: str, document: dict[str, Any]) -> Any:
        """Insert a single document and return inserted id."""
        started = perf_counter()
        try:
            result = await self.collection(collection).insert_one(document)
        except Exception as exc:
            self._observe_error("insert_one", started, exc)
            raise

        self._observe_operation("insert_one", started, success=True)
        return result.inserted_id

    async def find_one(
        self,
        collection: str,
        query: dict[str, Any],
        *,
        projection: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Find a single document by query."""
        started = perf_counter()
        try:
            document = await self.collection(collection).find_one(query, projection=projection)
        except Exception as exc:
            self._observe_error("find_one", started, exc)
            raise

        self._observe_operation("find_one", started, success=True)
        if document is None:
            return None
        return dict(document)

    async def find_many(
        self,
        collection: str,
        query: dict[str, Any],
        *,
        projection: dict[str, Any] | None = None,
        sort: list[tuple[str, int]] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Find multiple documents with optional sort and limit.

        Args:
            limit: Maximum number of documents to return.  Must be a positive
                   integer or ``None`` (no limit, capped to 1 000 for safety).
                   Passing ``0`` or a negative value raises ``ValueError``.
        """
        if limit is not None and limit <= 0:
            raise ValueError("limit must be a positive integer or None")

        started = perf_counter()
        try:
            cursor = self.collection(collection).find(query, projection=projection)
            if sort:
                cursor = cursor.sort(sort)
            if limit is not None:
                cursor = cursor.limit(limit)
            documents = await cursor.to_list(length=limit if limit is not None else 1_000)
        except Exception as exc:
            self._observe_error("find_many", started, exc)
            raise

        self._observe_operation("find_many", started, success=True)
        return [dict(document) for document in documents]

    async def update_one(
        self,
        collection: str,
        query: dict[str, Any],
        update: dict[str, Any],
        *,
        upsert: bool = False,
    ) -> int:
        """Update a single document and return modified count."""
        started = perf_counter()
        try:
            result = await self.collection(collection).update_one(query, update, upsert=upsert)
        except Exception as exc:
            self._observe_error("update_one", started, exc)
            raise

        self._observe_operation("update_one", started, success=True)
        return int(result.modified_count)

    async def delete_one(self, collection: str, query: dict[str, Any]) -> int:
        """Delete a single document and return removed count."""
        started = perf_counter()
        try:
            result = await self.collection(collection).delete_one(query)
        except Exception as exc:
            self._observe_error("delete_one", started, exc)
            raise

        self._observe_operation("delete_one", started, success=True)
        return int(result.deleted_count)

    async def count(
        self,
        collection: str,
        query: dict[str, Any] | None = None,
    ) -> int:
        """Return document count for a collection, optionally filtered by query."""
        started = perf_counter()
        try:
            filter_query = query or {}
            result = await self.collection(collection).count_documents(filter_query)
        except Exception as exc:
            self._observe_error("count", started, exc)
            raise

        self._observe_operation("count", started, success=True)
        return int(result)

    async def health_check(self) -> HealthStatus:
        """Verify MongoDB liveness with a ping command."""
        start = perf_counter()
        try:
            await self.ping()
            latency_ms = (perf_counter() - start) * 1000
            return HealthStatus(
                healthy=True,
                latency_ms=latency_ms,
                message="ok",
                details={"database": self.database_name},
            )
        except Exception as exc:
            latency_ms = (perf_counter() - start) * 1000
            return HealthStatus(
                healthy=False,
                latency_ms=latency_ms,
                message=str(exc),
                details={"error_type": exc.__class__.__name__, "database": self.database_name},
            )

    async def close(self) -> None:
        """Close MongoDB client."""
        started = perf_counter()
        try:
            self._client.close()
        except Exception as exc:
            self._observe_error("close", started, exc)
            raise
        finally:
            self._closed = True

        self._observe_operation("close", started, success=True)


async def create_mongodb_resource(settings: MongoDbSettings) -> MongoDbResource:
    """Factory used by ResourceManager bootstrap."""
    return await MongoDbResource.create(settings)
