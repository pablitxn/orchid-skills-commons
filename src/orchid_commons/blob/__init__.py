"""Blob storage profiles and helpers."""

from orchid_commons.blob.minio import (
    BucketBootstrapResult,
    MinioProfile,
    bootstrap_bucket,
    create_minio_profile,
    minio_local_dev_settings,
    register_minio_factory,
)
from orchid_commons.blob.r2 import create_r2_profile, register_r2_factory
from orchid_commons.blob.router import (
    BucketInfo,
    MultiBucketBlobRouter,
    create_multi_bucket_router,
    register_multi_bucket_factory,
)
from orchid_commons.blob.s3 import (
    BlobAuthError,
    BlobError,
    BlobNotFoundError,
    BlobObject,
    BlobOperationError,
    BlobStorage,
    BlobTransientError,
    S3BlobStorage,
)

__all__ = [
    "BlobAuthError",
    "BlobError",
    "BlobNotFoundError",
    "BlobObject",
    "BlobOperationError",
    "BlobStorage",
    "BlobTransientError",
    "BucketBootstrapResult",
    "BucketInfo",
    "MinioProfile",
    "MultiBucketBlobRouter",
    "S3BlobStorage",
    "bootstrap_bucket",
    "create_minio_profile",
    "create_multi_bucket_router",
    "create_r2_profile",
    "minio_local_dev_settings",
    "register_minio_factory",
    "register_multi_bucket_factory",
    "register_r2_factory",
]
