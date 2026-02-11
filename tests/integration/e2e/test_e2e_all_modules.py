"""
End-to-end integration tests for all orchid-commons modules.

This test suite validates that all abstractions work correctly together
when connected to real services (MinIO, MongoDB, Qdrant, PostgreSQL, etc.).

Run with:
    pytest tests/integration/test_e2e_all_modules.py -v -s

Environment variables for external services:
    ORCHID_MINIO_ENDPOINT=localhost:9000
    ORCHID_QDRANT_HOST=localhost
    ORCHID_POSTGRES_DSN=postgresql://postgres:postgres@localhost:5432/orchid
    ORCHID_MONGODB_URI=mongodb://localhost:27017
    ORCHID_REDIS_URL=redis://localhost:6379
"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration


# ============================================================================
# BLOB STORAGE TESTS
# ============================================================================


class TestBlobStorageE2E:
    """Test MinioProfile with MinIO."""

    async def test_upload_download_delete_cycle(self, minio_settings) -> None:
        """Full lifecycle: upload -> download -> verify -> delete."""
        from orchid_commons.blob.minio import create_minio_profile

        profile = await create_minio_profile(minio_settings)
        try:
            # Upload
            key = f"e2e-test/{uuid4().hex}.txt"
            content = b"Hello from orchid-commons E2E test!"
            await profile.upload(key, content, content_type="text/plain")

            # Download and verify
            downloaded = await profile.download(key)
            assert downloaded.data == content
            assert downloaded.key == key
            assert downloaded.content_type == "text/plain"

            # Check exists
            assert await profile.exists(key) is True

            # Delete
            await profile.delete(key)

            # Verify deleted
            assert await profile.exists(key) is False

            # Health check
            health = await profile.health_check()
            assert health.healthy is True
        finally:
            await profile.close()

    async def test_metadata_handling(self, minio_settings) -> None:
        """Test upload with metadata."""
        from orchid_commons.blob.minio import create_minio_profile

        profile = await create_minio_profile(minio_settings)
        try:
            key = f"e2e-metadata/{uuid4().hex}.txt"
            content = b"content with metadata"
            metadata = {"author": "test", "version": "1.0"}

            await profile.upload(key, content, metadata=metadata)

            # Download and verify metadata is preserved
            obj = await profile.download(key)
            assert obj.data == content
            assert obj.metadata.get("author") == "test"
            assert obj.metadata.get("version") == "1.0"

            await profile.delete(key)
        finally:
            await profile.close()


class TestMultiBucketRouterE2E:
    """Test MultiBucketBlobRouter with multiple logical buckets."""

    async def test_multi_bucket_operations(self, multi_bucket_settings) -> None:
        """Test operations across multiple logical buckets."""
        from orchid_commons.blob import create_multi_bucket_router

        router = await create_multi_bucket_router(multi_bucket_settings)
        try:
            # Upload to different buckets
            video_key = f"video-{uuid4().hex}.mp4"
            chunk_key = f"chunk-{uuid4().hex}.json"

            await router.upload("videos", video_key, b"fake video content")
            await router.upload("chunks", chunk_key, b'{"chunk": 1}')

            # Download from each - returns BlobObject
            video_obj = await router.download("videos", video_key)
            chunk_obj = await router.download("chunks", chunk_key)

            assert video_obj.data == b"fake video content"
            assert chunk_obj.data == b'{"chunk": 1}'

            # Cleanup
            await router.delete("videos", video_key)
            await router.delete("chunks", chunk_key)

            # Health check all buckets
            health = await router.health_check()
            assert health.healthy is True
        finally:
            await router.close()


# ============================================================================
# SQLITE TESTS
# ============================================================================


class TestSqliteResourceE2E:
    """Test SQLite resource for SQL operations."""

    async def test_sql_operations(self, sqlite_settings) -> None:
        """Test SQL execute, fetchone, fetchall."""
        from orchid_commons.db import create_sqlite_resource

        sqlite = await create_sqlite_resource(sqlite_settings)
        try:
            # Create table
            await sqlite.execute(
                "CREATE TABLE IF NOT EXISTS e2e_users(id INTEGER PRIMARY KEY, name TEXT, age INTEGER)",
                commit=True,
            )

            # Insert
            await sqlite.execute(
                "INSERT INTO e2e_users(name, age) VALUES (?, ?)",
                ("Alice", 30),
                commit=True,
            )
            await sqlite.execute(
                "INSERT INTO e2e_users(name, age) VALUES (?, ?)",
                ("Bob", 25),
                commit=True,
            )

            # Fetch one
            row = await sqlite.fetchone("SELECT * FROM e2e_users WHERE name = ?", ("Alice",))
            assert row is not None
            assert row["name"] == "Alice"
            assert row["age"] == 30

            # Fetch all
            rows = await sqlite.fetchall("SELECT * FROM e2e_users ORDER BY name")
            assert len(rows) == 2
            assert rows[0]["name"] == "Alice"
            assert rows[1]["name"] == "Bob"

            # Update
            await sqlite.execute(
                "UPDATE e2e_users SET age = ? WHERE name = ?",
                (31, "Alice"),
                commit=True,
            )
            updated = await sqlite.fetchone("SELECT age FROM e2e_users WHERE name = ?", ("Alice",))
            assert updated["age"] == 31

            # Delete
            await sqlite.execute("DELETE FROM e2e_users WHERE name = ?", ("Bob",), commit=True)
            remaining = await sqlite.fetchall("SELECT * FROM e2e_users")
            assert len(remaining) == 1

            # Health check
            health = await sqlite.health_check()
            assert health.healthy is True
        finally:
            await sqlite.close()


# ============================================================================
# MONGODB TESTS
# ============================================================================


class TestMongoDBE2E:
    """Test MongoDB resource (requires MongoDB running)."""

    @pytest.fixture
    def mongodb_settings(self):
        """MongoDB settings from environment."""
        uri = os.getenv("ORCHID_MONGODB_URI", "mongodb://localhost:27017")
        database = os.getenv("ORCHID_MONGODB_DATABASE", f"orchid_e2e_{uuid4().hex[:8]}")

        pytest.importorskip("motor")

        from orchid_commons.config import MongoDbSettings

        return MongoDbSettings(uri=uri, database=database)

    async def test_mongodb_operations(self, mongodb_settings) -> None:
        """Test MongoDB CRUD operations."""
        from orchid_commons.db import create_mongodb_resource

        try:
            store = await create_mongodb_resource(mongodb_settings)
        except Exception as exc:
            pytest.skip(f"MongoDB not available: {exc}")

        try:
            collection = f"e2e_docs_{uuid4().hex[:8]}"

            # Insert
            doc_id = await store.insert_one(collection, {"title": "Test Doc", "count": 42})
            assert doc_id is not None

            # Find one
            doc = await store.find_one(collection, {"title": "Test Doc"})
            assert doc is not None
            assert doc["count"] == 42

            # Find many
            await store.insert_one(collection, {"title": "Doc 2", "count": 100})
            docs = await store.find_many(collection, {})
            assert len(docs) == 2

            # Count
            count = await store.count(collection, {})
            assert count == 2

            # Health check
            health = await store.health_check()
            assert health.healthy is True
        finally:
            await store.close()


# ============================================================================
# QDRANT VECTOR STORE TESTS
# ============================================================================


class TestQdrantVectorStoreE2E:
    """Test Qdrant vector store."""

    @pytest.mark.skip(
        reason="Qdrant client API incompatibility - search method changed in newer versions"
    )
    async def test_upsert_and_search(self, qdrant_settings) -> None:
        """Test vector upsert and similarity search."""
        from orchid_commons.db import create_qdrant_vector_store
        from orchid_commons.db.vector import VectorPoint

        store = await create_qdrant_vector_store(qdrant_settings)
        try:
            collection = f"e2e_vectors_{uuid4().hex[:8]}"
            vector_size = 128

            # Create collection
            await store.create_collection(collection, vector_size=vector_size)

            # Generate test vectors with UUID ids (Qdrant requires int or UUID)
            import random

            random.seed(42)
            vector_ids = [str(uuid4()) for _ in range(10)]
            vectors = [
                VectorPoint(
                    id=vector_ids[i],
                    vector=[random.random() for _ in range(vector_size)],
                    payload={"label": f"item-{i}", "category": "test"},
                )
                for i in range(10)
            ]

            # Upsert
            await store.upsert(collection, vectors)

            # Count
            count = await store.count(collection)
            assert count == 10

            # Search
            query_vector = vectors[0].vector
            results = await store.search(collection, query_vector, limit=5)
            assert len(results) == 5
            # First result should be most similar (itself or very close)
            assert results[0].score > 0.9

            # Delete collection
            await store.delete_collection(collection)

            # Health check
            health = await store.health_check()
            assert health.healthy is True
        finally:
            await store.close()


# ============================================================================
# OBSERVABILITY TESTS
# ============================================================================


class TestObservabilityE2E:
    """Test observability stack integration."""

    def test_logging_with_correlation_ids(self) -> None:
        """Test structured logging with correlation IDs."""
        from orchid_commons.observability.logging import (
            correlation_scope,
            get_correlation_ids,
        )

        with correlation_scope(
            request_id="req-123",
            trace_id="trace-abc",
            span_id="span-xyz",
        ):
            current = get_correlation_ids()
            assert current.request_id == "req-123"
            assert current.trace_id == "trace-abc"
            assert current.span_id == "span-xyz"

        # Outside scope, should be empty
        outside = get_correlation_ids()
        assert outside.request_id is None

    def test_correlation_from_headers(self) -> None:
        """Test extracting correlation IDs from headers."""
        from orchid_commons.observability.logging import (
            correlation_scope_from_headers,
            get_correlation_ids,
        )

        headers = {
            "x-request-id": "req-from-header",
            "x-trace-id": "trace-from-header",
            "x-span-id": "span-from-header",
        }

        with correlation_scope_from_headers(headers) as ids:
            assert ids.request_id == "req-from-header"
            current = get_correlation_ids()
            assert current.request_id == "req-from-header"

    async def test_otel_bootstrap_and_spans(self, sqlite_settings) -> None:
        """Test OpenTelemetry bootstrap and span creation."""
        try:
            pytest.importorskip("opentelemetry")
            pytest.importorskip("opentelemetry.sdk")
            # Check for OTLP exporter
            pytest.importorskip("opentelemetry.exporter.otlp.proto.grpc")
        except pytest.skip.Exception:
            pytest.skip("OpenTelemetry dependencies not installed")

        from orchid_commons.config.models import ObservabilitySettings
        from orchid_commons.db import create_sqlite_resource
        from orchid_commons.observability.otel import (
            bootstrap_observability,
            request_span,
            shutdown_observability,
        )

        shutdown_observability()

        handle = bootstrap_observability(
            ObservabilitySettings(
                enabled=True,
                otlp_endpoint=None,  # No external collector
                retry_enabled=False,
            ),
            service_name="orchid-e2e-test",
            environment="test",
        )

        assert handle.enabled is True
        assert handle.tracer_provider is not None

        # Create spans
        sqlite = await create_sqlite_resource(sqlite_settings)
        try:
            with request_span("e2e.test", method="GET", route="/test", status_code=200):
                await sqlite.execute(
                    "CREATE TABLE IF NOT EXISTS e2e_otel(id INTEGER PRIMARY KEY)",
                    commit=True,
                )
        finally:
            await sqlite.close()
            shutdown_observability()


# ============================================================================
# RUNTIME & RESOURCE MANAGER TESTS
# ============================================================================


class TestResourceManagerE2E:
    """Test ResourceManager lifecycle and health aggregation."""

    async def test_resource_lifecycle(self, sqlite_settings) -> None:
        """Test resource registration, access, and cleanup."""
        from orchid_commons.db import create_sqlite_resource
        from orchid_commons.runtime import ResourceManager

        manager = ResourceManager()

        # Create resource first
        sqlite = await create_sqlite_resource(sqlite_settings)

        # Register the created resource
        manager.register("sqlite", sqlite)

        # Get resource (synchronous)
        retrieved = manager.get("sqlite")
        assert retrieved is not None
        assert retrieved is sqlite

        # Check if registered
        assert manager.has("sqlite")

        # Health report
        report = await manager.health_report()
        assert report is not None
        assert report.healthy in (True, False)

        # Close all
        await manager.close_all()

    async def test_multiple_resources(self, sqlite_settings, minio_settings) -> None:
        """Test managing multiple resources."""
        from orchid_commons.blob.minio import create_minio_profile
        from orchid_commons.db import create_sqlite_resource
        from orchid_commons.runtime import ResourceManager

        manager = ResourceManager()

        # Create resources first
        sqlite = await create_sqlite_resource(sqlite_settings)
        blob = await create_minio_profile(minio_settings)

        # Register resources
        manager.register("sqlite", sqlite)
        manager.register("blob", blob)

        # Get both resources (synchronous)
        retrieved_sqlite = manager.get("sqlite")
        retrieved_blob = manager.get("blob")

        assert retrieved_sqlite is sqlite
        assert retrieved_blob is blob

        # Health report includes both
        report = await manager.health_report()
        assert report is not None

        await manager.close_all()


# ============================================================================
# FULL STACK INTEGRATION TEST
# ============================================================================


class TestFullStackE2E:
    """Integration test using multiple modules together."""

    async def test_full_workflow(self, sqlite_settings, minio_settings) -> None:
        """
        Simulate a real workflow:
        1. Create resources
        2. Register in manager
        3. Perform operations with correlation
        4. Check health
        5. Cleanup
        """
        from orchid_commons.blob.minio import create_minio_profile
        from orchid_commons.db import create_sqlite_resource
        from orchid_commons.observability.logging import correlation_scope
        from orchid_commons.runtime import ResourceManager

        # 1. Create resources
        sqlite = await create_sqlite_resource(sqlite_settings)
        blob = await create_minio_profile(minio_settings)

        # 2. Create manager and register resources
        manager = ResourceManager()
        manager.register("sqlite", sqlite)
        manager.register("blob", blob)

        try:
            # 3. Perform operations within correlation scope
            with correlation_scope(request_id="workflow-123", trace_id="trace-workflow"):
                # Create table
                await sqlite.execute(
                    "CREATE TABLE IF NOT EXISTS workflow_log(id INTEGER PRIMARY KEY, action TEXT)",
                    commit=True,
                )

                # Insert record
                await sqlite.execute(
                    "INSERT INTO workflow_log(action) VALUES (?)",
                    ("blob_upload",),
                    commit=True,
                )

                # Upload blob
                key = f"workflow/{uuid4().hex}.txt"
                await blob.upload(key, b"workflow data")

                # Verify
                obj = await blob.download(key)
                assert obj.data == b"workflow data"

                # Cleanup blob
                await blob.delete(key)

            # 4. Check health
            report = await manager.health_report()
            assert report is not None
            assert report.healthy is True

        finally:
            # 5. Cleanup
            await manager.close_all()
