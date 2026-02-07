"""Typed configuration models and environment loading helpers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orchid_commons.config.models import AppSettings


def _r2_endpoint_from_account(account_id: str) -> str:
    return f"{account_id}.r2.cloudflarestorage.com"


@dataclass(slots=True)
class SqliteSettings:
    db_path: Path = Path("data/romy_skills.db")


@dataclass(slots=True)
class PostgresSettings:
    dsn: str
    min_pool_size: int = 1
    max_pool_size: int = 10
    command_timeout_seconds: float = 60.0


@dataclass(slots=True)
class RedisSettings:
    url: str
    key_prefix: str = ""
    default_ttl_seconds: int | None = None
    encoding: str = "utf-8"
    decode_responses: bool = True
    socket_timeout_seconds: float | None = 5.0
    connect_timeout_seconds: float | None = 5.0
    health_check_interval_seconds: float = 15.0


@dataclass(slots=True)
class MongoDbSettings:
    uri: str
    database: str
    server_selection_timeout_ms: int = 2000
    connect_timeout_ms: int = 2000
    ping_timeout_seconds: float = 2.0
    app_name: str | None = None


@dataclass(slots=True)
class MinioSettings:
    endpoint: str
    access_key: str
    secret_key: str
    bucket: str = "orchid"
    create_bucket_if_missing: bool = False
    secure: bool = False
    region: str | None = None

    @classmethod
    def local_dev(
        cls,
        *,
        bucket: str = "orchid-dev",
        endpoint: str = "localhost:9000",
        access_key: str = "minioadmin",
        secret_key: str = "minioadmin",
        secure: bool = False,
        region: str | None = None,
        create_bucket_if_missing: bool = True,
    ) -> MinioSettings:
        """Build defaults that work with local docker-compose MinIO."""
        return cls(
            endpoint=endpoint,
            access_key=access_key,
            secret_key=secret_key,
            bucket=bucket,
            create_bucket_if_missing=create_bucket_if_missing,
            secure=secure,
            region=region,
        )

    def to_s3_client_kwargs(self) -> dict[str, str | bool | None]:
        """Return kwargs compatible with S3-compatible clients like MinIO."""
        return {
            "endpoint": self.endpoint,
            "access_key": self.access_key,
            "secret_key": self.secret_key,
            "secure": self.secure,
            "region": self.region,
        }

    def presign_base_url(self) -> str:
        scheme = "https" if self.secure else "http"
        return f"{scheme}://{self.endpoint}"


@dataclass(slots=True)
class R2Settings:
    access_key: str
    secret_key: str
    bucket: str = "orchid"
    account_id: str | None = None
    endpoint: str | None = None
    create_bucket_if_missing: bool = False
    secure: bool = True
    region: str = "auto"

    def __post_init__(self) -> None:
        # Validate eagerly to keep runtime behavior deterministic.
        _ = self.resolved_endpoint

    @property
    def resolved_endpoint(self) -> str:
        if self.endpoint:
            return self.endpoint
        if not self.account_id:
            raise ValueError("R2 requires either endpoint or account_id")
        return _r2_endpoint_from_account(self.account_id)

    def to_s3_client_kwargs(self) -> dict[str, str | bool]:
        """Return kwargs compatible with S3-compatible clients like MinIO."""
        return {
            "endpoint": self.resolved_endpoint,
            "access_key": self.access_key,
            "secret_key": self.secret_key,
            "secure": self.secure,
            "region": self.region,
        }

    def to_minio_settings(self) -> MinioSettings:
        """Adapt R2 profile into the same shape expected by MinIO callers."""
        return MinioSettings(
            endpoint=self.resolved_endpoint,
            access_key=self.access_key,
            secret_key=self.secret_key,
            bucket=self.bucket,
            create_bucket_if_missing=self.create_bucket_if_missing,
            secure=self.secure,
            region=self.region,
        )

    def presign_base_url(self) -> str:
        scheme = "https" if self.secure else "http"
        return f"{scheme}://{self.resolved_endpoint}"


@dataclass(slots=True)
class PgVectorSettings:
    table: str = "embeddings"
    dimensions: int = 1536
    distance_metric: str = "cosine"
    ivfflat_lists: int = 100


@dataclass(slots=True)
class ResourceSettings:
    sqlite: SqliteSettings | None = None
    postgres: PostgresSettings | None = None
    redis: RedisSettings | None = None
    mongodb: MongoDbSettings | None = None
    minio: MinioSettings | None = None
    r2: R2Settings | None = None
    pgvector: PgVectorSettings | None = None

    @classmethod
    def from_env(cls, prefix: str = "ORCHID_") -> ResourceSettings:
        """Build settings from environment variables.

        Expected variables:
        - ORCHID_SQLITE_DB_PATH
        - ORCHID_POSTGRES_DSN
        - ORCHID_POSTGRES_MIN_POOL_SIZE
        - ORCHID_POSTGRES_MAX_POOL_SIZE
        - ORCHID_POSTGRES_COMMAND_TIMEOUT_SECONDS
        - ORCHID_REDIS_URL
        - ORCHID_REDIS_KEY_PREFIX
        - ORCHID_REDIS_DEFAULT_TTL_SECONDS
        - ORCHID_REDIS_ENCODING
        - ORCHID_REDIS_DECODE_RESPONSES
        - ORCHID_REDIS_SOCKET_TIMEOUT_SECONDS
        - ORCHID_REDIS_CONNECT_TIMEOUT_SECONDS
        - ORCHID_REDIS_HEALTH_CHECK_INTERVAL_SECONDS
        - ORCHID_MONGODB_URI
        - ORCHID_MONGODB_DATABASE
        - ORCHID_MONGODB_SERVER_SELECTION_TIMEOUT_MS
        - ORCHID_MONGODB_CONNECT_TIMEOUT_MS
        - ORCHID_MONGODB_PING_TIMEOUT_SECONDS
        - ORCHID_MONGODB_APP_NAME
        - ORCHID_MINIO_ENDPOINT
        - ORCHID_MINIO_ACCESS_KEY
        - ORCHID_MINIO_SECRET_KEY
        - ORCHID_MINIO_BUCKET
        - ORCHID_MINIO_CREATE_BUCKET_IF_MISSING
        - ORCHID_MINIO_SECURE
        - ORCHID_MINIO_REGION
        - ORCHID_R2_ACCOUNT_ID
        - ORCHID_R2_ENDPOINT
        - ORCHID_R2_ACCESS_KEY
        - ORCHID_R2_SECRET_KEY
        - ORCHID_R2_BUCKET
        - ORCHID_R2_CREATE_BUCKET_IF_MISSING
        - ORCHID_R2_SECURE
        - ORCHID_R2_REGION
        - ORCHID_PGVECTOR_TABLE
        - ORCHID_PGVECTOR_DIMENSIONS
        - ORCHID_PGVECTOR_DISTANCE_METRIC
        - ORCHID_PGVECTOR_IVFFLAT_LISTS
        """

        def env(name: str) -> str | None:
            return os.getenv(f"{prefix}{name}")

        def env_bool(name: str, default: bool = False) -> bool:
            value = env(name)
            if value is None:
                return default
            return value.strip().lower() in {"1", "true", "yes", "on"}

        sqlite = None
        sqlite_path = env("SQLITE_DB_PATH")
        if sqlite_path:
            sqlite = SqliteSettings(db_path=Path(sqlite_path))

        postgres = None
        postgres_dsn = env("POSTGRES_DSN")
        if postgres_dsn:
            postgres = PostgresSettings(
                dsn=postgres_dsn,
                min_pool_size=int(env("POSTGRES_MIN_POOL_SIZE") or 1),
                max_pool_size=int(env("POSTGRES_MAX_POOL_SIZE") or 10),
                command_timeout_seconds=float(env("POSTGRES_COMMAND_TIMEOUT_SECONDS") or 60.0),
            )

        redis = None
        redis_url = env("REDIS_URL")
        if redis_url:
            default_ttl = env("REDIS_DEFAULT_TTL_SECONDS")
            socket_timeout = env("REDIS_SOCKET_TIMEOUT_SECONDS")
            connect_timeout = env("REDIS_CONNECT_TIMEOUT_SECONDS")
            redis = RedisSettings(
                url=redis_url,
                key_prefix=env("REDIS_KEY_PREFIX") or "",
                default_ttl_seconds=(int(default_ttl) if default_ttl is not None else None),
                encoding=env("REDIS_ENCODING") or "utf-8",
                decode_responses=env_bool("REDIS_DECODE_RESPONSES", True),
                socket_timeout_seconds=(
                    float(socket_timeout) if socket_timeout is not None else None
                ),
                connect_timeout_seconds=(
                    float(connect_timeout) if connect_timeout is not None else None
                ),
                health_check_interval_seconds=float(
                    env("REDIS_HEALTH_CHECK_INTERVAL_SECONDS") or 15.0
                ),
            )

        mongodb = None
        mongodb_uri = env("MONGODB_URI")
        mongodb_database = env("MONGODB_DATABASE")
        if mongodb_uri and mongodb_database:
            mongodb = MongoDbSettings(
                uri=mongodb_uri,
                database=mongodb_database,
                server_selection_timeout_ms=int(
                    env("MONGODB_SERVER_SELECTION_TIMEOUT_MS") or 2000
                ),
                connect_timeout_ms=int(env("MONGODB_CONNECT_TIMEOUT_MS") or 2000),
                ping_timeout_seconds=float(env("MONGODB_PING_TIMEOUT_SECONDS") or 2.0),
                app_name=env("MONGODB_APP_NAME"),
            )

        minio = None
        minio_endpoint = env("MINIO_ENDPOINT")
        minio_access_key = env("MINIO_ACCESS_KEY")
        minio_secret_key = env("MINIO_SECRET_KEY")
        if minio_endpoint and minio_access_key and minio_secret_key:
            minio = MinioSettings(
                endpoint=minio_endpoint,
                access_key=minio_access_key,
                secret_key=minio_secret_key,
                bucket=env("MINIO_BUCKET") or "orchid",
                create_bucket_if_missing=env_bool("MINIO_CREATE_BUCKET_IF_MISSING", False),
                secure=env_bool("MINIO_SECURE", False),
                region=env("MINIO_REGION"),
            )

        r2 = None
        r2_account_id = env("R2_ACCOUNT_ID")
        r2_endpoint = env("R2_ENDPOINT")
        r2_access_key = env("R2_ACCESS_KEY")
        r2_secret_key = env("R2_SECRET_KEY")
        if r2_access_key and r2_secret_key and (r2_endpoint or r2_account_id):
            r2 = R2Settings(
                account_id=r2_account_id,
                endpoint=r2_endpoint,
                access_key=r2_access_key,
                secret_key=r2_secret_key,
                bucket=env("R2_BUCKET") or "orchid",
                create_bucket_if_missing=env_bool("R2_CREATE_BUCKET_IF_MISSING", False),
                secure=env_bool("R2_SECURE", True),
                region=env("R2_REGION") or "auto",
            )

        pgvector = None
        if postgres_dsn:
            pgvector = PgVectorSettings(
                table=env("PGVECTOR_TABLE") or "embeddings",
                dimensions=int(env("PGVECTOR_DIMENSIONS") or 1536),
                distance_metric=env("PGVECTOR_DISTANCE_METRIC") or "cosine",
                ivfflat_lists=int(env("PGVECTOR_IVFFLAT_LISTS") or 100),
            )

        return cls(
            sqlite=sqlite,
            postgres=postgres,
            redis=redis,
            mongodb=mongodb,
            minio=minio,
            r2=r2,
            pgvector=pgvector,
        )

    @classmethod
    def from_app_settings(cls, app_settings: AppSettings) -> ResourceSettings:
        """Convert config.AppSettings into ResourceSettings for ResourceManager."""
        resources = app_settings.resources

        sqlite = None
        if resources.sqlite is not None:
            sqlite = SqliteSettings(db_path=Path(resources.sqlite.db_path))

        postgres = None
        if resources.postgres is not None:
            postgres = PostgresSettings(
                dsn=resources.postgres.dsn,
                min_pool_size=resources.postgres.min_pool_size,
                max_pool_size=resources.postgres.max_pool_size,
                command_timeout_seconds=resources.postgres.command_timeout_seconds,
            )

        redis = None
        if resources.redis is not None:
            redis = RedisSettings(
                url=resources.redis.url,
                key_prefix=resources.redis.key_prefix,
                default_ttl_seconds=resources.redis.default_ttl_seconds,
                encoding=resources.redis.encoding,
                decode_responses=resources.redis.decode_responses,
                socket_timeout_seconds=resources.redis.socket_timeout_seconds,
                connect_timeout_seconds=resources.redis.connect_timeout_seconds,
                health_check_interval_seconds=resources.redis.health_check_interval_seconds,
            )

        mongodb = None
        if resources.mongodb is not None:
            mongodb = MongoDbSettings(
                uri=resources.mongodb.uri,
                database=resources.mongodb.database,
                server_selection_timeout_ms=resources.mongodb.server_selection_timeout_ms,
                connect_timeout_ms=resources.mongodb.connect_timeout_ms,
                ping_timeout_seconds=resources.mongodb.ping_timeout_seconds,
                app_name=resources.mongodb.app_name,
            )

        minio = None
        if resources.minio is not None:
            minio = MinioSettings(
                endpoint=resources.minio.endpoint,
                access_key=resources.minio.access_key,
                secret_key=resources.minio.secret_key,
                bucket=resources.minio.bucket,
                create_bucket_if_missing=resources.minio.create_bucket_if_missing,
                secure=resources.minio.secure,
                region=resources.minio.region,
            )

        r2 = None
        if resources.r2 is not None:
            r2 = R2Settings(
                account_id=resources.r2.account_id,
                endpoint=resources.r2.endpoint,
                access_key=resources.r2.access_key,
                secret_key=resources.r2.secret_key,
                bucket=resources.r2.bucket,
                create_bucket_if_missing=resources.r2.create_bucket_if_missing,
                secure=resources.r2.secure,
                region=resources.r2.region,
            )

        return cls(
            sqlite=sqlite,
            postgres=postgres,
            redis=redis,
            mongodb=mongodb,
            minio=minio,
            r2=r2,
            pgvector=None,
        )
