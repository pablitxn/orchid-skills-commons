"""Tests for DocumentStore contract and typed errors."""

from __future__ import annotations

from orchid_commons.db.document import (
    DocumentAuthError,
    DocumentNotFoundError,
    DocumentOperationError,
    DocumentStore,
    DocumentStoreError,
    DocumentTransientError,
    DocumentValidationError,
)


class TestDocumentStoreErrors:
    def test_base_error_formats_message(self) -> None:
        error = DocumentStoreError(
            operation="insert_one",
            collection="users",
            message="connection refused",
        )

        assert str(error) == "Document insert_one failed for 'users': connection refused"
        assert error.operation == "insert_one"
        assert error.collection == "users"

    def test_base_error_handles_none_collection(self) -> None:
        error = DocumentStoreError(
            operation="ping",
            collection=None,
            message="timeout",
        )

        assert str(error) == "Document ping failed for '<unknown>': timeout"

    def test_validation_error_inherits_from_base(self) -> None:
        error = DocumentValidationError(
            operation="insert_one",
            collection="users",
            message="document cannot be empty",
        )

        assert isinstance(error, DocumentStoreError)
        assert "document cannot be empty" in str(error)

    def test_not_found_error_inherits_from_base(self) -> None:
        error = DocumentNotFoundError(
            operation="find_one",
            collection="users",
            message="collection does not exist",
        )

        assert isinstance(error, DocumentStoreError)
        assert error.operation == "find_one"

    def test_auth_error_inherits_from_base(self) -> None:
        error = DocumentAuthError(
            operation="find_many",
            collection="secrets",
            message="unauthorized",
        )

        assert isinstance(error, DocumentStoreError)
        assert "unauthorized" in str(error)

    def test_transient_error_inherits_from_base(self) -> None:
        error = DocumentTransientError(
            operation="update_one",
            collection="users",
            message="connection reset",
        )

        assert isinstance(error, DocumentStoreError)
        assert error.collection == "users"

    def test_operation_error_inherits_from_base(self) -> None:
        error = DocumentOperationError(
            operation="delete_one",
            collection="users",
            message="write concern error",
        )

        assert isinstance(error, DocumentStoreError)
        assert "write concern error" in str(error)


class TestDocumentStoreProtocol:
    def test_protocol_is_runtime_checkable(self) -> None:
        assert hasattr(DocumentStore, "__protocol_attrs__") or hasattr(
            DocumentStore, "_is_protocol"
        )

    def test_mongodb_is_document_store_instance(self) -> None:
        from orchid_commons.db.mongodb import MongoDbResource
        from tests.test_mongodb import FakeDatabase, FakeMongoClient

        database = FakeDatabase()
        resource = MongoDbResource(
            _client=FakeMongoClient(database),
            _database=database,
            database_name="test",
        )

        assert isinstance(resource, DocumentStore)
