"""Blob storage abstractions and S3-compatible implementation."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from io import BytesIO
from time import perf_counter
from typing import Any, Literal, Protocol, runtime_checkable

from orchid_commons.config.resources import MinioSettings, R2Settings
from orchid_commons.observability.metrics import MetricsRecorder, get_metrics_recorder
from orchid_commons.runtime.errors import MissingDependencyError, OrchidCommonsError
from orchid_commons.runtime.health import HealthStatus

_DEFAULT_PRESIGN_EXPIRY = timedelta(minutes=15)
_NOT_FOUND_CODES = {"NoSuchBucket", "NoSuchKey", "NotFound"}
_AUTH_CODES = {
    "AccessDenied",
    "ExpiredToken",
    "InvalidAccessKeyId",
    "InvalidToken",
    "SignatureDoesNotMatch",
    "Unauthorized",
}
_TRANSIENT_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}


class BlobError(OrchidCommonsError):
    """Base exception for blob operations."""

    def __init__(
        self,
        operation: str,
        bucket: str,
        key: str | None,
        message: str,
    ) -> None:
        self.operation = operation
        self.bucket = bucket
        self.key = key
        target = bucket if key is None else f"{bucket}/{key}"
        super().__init__(f"Blob {operation} failed for '{target}': {message}")


class BlobNotFoundError(BlobError):
    """Raised when a bucket or object does not exist."""


class BlobAuthError(BlobError):
    """Raised when credentials are invalid or access is denied."""


class BlobTransientError(BlobError):
    """Raised for retryable/transient blob operation failures."""


class BlobOperationError(BlobError):
    """Raised for non-transient blob operation failures."""


@dataclass(frozen=True, slots=True)
class BlobObject:
    """Object returned by ``download`` with payload and metadata."""

    key: str
    data: bytes
    content_type: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)


@runtime_checkable
class BlobStorage(Protocol):
    """Common contract for blob backends.

    All methods raise typed ``BlobError`` subclasses for backend failures.
    """

    async def upload(
        self,
        key: str,
        data: bytes | bytearray | memoryview,
        *,
        content_type: str | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> None:
        """Upload object bytes with optional content type and metadata."""
        ...

    async def download(self, key: str) -> BlobObject:
        """Download object payload and return metadata/content type."""
        ...

    async def exists(self, key: str) -> bool:
        """Return ``True`` when the object exists."""
        ...

    async def delete(self, key: str) -> None:
        """Delete object if present."""
        ...

    async def presign(
        self,
        key: str,
        *,
        method: Literal["GET", "PUT"] = "GET",
        expires: timedelta = _DEFAULT_PRESIGN_EXPIRY,
    ) -> str:
        """Generate a signed URL for object access."""
        ...

    async def health_check(self) -> HealthStatus:
        """Run backend health check."""
        ...


@runtime_checkable
class S3ObjectResponse(Protocol):
    """Response contract used by S3-compatible ``get_object`` calls."""

    headers: Mapping[str, str]

    def read(self, amt: int | None = None) -> bytes:
        """Read payload bytes from response."""
        ...

    def close(self) -> None:
        """Close the response stream."""
        ...

    def release_conn(self) -> None:
        """Release connection back to underlying pool."""
        ...


@runtime_checkable
class S3CompatibleClient(Protocol):
    """Subset of S3 API expected by ``S3BlobStorage``.

    The MinIO SDK and Cloudflare R2-compatible clients satisfy this API.
    """

    def put_object(
        self,
        bucket_name: str,
        object_name: str,
        data: BytesIO,
        length: int,
        *,
        content_type: str | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> Any:
        ...

    def get_object(self, bucket_name: str, object_name: str) -> S3ObjectResponse:
        ...

    def stat_object(self, bucket_name: str, object_name: str) -> Any:
        ...

    def remove_object(self, bucket_name: str, object_name: str) -> Any:
        ...

    def presigned_get_object(
        self,
        bucket_name: str,
        object_name: str,
        *,
        expires: timedelta,
    ) -> str:
        ...

    def presigned_put_object(
        self,
        bucket_name: str,
        object_name: str,
        *,
        expires: timedelta,
    ) -> str:
        ...

    def bucket_exists(self, bucket_name: str) -> bool:
        ...


class S3BlobStorage(BlobStorage):
    """S3-compatible blob storage implementation.

    This adapter targets MinIO-compatible semantics and works with providers such
    as AWS S3 and Cloudflare R2 through their S3-compatible APIs.
    """

    def __init__(
        self,
        *,
        client: S3CompatibleClient,
        bucket: str,
        metrics: MetricsRecorder | None = None,
        metrics_resource: str = "s3",
    ) -> None:
        normalized_bucket = bucket.strip()
        if not normalized_bucket:
            raise ValueError("bucket must be a non-empty string")
        self._client = client
        self._bucket = normalized_bucket
        self._metrics = metrics
        self._metrics_resource = metrics_resource

    @property
    def bucket(self) -> str:
        """Bucket name configured for this storage instance."""
        return self._bucket

    @classmethod
    def from_minio_settings(
        cls,
        settings: MinioSettings,
        *,
        bucket: str | None = None,
    ) -> S3BlobStorage:
        """Build storage using project ``MinioSettings``."""
        try:
            from minio import Minio
        except ImportError as exc:
            raise MissingDependencyError(
                "Install optional dependency: orchid-skills-commons[blob]"
            ) from exc

        resolved_bucket = settings.bucket if bucket is None else bucket
        # Minio(**dict) loses type info; Minio satisfies S3CompatibleClient structurally
        client = Minio(**settings.to_s3_client_kwargs())  # type: ignore[arg-type]
        return cls(client=client, bucket=resolved_bucket)  # type: ignore[arg-type]

    @classmethod
    def from_r2_settings(
        cls,
        settings: R2Settings,
        *,
        bucket: str | None = None,
    ) -> S3BlobStorage:
        """Build storage using project ``R2Settings``."""
        try:
            from minio import Minio
        except ImportError as exc:
            raise MissingDependencyError(
                "Install optional dependency: orchid-skills-commons[blob]"
            ) from exc

        resolved_bucket = settings.bucket if bucket is None else bucket
        # Minio(**dict) loses type info; Minio satisfies S3CompatibleClient structurally
        client = Minio(**settings.to_s3_client_kwargs())  # type: ignore[arg-type]
        return cls(client=client, bucket=resolved_bucket)  # type: ignore[arg-type]

    async def upload(
        self,
        key: str,
        data: bytes | bytearray | memoryview,
        *,
        content_type: str | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> None:
        """Upload bytes to ``key`` with optional metadata/content-type."""
        started = perf_counter()
        object_key = _normalize_key(key)
        payload = _coerce_bytes(data)
        metadata_headers = _normalize_metadata(metadata)

        try:
            await asyncio.to_thread(
                self._client.put_object,
                self._bucket,
                object_key,
                BytesIO(payload),
                len(payload),
                content_type=content_type,
                metadata=metadata_headers or None,
            )
        except Exception as exc:
            translated = _translate_blob_error(
                operation="upload",
                bucket=self._bucket,
                key=object_key,
                exc=exc,
            )
            self._observe_error("upload", started, translated)
            raise translated from exc

        self._observe_success("upload", started)

    async def download(self, key: str) -> BlobObject:
        """Download object bytes and include metadata/content-type."""
        started = perf_counter()
        object_key = _normalize_key(key)

        try:
            response = await asyncio.to_thread(
                self._client.get_object,
                self._bucket,
                object_key,
            )
        except Exception as exc:
            translated = _translate_blob_error(
                operation="download",
                bucket=self._bucket,
                key=object_key,
                exc=exc,
            )
            self._observe_error("download", started, translated)
            raise translated from exc

        try:
            payload = await asyncio.to_thread(response.read)
            headers = _headers_to_dict(getattr(response, "headers", None))
        except Exception as exc:
            translated = _translate_blob_error(
                operation="download",
                bucket=self._bucket,
                key=object_key,
                exc=exc,
            )
            self._observe_error("download", started, translated)
            raise translated from exc
        finally:
            _safe_close_response(response)

        result = BlobObject(
            key=object_key,
            data=payload,
            content_type=headers.get("content-type"),
            metadata=_extract_user_metadata(headers),
        )
        self._observe_success("download", started)
        return result

    async def exists(self, key: str) -> bool:
        """Check whether object exists without downloading it."""
        started = perf_counter()
        object_key = _normalize_key(key)

        try:
            await asyncio.to_thread(
                self._client.stat_object,
                self._bucket,
                object_key,
            )
            self._observe_success("exists", started)
            return True
        except Exception as exc:
            if _is_not_found_error(exc):
                self._observe_success("exists", started)
                return False
            translated = _translate_blob_error(
                operation="exists",
                bucket=self._bucket,
                key=object_key,
                exc=exc,
            )
            self._observe_error("exists", started, translated)
            raise translated from exc

    async def delete(self, key: str) -> None:
        """Delete object, treating missing keys as no-op."""
        started = perf_counter()
        object_key = _normalize_key(key)

        try:
            await asyncio.to_thread(
                self._client.remove_object,
                self._bucket,
                object_key,
            )
        except Exception as exc:
            if _is_not_found_error(exc):
                self._observe_success("delete", started)
                return
            translated = _translate_blob_error(
                operation="delete",
                bucket=self._bucket,
                key=object_key,
                exc=exc,
            )
            self._observe_error("delete", started, translated)
            raise translated from exc

        self._observe_success("delete", started)

    async def presign(
        self,
        key: str,
        *,
        method: Literal["GET", "PUT"] = "GET",
        expires: timedelta = _DEFAULT_PRESIGN_EXPIRY,
    ) -> str:
        """Generate presigned URL for ``GET`` or ``PUT`` access."""
        started = perf_counter()
        object_key = _normalize_key(key)
        normalized_method = method.upper()

        if normalized_method not in {"GET", "PUT"}:
            raise ValueError("method must be GET or PUT")

        try:
            if normalized_method == "GET":
                result = await asyncio.to_thread(
                    self._client.presigned_get_object,
                    self._bucket,
                    object_key,
                    expires=expires,
                )
                self._observe_success("presign_get", started)
                return result
            result = await asyncio.to_thread(
                self._client.presigned_put_object,
                self._bucket,
                object_key,
                expires=expires,
            )
            self._observe_success("presign_put", started)
            return result
        except Exception as exc:
            translated = _translate_blob_error(
                operation="presign",
                bucket=self._bucket,
                key=object_key,
                exc=exc,
            )
            self._observe_error("presign", started, translated)
            raise translated from exc

    async def health_check(self) -> HealthStatus:
        """Check connectivity and bucket visibility."""
        started = perf_counter()

        try:
            exists = await asyncio.to_thread(self._client.bucket_exists, self._bucket)
            latency_ms = (perf_counter() - started) * 1000
            if exists:
                self._observe_success("health_check", started)
                return HealthStatus(
                    healthy=True,
                    latency_ms=latency_ms,
                    message="bucket reachable",
                )
            self._observe_success("health_check", started)
            return HealthStatus(
                healthy=False,
                latency_ms=latency_ms,
                message="bucket is not accessible or does not exist",
            )
        except Exception as exc:
            latency_ms = (perf_counter() - started) * 1000
            self._observe_error("health_check", started, exc)
            return HealthStatus(
                healthy=False,
                latency_ms=latency_ms,
                message=f"blob health check failed: {exc}",
                details={"error_type": type(exc).__name__},
            )

    async def close(self) -> None:
        """Best-effort close for underlying clients."""
        started = perf_counter()
        close = getattr(self._client, "close", None)
        if callable(close):
            try:
                maybe_awaitable = close()
                if hasattr(maybe_awaitable, "__await__"):
                    await maybe_awaitable
            except Exception as exc:
                self._observe_error("close", started, exc)
                raise
            self._observe_success("close", started)
            return

        http_client = getattr(self._client, "_http", None)
        clear = getattr(http_client, "clear", None)
        if callable(clear):
            try:
                await asyncio.to_thread(clear)
            except Exception as exc:
                self._observe_error("close", started, exc)
                raise
        self._observe_success("close", started)

    def _observe_success(self, operation: str, started: float) -> None:
        self._metrics_recorder().observe_operation(
            resource=self._metrics_resource,
            operation=operation,
            duration_seconds=perf_counter() - started,
            success=True,
        )

    def _observe_error(self, operation: str, started: float, exc: Exception) -> None:
        self._metrics_recorder().observe_operation(
            resource=self._metrics_resource,
            operation=operation,
            duration_seconds=perf_counter() - started,
            success=False,
        )
        self._metrics_recorder().observe_error(
            resource=self._metrics_resource,
            operation=operation,
            error_type=type(exc).__name__,
        )

    def _metrics_recorder(self) -> MetricsRecorder:
        return get_metrics_recorder() if self._metrics is None else self._metrics


def _normalize_key(key: str) -> str:
    normalized = key.strip()
    if not normalized:
        raise ValueError("key must be a non-empty string")
    return normalized


def _coerce_bytes(data: bytes | bytearray | memoryview) -> bytes:
    if isinstance(data, bytes):
        return data
    if isinstance(data, bytearray):
        return bytes(data)
    return data.tobytes()


def _normalize_metadata(
    metadata: Mapping[str, str] | None,
) -> dict[str, str]:
    if not metadata:
        return {}
    return {str(key): str(value) for key, value in metadata.items()}


def _headers_to_dict(raw_headers: Any) -> dict[str, str]:
    if raw_headers is None:
        return {}
    if hasattr(raw_headers, "items"):
        return {str(key).lower(): str(value) for key, value in raw_headers.items()}
    return {}


def _extract_user_metadata(headers: Mapping[str, str]) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for key, value in headers.items():
        normalized = key.lower()
        if normalized.startswith("x-amz-meta-"):
            metadata[normalized[11:]] = value
    return metadata


def _safe_close_response(response: Any) -> None:
    close = getattr(response, "close", None)
    if callable(close):
        close()

    release_conn = getattr(response, "release_conn", None)
    if callable(release_conn):
        release_conn()


def _translate_blob_error(
    *,
    operation: str,
    bucket: str,
    key: str | None,
    exc: Exception,
) -> BlobError:
    message = str(exc)

    if _is_not_found_error(exc):
        return BlobNotFoundError(operation, bucket, key, message)
    if _is_auth_error(exc):
        return BlobAuthError(operation, bucket, key, message)
    if _is_transient_error(exc):
        return BlobTransientError(operation, bucket, key, message)
    return BlobOperationError(operation, bucket, key, message)


def _is_not_found_error(exc: Exception) -> bool:
    code = _error_code(exc)
    status = _error_status(exc)
    return code in _NOT_FOUND_CODES or status == 404


def _is_auth_error(exc: Exception) -> bool:
    code = _error_code(exc)
    status = _error_status(exc)
    return code in _AUTH_CODES or status in {401, 403}


def _is_transient_error(exc: Exception) -> bool:
    status = _error_status(exc)
    if status in _TRANSIENT_STATUS_CODES:
        return True

    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True

    name = type(exc).__name__.lower()
    return "timeout" in name or "connection" in name


def _error_code(exc: Exception) -> str | None:
    code = getattr(exc, "code", None) or getattr(exc, "error_code", None)
    if code is None:
        return None
    return str(code)


def _error_status(exc: Exception) -> int | None:
    status = getattr(exc, "status", None) or getattr(exc, "status_code", None)
    if status is None:
        return None
    try:
        return int(status)
    except (TypeError, ValueError):
        return None
