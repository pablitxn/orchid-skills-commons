"""MinIO profile built on top of the S3-compatible blob provider."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import perf_counter
from typing import Protocol

from orchid_commons.blob.s3 import S3BlobStorage, S3CompatibleClient
from orchid_commons.config.resources import MinioSettings
from orchid_commons.runtime.errors import MissingDependencyError
from orchid_commons.runtime.health import HealthStatus
from orchid_commons.runtime.manager import register_factory


class SupportsBucketBootstrapClient(S3CompatibleClient, Protocol):
    """S3-compatible client contract required by MinIO profile bootstrap."""

    def make_bucket(self, bucket_name: str, location: str | None = None) -> None:
        """Create a bucket if missing."""
        ...

    def list_objects(
        self,
        bucket_name: str,
        prefix: str | None = None,
        recursive: bool = False,
    ) -> list:
        """List objects in the bucket."""
        ...


@dataclass(slots=True, frozen=True)
class BucketBootstrapResult:
    """Result for bucket bootstrap helper."""

    bucket: str
    exists: bool
    created: bool


def minio_local_dev_settings(
    *,
    access_key: str,
    secret_key: str,
    bucket: str = "orchid-dev",
    endpoint: str = "localhost:9000",
    secure: bool = False,
    region: str | None = None,
    create_bucket_if_missing: bool = True,
) -> MinioSettings:
    """Build MinIO settings defaults for local development/docker-compose."""
    return MinioSettings.local_dev(
        bucket=bucket,
        endpoint=endpoint,
        access_key=access_key,
        secret_key=secret_key,
        secure=secure,
        region=region,
        create_bucket_if_missing=create_bucket_if_missing,
    )


def _build_minio_client(settings: MinioSettings) -> SupportsBucketBootstrapClient:
    try:
        from minio import Minio
    except ImportError as exc:  # pragma: no cover - depends on optional extras
        raise MissingDependencyError(
            "MinIO support requires optional dependency 'minio'. "
            "Install with `orchid-mcp-commons[blob]`."
        ) from exc

    # Minio satisfies SupportsBucketBootstrapClient but has broader method signatures
    return Minio(**settings.to_s3_client_kwargs())  # type: ignore[arg-type,return-value]


async def bootstrap_bucket(
    client: SupportsBucketBootstrapClient,
    bucket: str,
    *,
    create_if_missing: bool = False,
    region: str | None = None,
) -> BucketBootstrapResult:
    """Ensure a bucket exists, creating it optionally."""
    exists = await asyncio.to_thread(client.bucket_exists, bucket)
    if exists:
        return BucketBootstrapResult(bucket=bucket, exists=True, created=False)

    if not create_if_missing:
        return BucketBootstrapResult(bucket=bucket, exists=False, created=False)

    try:
        await asyncio.to_thread(client.make_bucket, bucket, location=region)
    except Exception as exc:
        # Handle race where another process created it between checks.
        exists = await asyncio.to_thread(client.bucket_exists, bucket)
        if not exists:
            raise exc
        return BucketBootstrapResult(bucket=bucket, exists=True, created=False)

    return BucketBootstrapResult(bucket=bucket, exists=False, created=True)


class MinioProfile(S3BlobStorage):
    """MinIO profile over ``S3BlobStorage`` with bucket bootstrap helpers."""

    def __init__(
        self,
        *,
        client: SupportsBucketBootstrapClient,
        settings: MinioSettings,
        provider: str = "minio",
        provider_label: str = "MinIO",
    ) -> None:
        super().__init__(client=client, bucket=settings.bucket, metrics_resource=provider)
        self._settings = settings
        self._bootstrap_client = client
        self._provider = provider
        self._provider_label = provider_label

    @property
    def settings(self) -> MinioSettings:
        return self._settings

    @property
    def provider(self) -> str:
        return self._provider

    async def ensure_bucket(
        self, *, create_if_missing: bool | None = None
    ) -> BucketBootstrapResult:
        """Bootstrap profile bucket with optional create-if-missing behavior."""
        return await bootstrap_bucket(
            self._bootstrap_client,
            self.bucket,
            create_if_missing=(
                self._settings.create_bucket_if_missing
                if create_if_missing is None
                else create_if_missing
            ),
            region=self._settings.region,
        )

    async def health_check(self) -> HealthStatus:
        """Run MinIO-specific health check with endpoint/bucket diagnostics."""
        start = perf_counter()
        details = {
            "provider": self._provider,
            "endpoint": self._settings.endpoint,
            "bucket": self.bucket,
        }

        try:
            exists = await asyncio.to_thread(self._bootstrap_client.bucket_exists, self.bucket)
        except (OSError, TimeoutError, RuntimeError, ValueError) as exc:
            latency_ms = (perf_counter() - start) * 1000
            return HealthStatus(
                healthy=False,
                latency_ms=latency_ms,
                message=f"{self._provider_label} health check failed: {exc}",
                details={**details, "error_type": type(exc).__name__},
            )

        latency_ms = (perf_counter() - start) * 1000
        if exists:
            return HealthStatus(
                healthy=True,
                latency_ms=latency_ms,
                message=f"{self._provider_label} bucket '{self.bucket}' is reachable",
                details=details,
            )

        return HealthStatus(
            healthy=False,
            latency_ms=latency_ms,
            message=f"{self._provider_label} bucket '{self.bucket}' does not exist",
            details=details,
        )


async def create_minio_profile(
    settings: MinioSettings,
    *,
    create_bucket_if_missing: bool | None = None,
) -> MinioProfile:
    """Build and bootstrap MinIO profile from settings."""
    profile = MinioProfile(client=_build_minio_client(settings), settings=settings)
    await profile.ensure_bucket(create_if_missing=create_bucket_if_missing)
    return profile


def register_minio_factory(resource_name: str = "minio") -> None:
    """Register MinIO profile factory in ResourceManager bootstrap registry."""
    register_factory(resource_name, "minio", create_minio_profile)
