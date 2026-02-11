"""Tests for Qdrant vector store provider."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

import orchid_commons.db.qdrant as qdrant_module
from orchid_commons.config.resources import QdrantSettings
from orchid_commons.db.qdrant import QdrantVectorStore, create_qdrant_vector_store
from orchid_commons.db.vector import (
    VectorOperationError,
    VectorPoint,
    VectorSearchResult,
    VectorTransientError,
    VectorValidationError,
)


class FakeQdrantError(Exception):
    def __init__(self, message: str, *, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(message)


@dataclass(slots=True)
class FakeScoredPoint:
    id: int
    score: float
    payload: dict[str, Any] | None = None
    vector: list[float] | None = None


@dataclass(slots=True)
class FakeCountResult:
    count: int


@dataclass(slots=True)
class FakeQueryResponse:
    points: list[FakeScoredPoint]


@dataclass(slots=True)
class FakeLegacySearchResponse:
    result: list[FakeScoredPoint]


class FakeQdrantAsyncClient:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.collections_created: list[tuple[str, Any]] = []
        self.upsert_calls: list[tuple[str, list[Any]]] = []
        self.search_calls: list[dict[str, Any]] = []
        self.delete_calls: list[tuple[str, Any]] = []
        self.count_calls: list[dict[str, Any]] = []
        self.closed = False
        self.fail_health = False
        self.fail_search: Exception | None = None
        self.count_responses: list[int] = []
        self.point_count = 0

    async def create_collection(self, *, collection_name: str, vectors_config: Any) -> None:
        self.collections_created.append((collection_name, vectors_config))

    async def upsert(self, *, collection_name: str, points: list[Any]) -> None:
        self.upsert_calls.append((collection_name, points))
        self.point_count += len(points)

    async def search(self, **kwargs: Any) -> list[FakeScoredPoint]:
        self.search_calls.append(kwargs)
        if self.fail_search is not None:
            raise self.fail_search
        return [FakeScoredPoint(id=1, score=0.99, payload={"doc": "x"}, vector=[0.1, 0.2])]

    async def delete(self, *, collection_name: str, points_selector: Any) -> None:
        self.delete_calls.append((collection_name, points_selector))
        ids = getattr(points_selector, "points", None)
        if isinstance(ids, list):
            self.point_count = max(0, self.point_count - len(ids))

    async def count(self, **kwargs: Any) -> FakeCountResult:
        self.count_calls.append(kwargs)
        if self.count_responses:
            return FakeCountResult(count=self.count_responses.pop(0))
        return FakeCountResult(count=self.point_count)

    async def get_collections(self) -> dict[str, list[Any]]:
        if self.fail_health:
            raise RuntimeError("qdrant unavailable")
        return {"collections": []}

    async def close(self) -> None:
        self.closed = True


class FakeDistance:
    COSINE = "cosine"
    DOT = "dot"
    EUCLID = "euclid"
    MANHATTAN = "manhattan"


@dataclass(slots=True)
class FakeVectorParams:
    size: int
    distance: str


@dataclass(slots=True)
class FakePointStruct:
    id: int | str
    vector: list[float]
    payload: dict[str, Any] | None = None


@dataclass(slots=True)
class FakePointIdsList:
    points: list[int | str]


@dataclass(slots=True)
class FakeRange:
    gte: float | int | None = None
    gt: float | int | None = None
    lte: float | int | None = None
    lt: float | int | None = None


@dataclass(slots=True)
class FakeMatchAny:
    any: list[Any]


@dataclass(slots=True)
class FakeMatchValue:
    value: Any


@dataclass(slots=True)
class FakeFieldCondition:
    key: str
    range: FakeRange | None = None
    match: FakeMatchAny | FakeMatchValue | None = None


@dataclass(slots=True)
class FakeFilter:
    must: list[FakeFieldCondition] = field(default_factory=list)


@dataclass(slots=True)
class FakeFilterSelector:
    filter: FakeFilter


@dataclass(slots=True)
class FakeSearchRequest:
    vector: list[float]
    filter: FakeFilter | None = None
    limit: int = 10
    with_payload: bool = True
    with_vector: bool = False
    score_threshold: float | None = None


class FakeQdrantModels:
    Distance = FakeDistance
    VectorParams = FakeVectorParams
    PointStruct = FakePointStruct
    PointIdsList = FakePointIdsList
    Range = FakeRange
    MatchAny = FakeMatchAny
    MatchValue = FakeMatchValue
    FieldCondition = FakeFieldCondition
    Filter = FakeFilter
    FilterSelector = FakeFilterSelector
    SearchRequest = FakeSearchRequest


class FakeQdrantAsyncClientFactory:
    def __init__(self) -> None:
        self.instances: list[FakeQdrantAsyncClient] = []

    def __call__(self, **kwargs: Any) -> FakeQdrantAsyncClient:
        instance = FakeQdrantAsyncClient(**kwargs)
        self.instances.append(instance)
        return instance


class TestQdrantVectorStore:
    async def test_factory_and_vector_operations(self, monkeypatch: pytest.MonkeyPatch) -> None:
        factory = FakeQdrantAsyncClientFactory()
        monkeypatch.setattr(qdrant_module, "_import_qdrant_async_client", lambda: factory)
        monkeypatch.setattr(qdrant_module, "_import_qdrant_models", lambda: FakeQdrantModels)

        store = await create_qdrant_vector_store(
            QdrantSettings(
                host="qdrant.local",
                port=6333,
                collection_prefix="orchid",
            )
        )

        client = factory.instances[0]

        await store.create_collection("embeddings", vector_size=3, distance="cosine")
        affected = await store.upsert(
            "embeddings",
            [
                VectorPoint(
                    id=1,
                    vector=[0.1, 0.2, 0.3],
                    payload={"kind": "doc"},
                )
            ],
        )
        results = await store.search("embeddings", [0.1, 0.2, 0.3], limit=5)
        total = await store.count("embeddings")
        removed = await store.delete_ids("embeddings", [1])

        assert affected == 1
        assert total == 1
        assert removed == 1
        assert store.scoped_collection("embeddings") == "orchid_embeddings"
        assert client.collections_created[0][0] == "orchid_embeddings"
        assert client.upsert_calls[0][0] == "orchid_embeddings"
        assert results == [
            VectorSearchResult(
                id=1,
                score=0.99,
                payload={"doc": "x"},
                vector=[0.1, 0.2],
            )
        ]
        assert client.delete_calls[0][0] == "orchid_embeddings"

        health = await store.health_check()
        assert health.healthy is True

        await store.close()
        assert client.closed is True
        assert store.is_connected is False

    async def test_delete_by_filter_uses_count_delta(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(qdrant_module, "_import_qdrant_models", lambda: FakeQdrantModels)
        client = FakeQdrantAsyncClient(host="qdrant.local")
        client.count_responses = [5, 2]
        store = QdrantVectorStore(_client=client)

        deleted = await store.delete_by_filter("embeddings", {"video_id": "abc"})

        assert deleted == 3
        assert client.delete_calls[0][0] == "embeddings"
        assert isinstance(client.delete_calls[0][1], FakeFilterSelector)

    async def test_search_error_is_translated_to_typed_exception(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(qdrant_module, "_import_qdrant_models", lambda: FakeQdrantModels)
        client = FakeQdrantAsyncClient(host="qdrant.local")
        client.fail_search = FakeQdrantError("gateway timeout", status_code=503)
        store = QdrantVectorStore(_client=client)

        with pytest.raises(VectorTransientError):
            await store.search("embeddings", [0.1, 0.2, 0.3], limit=3)

    async def test_search_supports_query_points_api(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(qdrant_module, "_import_qdrant_models", lambda: FakeQdrantModels)
        client = FakeQdrantAsyncClient(host="qdrant.local")
        client.search = None  # type: ignore[assignment]

        async def query_points(**kwargs: Any) -> FakeQueryResponse:
            client.search_calls.append(kwargs)
            return FakeQueryResponse(
                points=[
                    FakeScoredPoint(id=42, score=0.77, payload={"source": "qp"}, vector=[1.0, 2.0])
                ]
            )

        client.query_points = query_points  # type: ignore[attr-defined]
        store = QdrantVectorStore(_client=client)

        results = await store.search("embeddings", [0.1, 0.2, 0.3], limit=3)

        assert results == [
            VectorSearchResult(
                id=42,
                score=0.77,
                payload={"source": "qp"},
                vector=[1.0, 2.0],
            )
        ]
        assert len(client.search_calls) == 1
        assert client.search_calls[0]["query"] == [0.1, 0.2, 0.3]

    async def test_search_falls_back_to_legacy_http_search(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(qdrant_module, "_import_qdrant_models", lambda: FakeQdrantModels)
        client = FakeQdrantAsyncClient(host="qdrant.local")
        client.search = None  # type: ignore[assignment]

        async def query_points(**kwargs: Any) -> FakeQueryResponse:
            del kwargs
            raise FakeQdrantError("not found endpoint", status_code=404)

        async def search_points(**kwargs: Any) -> FakeLegacySearchResponse:
            client.search_calls.append(kwargs)
            return FakeLegacySearchResponse(
                result=[
                    FakeScoredPoint(
                        id=7, score=0.91, payload={"source": "legacy"}, vector=[0.7, 0.8]
                    )
                ]
            )

        client.query_points = query_points  # type: ignore[attr-defined]
        client.http = SimpleNamespace(  # type: ignore[attr-defined]
            search_api=SimpleNamespace(search_points=search_points)
        )
        store = QdrantVectorStore(_client=client)

        results = await store.search("embeddings", [0.1, 0.2, 0.3], limit=3)

        assert results == [
            VectorSearchResult(
                id=7,
                score=0.91,
                payload={"source": "legacy"},
                vector=[0.7, 0.8],
            )
        ]
        assert len(client.search_calls) == 1

    async def test_factory_raises_if_health_check_fails(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def failing_factory(**kwargs: Any) -> FakeQdrantAsyncClient:
            client = FakeQdrantAsyncClient(**kwargs)
            client.fail_health = True
            return client

        monkeypatch.setattr(qdrant_module, "_import_qdrant_async_client", lambda: failing_factory)
        monkeypatch.setattr(qdrant_module, "_import_qdrant_models", lambda: FakeQdrantModels)

        with pytest.raises(VectorOperationError):
            settings = QdrantSettings(host="qdrant.local")
            await create_qdrant_vector_store(settings)

    async def test_validation_errors(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(qdrant_module, "_import_qdrant_models", lambda: FakeQdrantModels)
        store = QdrantVectorStore(_client=FakeQdrantAsyncClient(host="qdrant.local"))

        with pytest.raises(VectorValidationError):
            await store.search("embeddings", [], limit=3)

        with pytest.raises(VectorValidationError):
            await store.delete("embeddings", ids=[1], filters={"kind": "doc"})

    async def test_health_check_unhealthy(self) -> None:
        client = FakeQdrantAsyncClient(host="qdrant.local")
        client.fail_health = True
        store = QdrantVectorStore(_client=client)

        status = await store.health_check()

        assert status.healthy is False
        assert status.details == {"error_type": "RuntimeError"}
