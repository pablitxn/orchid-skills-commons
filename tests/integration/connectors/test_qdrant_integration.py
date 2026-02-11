"""End-to-end integration tests for Qdrant vector provider."""

from __future__ import annotations

from uuid import uuid4

import pytest

from orchid_commons import ResourceManager
from orchid_commons.config.resources import ResourceSettings
from orchid_commons.db import QdrantVectorStore, VectorPoint, create_qdrant_vector_store

pytestmark = pytest.mark.integration


async def test_qdrant_provider_roundtrip(qdrant_settings) -> None:
    store = await create_qdrant_vector_store(qdrant_settings)
    collection_name = f"embeddings_{uuid4().hex[:8]}"

    try:
        await store.create_collection(collection_name, vector_size=3, distance="cosine")

        upserted = await store.upsert(
            collection_name,
            [
                VectorPoint(id=1, vector=[0.1, 0.2, 0.3], payload={"video_id": "v1"}),
                VectorPoint(id=2, vector=[0.3, 0.2, 0.1], payload={"video_id": "v2"}),
            ],
        )
        assert upserted == 2
        assert await store.count(collection_name) == 2

        results = await store.search(collection_name, [0.1, 0.2, 0.3], limit=2)
        assert len(results) >= 1
        assert str(results[0].id) in {"1", "2"}

        removed = await store.delete(collection_name, ids=[1])
        assert removed == 1
        assert await store.count(collection_name) == 1
        assert (await store.health_check()).healthy is True
    finally:
        await store.close()


async def test_qdrant_resource_manager_bootstrap(qdrant_settings) -> None:
    manager = ResourceManager()
    settings = ResourceSettings(qdrant=qdrant_settings)

    await manager.startup(settings, required=["qdrant"])
    store = manager.get("qdrant")
    assert isinstance(store, QdrantVectorStore)

    report = await manager.health_report(timeout_seconds=5.0)
    assert "qdrant" in report.checks
    assert report.checks["qdrant"].healthy is True
    await manager.close_all()
