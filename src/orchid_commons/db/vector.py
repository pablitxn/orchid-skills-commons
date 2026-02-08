"""Common vector store contract and typed errors."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from orchid_commons.runtime.errors import OrchidCommonsError
from orchid_commons.runtime.health import HealthStatus


class VectorStoreError(OrchidCommonsError):
    """Base exception for vector store operations."""

    def __init__(
        self,
        operation: str,
        collection: str | None,
        message: str,
    ) -> None:
        self.operation = operation
        self.collection = collection
        target = "<unknown>" if collection is None else collection
        super().__init__(f"Vector {operation} failed for '{target}': {message}")


class VectorValidationError(VectorStoreError):
    """Raised when vector operation arguments are invalid."""


class VectorNotFoundError(VectorStoreError):
    """Raised when a vector collection or entity does not exist."""


class VectorAuthError(VectorStoreError):
    """Raised when credentials are invalid or access is denied."""


class VectorTransientError(VectorStoreError):
    """Raised for retryable/transient vector operation failures."""


class VectorOperationError(VectorStoreError):
    """Raised for non-transient vector operation failures."""


@dataclass(frozen=True, slots=True)
class VectorPoint:
    """A vector point to insert or update."""

    id: int | str
    vector: list[float]
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class VectorSearchResult:
    """Normalized result returned by vector search operations."""

    id: int | str
    score: float
    payload: Mapping[str, Any] = field(default_factory=dict)
    vector: list[float] | None = None


@runtime_checkable
class VectorStore(Protocol):
    """Common contract for vector backends.

    Implementations should raise ``VectorStoreError`` subclasses for backend failures.
    """

    async def upsert(self, collection_name: str, points: Sequence[VectorPoint]) -> int:
        """Insert or update vector points and return affected count."""
        ...

    async def search(
        self,
        collection_name: str,
        query_vector: Sequence[float],
        *,
        limit: int = 10,
        filters: Mapping[str, Any] | None = None,
        score_threshold: float | None = None,
        with_payload: bool = True,
        with_vectors: bool = False,
    ) -> list[VectorSearchResult]:
        """Return nearest vectors sorted by score descending."""
        ...

    async def delete(
        self,
        collection_name: str,
        *,
        ids: Sequence[int | str] | None = None,
        filters: Mapping[str, Any] | None = None,
    ) -> int:
        """Delete vectors by ids or filters and return removed count."""
        ...

    async def count(
        self,
        collection_name: str,
        *,
        filters: Mapping[str, Any] | None = None,
    ) -> int:
        """Return vector count for a collection."""
        ...

    async def health_check(self) -> HealthStatus:
        """Run backend health check."""
        ...

    async def close(self) -> None:
        """Release held connections/resources."""
        ...
