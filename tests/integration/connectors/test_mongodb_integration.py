"""End-to-end integration tests for the MongoDB resource provider."""

from __future__ import annotations

import pytest

from orchid_commons.db import create_mongodb_resource

pytestmark = pytest.mark.integration


async def test_mongodb_crud_roundtrip(mongodb_settings) -> None:
    resource = await create_mongodb_resource(mongodb_settings)
    collection = "integration_test"
    try:
        # Clean up from previous runs
        await resource.collection(collection).drop()

        # Insert
        doc_id = await resource.insert_one(collection, {"name": "orchid", "version": 1})
        assert doc_id is not None

        # Find one
        doc = await resource.find_one(collection, {"name": "orchid"})
        assert doc is not None
        assert doc["name"] == "orchid"

        # Update
        modified = await resource.update_one(
            collection, {"name": "orchid"}, {"$set": {"version": 2}}
        )
        assert modified == 1

        updated = await resource.find_one(collection, {"name": "orchid"})
        assert updated is not None
        assert updated["version"] == 2

        # Find many
        await resource.insert_one(collection, {"name": "romy", "version": 1})
        docs = await resource.find_many(collection, {}, sort=[("name", 1)])
        assert len(docs) == 2
        assert docs[0]["name"] == "orchid"
        assert docs[1]["name"] == "romy"

        # Count
        count = await resource.count(collection)
        assert count == 2

        # Delete
        deleted = await resource.delete_one(collection, {"name": "romy"})
        assert deleted == 1

        # Health check
        assert (await resource.health_check()).healthy is True
    finally:
        await resource.close()
