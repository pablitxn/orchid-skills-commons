"""Tests for MongoDB resource provider."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

import orchid_commons.db.mongodb as mongodb_module
from orchid_commons.config.resources import MongoDbSettings
from orchid_commons.db.mongodb import MongoDbResource, create_mongodb_resource


def _matches(document: dict[str, Any], query: dict[str, Any]) -> bool:
    return all(document.get(key) == value for key, value in query.items())


@dataclass(slots=True)
class FakeInsertResult:
    inserted_id: Any


@dataclass(slots=True)
class FakeUpdateResult:
    modified_count: int


@dataclass(slots=True)
class FakeDeleteResult:
    deleted_count: int


class FakeCursor:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self._documents = documents
        self._sort: list[tuple[str, int]] = []
        self._limit: int | None = None

    def sort(self, sort: list[tuple[str, int]]) -> FakeCursor:
        self._sort = list(sort)
        return self

    def limit(self, limit: int) -> FakeCursor:
        self._limit = limit
        return self

    async def to_list(self, length: int) -> list[dict[str, Any]]:
        documents = list(self._documents)
        for key, direction in reversed(self._sort):
            documents.sort(key=lambda row: row.get(key), reverse=direction < 0)

        if self._limit is not None:
            documents = documents[: self._limit]
        return documents[:length]


class FakeCollection:
    def __init__(self) -> None:
        self.documents: list[dict[str, Any]] = []

    async def insert_one(self, document: dict[str, Any]) -> FakeInsertResult:
        next_id = len(self.documents) + 1
        stored = {"_id": next_id, **document}
        self.documents.append(stored)
        return FakeInsertResult(inserted_id=next_id)

    async def find_one(
        self,
        query: dict[str, Any],
        projection: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        del projection
        for document in self.documents:
            if _matches(document, query):
                return dict(document)
        return None

    def find(
        self,
        query: dict[str, Any],
        projection: dict[str, Any] | None = None,
    ) -> FakeCursor:
        del projection
        matches = [dict(document) for document in self.documents if _matches(document, query)]
        return FakeCursor(matches)

    async def update_one(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
        *,
        upsert: bool = False,
    ) -> FakeUpdateResult:
        for index, document in enumerate(self.documents):
            if not _matches(document, query):
                continue

            replacement = dict(document)
            if "$set" in update and isinstance(update["$set"], dict):
                replacement.update(update["$set"])
            self.documents[index] = replacement
            return FakeUpdateResult(modified_count=1)

        if upsert and "$set" in update and isinstance(update["$set"], dict):
            inserted = dict(query)
            inserted.update(update["$set"])
            await self.insert_one(inserted)
            return FakeUpdateResult(modified_count=1)

        return FakeUpdateResult(modified_count=0)

    async def delete_one(self, query: dict[str, Any]) -> FakeDeleteResult:
        for index, document in enumerate(self.documents):
            if _matches(document, query):
                self.documents.pop(index)
                return FakeDeleteResult(deleted_count=1)
        return FakeDeleteResult(deleted_count=0)

    async def count_documents(self, query: dict[str, Any]) -> int:
        return sum(1 for doc in self.documents if _matches(doc, query))


class FakeDatabase:
    def __init__(self) -> None:
        self.collections: dict[str, FakeCollection] = {}
        self.command_error: Exception | None = None

    def __getitem__(self, name: str) -> FakeCollection:
        self.collections.setdefault(name, FakeCollection())
        return self.collections[name]

    async def command(self, command_name: str) -> dict[str, float]:
        assert command_name == "ping"
        if self.command_error is not None:
            raise self.command_error
        return {"ok": 1.0}


class FakeMongoClient:
    def __init__(self, database: FakeDatabase) -> None:
        self._database = database
        self.closed = False

    def __getitem__(self, name: str) -> FakeDatabase:
        del name
        return self._database

    def close(self) -> None:
        self.closed = True


class FakeMotorAsyncioModule:
    def __init__(self, client: FakeMongoClient) -> None:
        self._client = client
        self.calls: list[dict[str, Any]] = []

    def AsyncIOMotorClient(self, uri: str, **kwargs: Any) -> FakeMongoClient:
        self.calls.append({"uri": uri, **kwargs})
        return self._client


class TestMongoDbResource:
    async def test_factory_and_crud_helpers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        database = FakeDatabase()
        client = FakeMongoClient(database)
        fake_motor = FakeMotorAsyncioModule(client)
        monkeypatch.setattr(mongodb_module, "_import_motor_asyncio", lambda: fake_motor)

        resource = await create_mongodb_resource(
            MongoDbSettings(
                uri="mongodb://localhost:27017",
                database="orchid",
                app_name="orchid-tests",
            )
        )

        inserted = await resource.insert_one("skills", {"name": "romy", "kind": "bot"})
        await resource.insert_one("skills", {"name": "orchid", "kind": "project"})
        one = await resource.find_one("skills", {"name": "romy"})
        many = await resource.find_many(
            "skills",
            {"kind": "bot"},
            sort=[("name", 1)],
            limit=10,
        )
        updated = await resource.update_one(
            "skills",
            {"name": "romy"},
            {"$set": {"kind": "assistant"}},
        )
        deleted = await resource.delete_one("skills", {"name": "orchid"})

        assert inserted == 1
        assert one is not None
        assert one["name"] == "romy"
        assert many == [{"_id": 1, "name": "romy", "kind": "bot"}]
        assert updated == 1
        assert deleted == 1

        status = await resource.health_check()
        assert status.healthy is True

        await resource.close()

        assert client.closed is True
        assert resource.is_connected is False
        assert fake_motor.calls[0]["uri"] == "mongodb://localhost:27017"
        assert fake_motor.calls[0]["appname"] == "orchid-tests"

    async def test_create_translates_startup_ping_error_and_closes_client(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        database = FakeDatabase()
        database.command_error = ConnectionError("temporary network issue")
        client = FakeMongoClient(database)
        fake_motor = FakeMotorAsyncioModule(client)
        monkeypatch.setattr(mongodb_module, "_import_motor_asyncio", lambda: fake_motor)

        with pytest.raises(mongodb_module.DocumentTransientError, match="ping"):
            await create_mongodb_resource(
                MongoDbSettings(
                    uri="mongodb://localhost:27017",
                    database="orchid",
                    app_name="orchid-tests",
                )
            )

        assert client.closed is True

    async def test_ping_translates_connection_error(self) -> None:
        database = FakeDatabase()
        database.command_error = ConnectionError("mongo unavailable")
        resource = MongoDbResource(
            _client=FakeMongoClient(database),
            _database=database,
            database_name="orchid",
        )

        with pytest.raises(mongodb_module.DocumentTransientError, match="ping"):
            await resource.ping()

    async def test_health_check_unhealthy(self) -> None:
        database = FakeDatabase()
        database.command_error = RuntimeError("mongo unavailable")
        resource = MongoDbResource(
            _client=FakeMongoClient(database),
            _database=database,
            database_name="orchid",
        )

        status = await resource.health_check()

        assert status.healthy is False
        assert status.details == {"error_type": "DocumentOperationError", "database": "orchid"}

    async def test_count(self) -> None:
        database = FakeDatabase()
        resource = MongoDbResource(
            _client=FakeMongoClient(database),
            _database=database,
            database_name="orchid",
        )

        await resource.insert_one("skills", {"name": "a", "kind": "bot"})
        await resource.insert_one("skills", {"name": "b", "kind": "bot"})
        await resource.insert_one("skills", {"name": "c", "kind": "project"})

        total = await resource.count("skills")
        bots = await resource.count("skills", {"kind": "bot"})
        empty = await resource.count("empty_collection")

        assert total == 3
        assert bots == 2
        assert empty == 0

    async def test_find_many_limit_none_returns_results(self) -> None:
        database = FakeDatabase()
        resource = MongoDbResource(
            _client=FakeMongoClient(database),
            _database=database,
            database_name="orchid",
        )
        await resource.insert_one("items", {"v": 1})
        await resource.insert_one("items", {"v": 2})
        await resource.insert_one("items", {"v": 3})

        results = await resource.find_many("items", {})
        assert len(results) == 3

    async def test_find_many_limit_positive_caps_results(self) -> None:
        database = FakeDatabase()
        resource = MongoDbResource(
            _client=FakeMongoClient(database),
            _database=database,
            database_name="orchid",
        )
        for i in range(10):
            await resource.insert_one("items", {"v": i})

        results = await resource.find_many("items", {}, limit=5)
        assert len(results) == 5

    async def test_find_many_limit_zero_raises(self) -> None:
        database = FakeDatabase()
        resource = MongoDbResource(
            _client=FakeMongoClient(database),
            _database=database,
            database_name="orchid",
        )

        with pytest.raises(ValueError, match="limit must be a positive integer or None"):
            await resource.find_many("items", {}, limit=0)

    async def test_find_many_limit_negative_raises(self) -> None:
        database = FakeDatabase()
        resource = MongoDbResource(
            _client=FakeMongoClient(database),
            _database=database,
            database_name="orchid",
        )

        with pytest.raises(ValueError, match="limit must be a positive integer or None"):
            await resource.find_many("items", {}, limit=-1)

    def test_implements_document_store_protocol(self) -> None:
        from orchid_commons.db.document import DocumentStore

        database = FakeDatabase()
        resource = MongoDbResource(
            _client=FakeMongoClient(database),
            _database=database,
            database_name="orchid",
        )

        assert isinstance(resource, DocumentStore)
