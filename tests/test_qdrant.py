"""Tests for Qdrant vector store provider."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

import orchid_commons.db.qdrant as qdrant_module
from orchid_commons.config.resources import QdrantSettings
from orchid_commons.db.qdrant import QdrantVectorStore, create_qdrant_vector_store


@dataclass(slots=True)
class FakeScoredPoint:
    id: int
    score: float
    payload: dict[str, Any] | None = None
    vector: list[float] | None = None


class FakeQdrantAsyncClient:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.collections_created: list[tuple[str, Any]] = []
        self.upsert_calls: list[tuple[str, list[Any]]] = []
        self.search_calls: list[dict[str, Any]] = []
        self.delete_calls: list[tuple[str, Any]] = []
        self.closed = False
        self.fail_health = False

    async def create_collection(self, *, collection_name: str, vectors_config: Any) -> None:
        self.collections_created.append((collection_name, vectors_config))

    async def upsert(self, *, collection_name: str, points: list[Any]) -> None:
        self.upsert_calls.append((collection_name, points))

    async def search(self, **kwargs: Any) -> list[FakeScoredPoint]:
        self.search_calls.append(kwargs)
        return [FakeScoredPoint(id=1, score=0.99, payload={"doc": "x"}, vector=[0.1, 0.2])]

    async def delete(self, *, collection_name: str, points_selector: Any) -> None:
        self.delete_calls.append((collection_name, points_selector))

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


class FakeQdrantModels:
    Distance = FakeDistance
    VectorParams = FakeVectorParams
    PointStruct = FakePointStruct
    PointIdsList = FakePointIdsList


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
        await store.upsert(
            "embeddings",
            [
                {
                    "id": 1,
                    "vector": [0.1, 0.2, 0.3],
                    "payload": {"kind": "doc"},
                }
            ],
        )
        results = await store.search("embeddings", [0.1, 0.2, 0.3], limit=5)
        await store.delete_ids("embeddings", [1])

        assert store.scoped_collection("embeddings") == "orchid_embeddings"
        assert client.collections_created[0][0] == "orchid_embeddings"
        assert client.upsert_calls[0][0] == "orchid_embeddings"
        assert results == [
            {
                "id": 1,
                "score": 0.99,
                "payload": {"doc": "x"},
                "vector": [0.1, 0.2],
            }
        ]
        assert client.delete_calls[0][0] == "orchid_embeddings"

        health = await store.health_check()
        assert health.healthy is True

        await store.close()
        assert client.closed is True
        assert store.is_connected is False

    async def test_health_check_unhealthy(self) -> None:
        client = FakeQdrantAsyncClient(host="qdrant.local")
        client.fail_health = True
        store = QdrantVectorStore(_client=client)

        status = await store.health_check()

        assert status.healthy is False
        assert status.details == {"error_type": "RuntimeError"}
