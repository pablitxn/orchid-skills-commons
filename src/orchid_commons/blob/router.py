"""Multi-bucket blob router for S3/MinIO with logical alias support."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from time import perf_counter
from typing import Literal

from orchid_commons.blob.minio import (
    SupportsBucketBootstrapClient,
    bootstrap_bucket,
)
from orchid_commons.blob.s3 import (
    _DEFAULT_PRESIGN_EXPIRY,
    BlobObject,
    S3BlobStorage,
)
from orchid_commons.config.resources import MultiBucketSettings
from orchid_commons.runtime.errors import MissingDependencyError
from orchid_commons.runtime.health import HealthStatus
from orchid_commons.runtime.manager import register_factory


@dataclass(frozen=True, slots=True)
class BucketInfo:
    """Information about a bucket alias and its status."""

    alias: str
    bucket: str
    exists: bool
    created: bool


class MultiBucketBlobRouter:
    """Routes blob operations to multiple buckets via logical aliases.

    This router allows consumers to work with logical bucket names (aliases)
    instead of physical bucket names. For example, a video processing service
    can use aliases like 'videos', 'chunks', and 'frames' without knowing
    the actual bucket names.

    Example usage:
        settings = MultiBucketSettings(
            endpoint="localhost:9000",
            access_key="minioadmin",
            secret_key="minioadmin",
            buckets={"videos": "prod-videos", "chunks": "prod-chunks"},
        )
        router = await create_multi_bucket_router(settings)

        # Upload to 'videos' bucket
        await router.upload("videos", "clip.mp4", video_bytes)

        # Download from 'chunks' bucket
        chunk = await router.download("chunks", "segment-001.ts")
    """

    def __init__(
        self,
        *,
        client: SupportsBucketBootstrapClient,
        settings: MultiBucketSettings,
        provider: str = "multi_bucket",
    ) -> None:
        self._client = client
        self._settings = settings
        self._provider = provider
        self._storages: dict[str, S3BlobStorage] = {}

        # Pre-create storage instances for each alias
        for alias, bucket in settings.buckets.items():
            self._storages[alias] = S3BlobStorage(
                client=client,
                bucket=bucket,
                metrics_resource=f"{provider}:{alias}",
            )

    @property
    def aliases(self) -> tuple[str, ...]:
        """Return all configured bucket aliases."""
        return tuple(self._settings.buckets.keys())

    @property
    def settings(self) -> MultiBucketSettings:
        """Return the settings used to configure this router."""
        return self._settings

    def get_storage(self, alias: str) -> S3BlobStorage:
        """Get the storage instance for a specific alias."""
        if alias not in self._storages:
            raise KeyError(f"Unknown bucket alias: {alias!r}")
        return self._storages[alias]

    def get_bucket(self, alias: str) -> str:
        """Resolve alias to physical bucket name."""
        return self._settings.get_bucket(alias)

    async def upload(
        self,
        alias: str,
        key: str,
        data: bytes | bytearray | memoryview,
        *,
        content_type: str | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> None:
        """Upload object bytes to the bucket identified by alias."""
        storage = self.get_storage(alias)
        await storage.upload(key, data, content_type=content_type, metadata=metadata)

    async def download(self, alias: str, key: str) -> BlobObject:
        """Download object from the bucket identified by alias."""
        storage = self.get_storage(alias)
        return await storage.download(key)

    async def exists(self, alias: str, key: str) -> bool:
        """Check if object exists in the bucket identified by alias."""
        storage = self.get_storage(alias)
        return await storage.exists(key)

    async def delete(self, alias: str, key: str) -> None:
        """Delete object from the bucket identified by alias."""
        storage = self.get_storage(alias)
        await storage.delete(key)

    async def presign(
        self,
        alias: str,
        key: str,
        *,
        method: Literal["GET", "PUT"] = "GET",
        expires: timedelta = _DEFAULT_PRESIGN_EXPIRY,
    ) -> str:
        """Generate presigned URL for object in the bucket identified by alias."""
        storage = self.get_storage(alias)
        return await storage.presign(key, method=method, expires=expires)

    async def list_objects(
        self,
        alias: str,
        prefix: str = "",
        *,
        recursive: bool = True,
    ) -> list[str]:
        """List object keys in the bucket identified by alias.

        Args:
            alias: Logical bucket alias.
            prefix: Filter objects by key prefix.
            recursive: If True, list all objects recursively; otherwise list only
                       objects at the prefix level.

        Returns:
            List of object keys matching the prefix.
        """
        bucket = self.get_bucket(alias)

        def _list() -> list[str]:
            objects = self._client.list_objects(bucket, prefix=prefix, recursive=recursive)
            return [obj.object_name for obj in objects]

        return await asyncio.to_thread(_list)

    async def ensure_buckets(
        self,
        *,
        create_if_missing: bool | None = None,
    ) -> list[BucketInfo]:
        """Bootstrap all configured buckets.

        Args:
            create_if_missing: Override the settings value for bucket creation.

        Returns:
            List of BucketInfo with status for each alias.
        """
        should_create = (
            self._settings.create_buckets_if_missing
            if create_if_missing is None
            else create_if_missing
        )
        results: list[BucketInfo] = []

        for alias, bucket in self._settings.buckets.items():
            result = await bootstrap_bucket(
                self._client,
                bucket,
                create_if_missing=should_create,
                region=self._settings.region,
            )
            results.append(
                BucketInfo(
                    alias=alias,
                    bucket=bucket,
                    exists=result.exists or result.created,
                    created=result.created,
                )
            )

        return results

    async def health_check(self) -> HealthStatus:
        """Check connectivity and bucket visibility for all aliases."""
        start = perf_counter()
        details: dict[str, object] = {
            "provider": self._provider,
            "endpoint": self._settings.endpoint,
            "aliases": list(self._settings.buckets.keys()),
        }

        bucket_status: dict[str, bool] = {}
        all_healthy = True

        for alias, bucket in self._settings.buckets.items():
            try:
                exists = await asyncio.to_thread(self._client.bucket_exists, bucket)
                bucket_status[alias] = exists
                if not exists:
                    all_healthy = False
            except Exception as exc:
                bucket_status[alias] = False
                all_healthy = False
                details[f"error_{alias}"] = str(exc)

        latency_ms = (perf_counter() - start) * 1000
        details["buckets"] = bucket_status

        if all_healthy:
            return HealthStatus(
                healthy=True,
                latency_ms=latency_ms,
                message="All buckets are reachable",
                details=details,
            )

        unhealthy_aliases = [a for a, healthy in bucket_status.items() if not healthy]
        return HealthStatus(
            healthy=False,
            latency_ms=latency_ms,
            message=f"Buckets not accessible: {', '.join(unhealthy_aliases)}",
            details=details,
        )

    async def close(self) -> None:
        """Close underlying storage connections.

        Multiple aliases can share the same S3 client instance. We close each
        distinct client only once.
        """
        errors: list[Exception] = []
        seen_clients: set[int] = set()
        for storage in self._storages.values():
            client_id = id(getattr(storage, "_client", None))
            if client_id in seen_clients:
                continue
            seen_clients.add(client_id)
            try:
                await storage.close()
            except Exception as exc:
                errors.append(exc)

        if errors:
            raise ExceptionGroup("Errors closing multi-bucket storages", errors)


def _build_minio_client(settings: MultiBucketSettings) -> SupportsBucketBootstrapClient:
    """Build MinIO client from MultiBucketSettings."""
    try:
        from minio import Minio
    except ImportError as exc:
        raise MissingDependencyError(
            "MinIO support requires optional dependency 'minio'. "
            "Install with `orchid-skills-commons[blob]`."
        ) from exc

    # Minio satisfies SupportsBucketBootstrapClient but has broader method signatures
    return Minio(**settings.to_s3_client_kwargs())  # type: ignore[arg-type,return-value]


async def create_multi_bucket_router(
    settings: MultiBucketSettings,
    *,
    create_buckets_if_missing: bool | None = None,
) -> MultiBucketBlobRouter:
    """Build and bootstrap MultiBucketBlobRouter from settings.

    Args:
        settings: Multi-bucket configuration with alias mappings.
        create_buckets_if_missing: Override settings value for bucket creation.

    Returns:
        Initialized and bootstrapped router ready for use.
    """
    client = _build_minio_client(settings)
    router = MultiBucketBlobRouter(client=client, settings=settings)
    await router.ensure_buckets(create_if_missing=create_buckets_if_missing)
    return router


def register_multi_bucket_factory(resource_name: str = "multi_bucket") -> None:
    """Register multi-bucket router factory in ResourceManager bootstrap registry."""
    register_factory(resource_name, "multi_bucket", create_multi_bucket_router)
