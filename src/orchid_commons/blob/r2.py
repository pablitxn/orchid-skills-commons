"""Cloudflare R2 profile over an S3-compatible client."""

from __future__ import annotations

from orchid_commons.blob.minio import MinioProfile, SupportsBucketBootstrapClient
from orchid_commons.config.resources import R2Settings
from orchid_commons.runtime.errors import MissingDependencyError
from orchid_commons.runtime.manager import register_factory


def _build_r2_client(settings: R2Settings) -> SupportsBucketBootstrapClient:
    try:
        from minio import Minio
    except ImportError as exc:  # pragma: no cover - import path depends on extras
        raise MissingDependencyError(
            "Cloudflare R2 support requires optional dependency 'minio'. "
            "Install with `orchid-skills-commons[blob]`."
        ) from exc

    return Minio(**settings.to_s3_client_kwargs())


async def create_r2_profile(
    settings: R2Settings,
    *,
    create_bucket_if_missing: bool | None = None,
) -> MinioProfile:
    """Build and bootstrap Cloudflare R2 profile from settings."""
    minio_settings = settings.to_minio_settings()
    profile = MinioProfile(
        client=_build_r2_client(settings),
        settings=minio_settings,
        provider="cloudflare-r2",
        provider_label="Cloudflare R2",
    )
    await profile.ensure_bucket(create_if_missing=create_bucket_if_missing)
    return profile


def register_r2_factory(resource_name: str = "r2") -> None:
    """Register Cloudflare R2 profile factory in ResourceManager registry."""
    register_factory(resource_name, "r2", create_r2_profile)
