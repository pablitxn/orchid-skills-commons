"""Common document store contract and typed errors."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from orchid_commons.runtime.errors import OrchidCommonsError
from orchid_commons.runtime.health import HealthStatus


class DocumentStoreError(OrchidCommonsError):
    """Base exception for document store operations."""

    def __init__(
        self,
        operation: str,
        collection: str | None,
        message: str,
    ) -> None:
        self.operation = operation
        self.collection = collection
        target = "<unknown>" if collection is None else collection
        super().__init__(f"Document {operation} failed for '{target}': {message}")


class DocumentValidationError(DocumentStoreError):
    """Raised when document operation arguments are invalid."""


class DocumentNotFoundError(DocumentStoreError):
    """Raised when a document or collection does not exist."""


class DocumentAuthError(DocumentStoreError):
    """Raised when credentials are invalid or access is denied."""


class DocumentTransientError(DocumentStoreError):
    """Raised for retryable/transient document operation failures."""


class DocumentOperationError(DocumentStoreError):
    """Raised for non-transient document operation failures."""


@runtime_checkable
class DocumentStore(Protocol):
    """Common contract for document database backends.

    Implementations should raise ``DocumentStoreError`` subclasses for backend failures.
    """

    async def insert_one(self, collection: str, document: dict[str, Any]) -> Any:
        """Insert a single document and return the inserted ID."""
        ...

    async def find_one(
        self,
        collection: str,
        query: dict[str, Any],
        *,
        projection: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Find a single document matching the query."""
        ...

    async def find_many(
        self,
        collection: str,
        query: dict[str, Any],
        *,
        projection: Mapping[str, Any] | None = None,
        sort: list[tuple[str, int]] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Find multiple documents with optional sort and limit."""
        ...

    async def update_one(
        self,
        collection: str,
        query: dict[str, Any],
        update: dict[str, Any],
        *,
        upsert: bool = False,
    ) -> int:
        """Update a single document and return the modified count."""
        ...

    async def delete_one(self, collection: str, query: dict[str, Any]) -> int:
        """Delete a single document and return the deleted count."""
        ...

    async def count(
        self,
        collection: str,
        query: dict[str, Any] | None = None,
    ) -> int:
        """Return document count for a collection, optionally filtered by query."""
        ...

    async def health_check(self) -> HealthStatus:
        """Run backend health check."""
        ...

    async def close(self) -> None:
        """Release held connections/resources."""
        ...
