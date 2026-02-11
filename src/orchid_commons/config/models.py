"""Typed configuration models with Pydantic validation."""

from __future__ import annotations

import json
import os
import warnings
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


def _r2_endpoint_from_account(account_id: str) -> str:
    return f"{account_id}.r2.cloudflarestorage.com"


class ServiceSettings(BaseModel):
    """Service identification and network settings."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(..., min_length=1, description="Service name")
    version: str = Field(..., min_length=1, description="Service version")
    host: str = Field(
        default="0.0.0.0",
        description=(
            "Bind host. Defaults to 0.0.0.0 for containerised deployments; "
            "restrict via network policies or firewalls in production."
        ),
    )
    port: int = Field(default=8000, ge=1, le=65535, description="Bind port")


class LoggingSettings(BaseModel):
    """Logging configuration."""

    model_config = ConfigDict(frozen=True)

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO", description="Log level"
    )
    format: Literal["json", "text"] = Field(default="json", description="Log output format")
    sampling: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Optional sampling ratio for low-severity logs",
    )


class LangfuseSettings(BaseModel):
    """Langfuse tracing client configuration."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = Field(default=True, description="Enable Langfuse tracing")
    public_key: SecretStr | None = Field(
        default=None, min_length=1, description="Langfuse public key"
    )
    secret_key: SecretStr | None = Field(
        default=None, min_length=1, description="Langfuse secret key"
    )
    base_url: str = Field(
        default="https://cloud.langfuse.com",
        min_length=1,
        description="Langfuse API base URL",
    )
    environment: str | None = Field(default=None, min_length=1, description="Tracing environment")
    release: str | None = Field(default=None, min_length=1, description="Application release tag")
    timeout_seconds: int = Field(default=5, ge=1, description="HTTP timeout in seconds")
    flush_at: int = Field(default=512, ge=1, description="Batch flush size")
    flush_interval_seconds: float = Field(default=5.0, gt=0, description="Batch flush interval")
    sample_rate: float = Field(default=1.0, ge=0.0, le=1.0, description="Trace sample rate")
    debug: bool = Field(default=False, description="Enable Langfuse SDK debug mode")


class ObservabilitySettings(BaseModel):
    """Observability and telemetry settings."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = Field(default=True, description="Enable observability")
    otlp_endpoint: str | None = Field(default=None, description="OpenTelemetry collector endpoint")
    service_name: str | None = Field(default=None, description="Override service name for traces")
    sample_rate: float = Field(default=1.0, ge=0.0, le=1.0, description="Trace sample rate")
    otlp_insecure: bool = Field(
        default=False,
        description=(
            "Use insecure (plaintext) gRPC OTLP transport. "
            "Defaults to False (TLS enabled) to prevent telemetry data from being "
            "transmitted in cleartext. Set to True explicitly for local development "
            "or when the collector is co-located and TLS is not required."
        ),
    )
    otlp_timeout_seconds: float = Field(
        default=10.0,
        gt=0.0,
        description="Timeout for OTLP exports in seconds",
    )
    retry_enabled: bool = Field(default=True, description="Retry failed OTLP exports")
    retry_max_attempts: int = Field(
        default=3,
        ge=1,
        description="Maximum OTLP export attempts (including the first attempt)",
    )
    retry_initial_backoff_seconds: float = Field(
        default=0.2,
        ge=0.0,
        description="Initial exponential backoff between OTLP retries in seconds",
    )
    retry_max_backoff_seconds: float = Field(
        default=5.0,
        gt=0.0,
        description="Maximum exponential backoff between OTLP retries in seconds",
    )
    metrics_export_interval_seconds: float = Field(
        default=30.0,
        gt=0.0,
        description="Periodic metric export interval in seconds",
    )
    langfuse: LangfuseSettings = Field(default_factory=LangfuseSettings)


class PostgresSettings(BaseModel):
    """PostgreSQL connection settings."""

    model_config = ConfigDict(frozen=True)

    dsn: SecretStr = Field(..., min_length=1, description="PostgreSQL connection string")
    min_pool_size: int = Field(default=1, ge=1, description="Minimum pool connections")
    max_pool_size: int = Field(default=10, ge=1, description="Maximum pool connections")
    command_timeout_seconds: float = Field(
        default=60.0, gt=0, description="Command timeout in seconds"
    )


class SqliteSettings(BaseModel):
    """SQLite connection settings."""

    model_config = ConfigDict(frozen=True)

    db_path: Path = Field(default=Path("data/app.db"), description="Path to SQLite database file")


class MinioSettings(BaseModel):
    """MinIO/S3 connection settings."""

    model_config = ConfigDict(frozen=True)

    endpoint: str = Field(..., min_length=1, description="MinIO endpoint")
    access_key: SecretStr = Field(..., min_length=1, description="Access key")
    secret_key: SecretStr = Field(..., min_length=1, description="Secret key")
    bucket: str = Field(default="orchid", min_length=1, description="Default bucket name")
    create_bucket_if_missing: bool = Field(
        default=False, description="Create bucket on startup when it is missing"
    )
    secure: bool = Field(
        default=True,
        description=(
            "Use HTTPS. Defaults to True for production safety. "
            "Set to False explicitly for local development or use local_dev()."
        ),
    )
    region: str | None = Field(default=None, description="AWS region")

    def to_s3_client_kwargs(self) -> dict[str, str | bool | None]:
        """Return kwargs compatible with S3-compatible clients like MinIO."""
        return {
            "endpoint": self.endpoint,
            "access_key": self.access_key.get_secret_value(),
            "secret_key": self.secret_key.get_secret_value(),
            "secure": self.secure,
            "region": self.region,
        }

    def presign_base_url(self) -> str:
        scheme = "https" if self.secure else "http"
        return f"{scheme}://{self.endpoint}"

    @classmethod
    def local_dev(
        cls,
        *,
        access_key: str,
        secret_key: str,
        bucket: str = "orchid-dev",
        endpoint: str = "localhost:9000",
        secure: bool = False,
        region: str | None = None,
        create_bucket_if_missing: bool = True,
    ) -> MinioSettings:
        """Build defaults that work with local docker-compose MinIO."""
        if os.getenv("ORCHID_ENV", "development") == "production":
            raise RuntimeError("local_dev() must not be used in production environments")
        warnings.warn("local_dev() is intended for local development only.", stacklevel=2)
        return cls(
            endpoint=endpoint,
            access_key=SecretStr(access_key),
            secret_key=SecretStr(secret_key),
            bucket=bucket,
            create_bucket_if_missing=create_bucket_if_missing,
            secure=secure,
            region=region,
        )


class RedisSettings(BaseModel):
    """Redis cache connection settings."""

    model_config = ConfigDict(frozen=True)

    url: SecretStr = Field(..., min_length=1, description="Redis connection URL")
    key_prefix: str = Field(default="", description="Optional key prefix")
    default_ttl_seconds: int | None = Field(
        default=None,
        ge=1,
        description="Default TTL for cached keys, omitted for no expiry",
    )
    encoding: str = Field(default="utf-8", min_length=1, description="Redis text encoding")
    decode_responses: bool = Field(
        default=True,
        description="Decode Redis responses to text values",
    )
    socket_timeout_seconds: float | None = Field(
        default=5.0,
        gt=0.0,
        description="Read/write socket timeout in seconds",
    )
    connect_timeout_seconds: float | None = Field(
        default=5.0,
        gt=0.0,
        description="Socket connect timeout in seconds",
    )
    health_check_interval_seconds: float = Field(
        default=15.0,
        ge=0.0,
        description="Background health check interval in seconds",
    )


class MongoDbSettings(BaseModel):
    """MongoDB connection settings."""

    model_config = ConfigDict(frozen=True)

    uri: SecretStr = Field(..., min_length=1, description="MongoDB connection URI")
    database: str = Field(..., min_length=1, description="Database name")
    server_selection_timeout_ms: int = Field(
        default=2000,
        ge=1,
        description="Server selection timeout in milliseconds",
    )
    connect_timeout_ms: int = Field(
        default=2000,
        ge=1,
        description="Socket connect timeout in milliseconds",
    )
    ping_timeout_seconds: float = Field(
        default=2.0,
        gt=0.0,
        description="Timeout for health ping in seconds",
    )
    app_name: str | None = Field(default=None, min_length=1, description="Optional app name")


class RabbitMqSettings(BaseModel):
    """RabbitMQ connection settings."""

    model_config = ConfigDict(frozen=True)

    url: SecretStr = Field(..., min_length=1, description="RabbitMQ connection URL")
    prefetch_count: int = Field(default=50, ge=1, description="Consumer prefetch count")
    connect_timeout_seconds: float = Field(
        default=10.0,
        gt=0.0,
        description="Connect timeout in seconds",
    )
    heartbeat_seconds: int = Field(
        default=60,
        ge=0,
        description="AMQP heartbeat interval in seconds",
    )
    publisher_confirms: bool = Field(
        default=True,
        description="Enable publisher confirms on channel",
    )
    startup_retry_attempts: int = Field(
        default=4,
        ge=1,
        description="Connection bootstrap attempts before failing",
    )
    startup_retry_initial_backoff_seconds: float = Field(
        default=0.25,
        gt=0.0,
        description="Initial retry backoff for startup attempts",
    )
    startup_retry_max_backoff_seconds: float = Field(
        default=3.0,
        gt=0.0,
        description="Maximum retry backoff for startup attempts",
    )


class QdrantSettings(BaseModel):
    """Qdrant vector database settings."""

    model_config = ConfigDict(frozen=True)

    url: str | None = Field(default=None, min_length=1, description="Qdrant base URL")
    host: str | None = Field(default=None, min_length=1, description="Qdrant host")
    port: int = Field(default=6333, ge=1, le=65535, description="Qdrant HTTP port")
    grpc_port: int = Field(default=6334, ge=1, le=65535, description="Qdrant gRPC port")
    use_ssl: bool = Field(
        default=True,
        description=(
            "Use HTTPS/TLS. Defaults to True for production safety. "
            "Set to False explicitly for local development."
        ),
    )
    api_key: SecretStr | None = Field(default=None, min_length=1, description="Qdrant API key")
    timeout_seconds: float = Field(default=10.0, gt=0.0, description="Request timeout")
    prefer_grpc: bool = Field(default=False, description="Prefer gRPC transport")
    collection_prefix: str = Field(default="", description="Collection name prefix")

    @model_validator(mode="after")
    def validate_url_or_host(self) -> QdrantSettings:
        if self.url is None and self.host is None:
            raise ValueError("Either url or host must be provided for Qdrant")
        return self


class R2Settings(BaseModel):
    """Cloudflare R2 settings using the S3-compatible API."""

    model_config = ConfigDict(frozen=True)

    access_key: SecretStr = Field(..., min_length=1, description="R2 access key")
    secret_key: SecretStr = Field(..., min_length=1, description="R2 secret key")
    bucket: str = Field(default="orchid", min_length=1, description="Default bucket name")
    account_id: str | None = Field(
        default=None, min_length=1, description="Cloudflare account id used to derive endpoint"
    )
    endpoint: str | None = Field(default=None, min_length=1, description="R2 endpoint override")
    create_bucket_if_missing: bool = Field(
        default=False, description="Create bucket on startup when it is missing"
    )
    secure: bool = Field(default=True, description="Use HTTPS")
    region: str = Field(default="auto", min_length=1, description="R2 region (usually auto)")

    @model_validator(mode="after")
    def validate_endpoint_or_account(self) -> R2Settings:
        if self.endpoint is None and self.account_id is None:
            raise ValueError("Either endpoint or account_id must be provided for R2")
        return self

    @property
    def resolved_endpoint(self) -> str:
        if self.endpoint:
            return self.endpoint
        if self.account_id is None:
            raise ValueError("Either endpoint or account_id must be provided for R2")
        return _r2_endpoint_from_account(self.account_id)

    def to_s3_client_kwargs(self) -> dict[str, str | bool]:
        """Return kwargs compatible with S3-compatible clients like MinIO."""
        return {
            "endpoint": self.resolved_endpoint,
            "access_key": self.access_key.get_secret_value(),
            "secret_key": self.secret_key.get_secret_value(),
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


class MultiBucketSettings(BaseModel):
    """Multi-bucket blob storage settings with logical alias mapping.

    Allows consumers to reference buckets by logical names (e.g., 'videos', 'chunks')
    instead of physical bucket names, enabling easy bucket management and renaming.
    """

    model_config = ConfigDict(frozen=True)

    endpoint: str = Field(..., min_length=1, description="S3-compatible endpoint")
    access_key: SecretStr = Field(..., min_length=1, description="Access key")
    secret_key: SecretStr = Field(..., min_length=1, description="Secret key")
    buckets: dict[str, str] = Field(
        ..., min_length=1, description="Mapping of logical aliases to bucket names"
    )
    create_buckets_if_missing: bool = Field(
        default=False, description="Create buckets on startup when missing"
    )
    secure: bool = Field(
        default=True,
        description=(
            "Use HTTPS. Defaults to True for production safety. "
            "Set to False explicitly for local development or use local_dev()."
        ),
    )
    region: str | None = Field(default=None, description="AWS region")

    def get_bucket(self, alias: str) -> str:
        """Resolve alias to physical bucket name."""
        if alias not in self.buckets:
            raise KeyError(f"Unknown bucket alias: {alias!r}")
        return self.buckets[alias]

    def to_s3_client_kwargs(self) -> dict[str, str | bool | None]:
        """Return kwargs compatible with S3-compatible clients like MinIO."""
        return {
            "endpoint": self.endpoint,
            "access_key": self.access_key.get_secret_value(),
            "secret_key": self.secret_key.get_secret_value(),
            "secure": self.secure,
            "region": self.region,
        }

    def presign_base_url(self) -> str:
        scheme = "https" if self.secure else "http"
        return f"{scheme}://{self.endpoint}"

    @classmethod
    def local_dev(
        cls,
        *,
        access_key: str,
        secret_key: str,
        buckets: dict[str, str] | None = None,
        endpoint: str = "localhost:9000",
        secure: bool = False,
        region: str | None = None,
        create_buckets_if_missing: bool = True,
    ) -> MultiBucketSettings:
        """Build defaults that work with local docker-compose MinIO."""
        if os.getenv("ORCHID_ENV", "development") == "production":
            raise RuntimeError("local_dev() must not be used in production environments")
        warnings.warn("local_dev() is intended for local development only.", stacklevel=2)
        default_buckets = buckets or {"default": "orchid-dev"}
        return cls(
            endpoint=endpoint,
            access_key=SecretStr(access_key),
            secret_key=SecretStr(secret_key),
            buckets=default_buckets,
            create_buckets_if_missing=create_buckets_if_missing,
            secure=secure,
            region=region,
        )


class PgVectorSettings(BaseModel):
    """pgvector extension settings."""

    model_config = ConfigDict(frozen=True)

    table: str = Field(default="embeddings", min_length=1, description="Table name")
    dimensions: int = Field(default=1536, ge=1, description="Vector dimensions")
    distance_metric: str = Field(default="cosine", min_length=1, description="Distance metric")
    ivfflat_lists: int = Field(default=100, ge=1, description="IVFFlat index lists")


class ResourceSettings(BaseModel):
    """External resource connections."""

    model_config = ConfigDict(frozen=True)

    postgres: PostgresSettings | None = Field(default=None)
    sqlite: SqliteSettings | None = Field(default=None)
    redis: RedisSettings | None = Field(default=None)
    mongodb: MongoDbSettings | None = Field(default=None)
    rabbitmq: RabbitMqSettings | None = Field(default=None)
    qdrant: QdrantSettings | None = Field(default=None)
    minio: MinioSettings | None = Field(default=None)
    r2: R2Settings | None = Field(default=None)
    pgvector: PgVectorSettings | None = Field(default=None)
    multi_bucket: MultiBucketSettings | None = Field(default=None)

    @classmethod
    def from_env(cls, prefix: str = "ORCHID_") -> ResourceSettings:
        """Build settings from environment variables.

        Each resource is constructed only when its required env vars are set.
        Optional fields fall back to class defaults when the env var is absent.
        """

        def env(name: str) -> str | None:
            return os.getenv(f"{prefix}{name}")

        def env_bool(name: str, default: bool = False) -> bool:
            value = env(name)
            if value is None:
                return default
            return value.strip().lower() in {"1", "true", "yes", "on"}

        def _parse_int(name: str, value: str) -> int:
            try:
                return int(value)
            except ValueError as int_error:
                raise ValueError(
                    f"Invalid {prefix}{name}={value!r}: expected integer"
                ) from int_error

        def _parse_float(name: str, value: str) -> float:
            try:
                return float(value)
            except ValueError as float_error:
                raise ValueError(
                    f"Invalid {prefix}{name}={value!r}: expected float"
                ) from float_error

        def env_int(name: str, default: int) -> int:
            value = env(name)
            if value is None:
                return default
            return _parse_int(name, value)

        def env_optional_int(name: str) -> int | None:
            value = env(name)
            if value is None:
                return None
            return _parse_int(name, value)

        def env_float(name: str, default: float) -> float:
            value = env(name)
            if value is None:
                return default
            return _parse_float(name, value)

        def env_optional_float(name: str) -> float | None:
            value = env(name)
            if value is None:
                return None
            return _parse_float(name, value)

        sqlite = None
        sqlite_path = env("SQLITE_DB_PATH")
        if sqlite_path:
            sqlite = SqliteSettings(db_path=Path(sqlite_path))

        postgres = None
        postgres_dsn = env("POSTGRES_DSN")
        if postgres_dsn:
            postgres = PostgresSettings(
                dsn=SecretStr(postgres_dsn),
                min_pool_size=env_int("POSTGRES_MIN_POOL_SIZE", 1),
                max_pool_size=env_int("POSTGRES_MAX_POOL_SIZE", 10),
                command_timeout_seconds=env_float("POSTGRES_COMMAND_TIMEOUT_SECONDS", 60.0),
            )

        redis = None
        redis_url = env("REDIS_URL")
        if redis_url:
            redis = RedisSettings(
                url=SecretStr(redis_url),
                key_prefix=env("REDIS_KEY_PREFIX") or "",
                default_ttl_seconds=env_optional_int("REDIS_DEFAULT_TTL_SECONDS"),
                encoding=env("REDIS_ENCODING") or "utf-8",
                decode_responses=env_bool("REDIS_DECODE_RESPONSES", True),
                socket_timeout_seconds=env_optional_float("REDIS_SOCKET_TIMEOUT_SECONDS"),
                connect_timeout_seconds=env_optional_float("REDIS_CONNECT_TIMEOUT_SECONDS"),
                health_check_interval_seconds=env_float(
                    "REDIS_HEALTH_CHECK_INTERVAL_SECONDS", 15.0
                ),
            )

        mongodb = None
        mongodb_uri = env("MONGODB_URI")
        mongodb_database = env("MONGODB_DATABASE")
        if mongodb_uri and mongodb_database:
            mongodb = MongoDbSettings(
                uri=SecretStr(mongodb_uri),
                database=mongodb_database,
                server_selection_timeout_ms=env_int("MONGODB_SERVER_SELECTION_TIMEOUT_MS", 2000),
                connect_timeout_ms=env_int("MONGODB_CONNECT_TIMEOUT_MS", 2000),
                ping_timeout_seconds=env_float("MONGODB_PING_TIMEOUT_SECONDS", 2.0),
                app_name=env("MONGODB_APP_NAME"),
            )

        rabbitmq = None
        rabbitmq_url = env("RABBITMQ_URL")
        if rabbitmq_url:
            rabbitmq = RabbitMqSettings(
                url=SecretStr(rabbitmq_url),
                prefetch_count=env_int("RABBITMQ_PREFETCH_COUNT", 50),
                connect_timeout_seconds=env_float("RABBITMQ_CONNECT_TIMEOUT_SECONDS", 10.0),
                heartbeat_seconds=env_int("RABBITMQ_HEARTBEAT_SECONDS", 60),
                publisher_confirms=env_bool("RABBITMQ_PUBLISHER_CONFIRMS", True),
                startup_retry_attempts=env_int("RABBITMQ_STARTUP_RETRY_ATTEMPTS", 4),
                startup_retry_initial_backoff_seconds=env_float(
                    "RABBITMQ_STARTUP_RETRY_INITIAL_BACKOFF_SECONDS", 0.25
                ),
                startup_retry_max_backoff_seconds=env_float(
                    "RABBITMQ_STARTUP_RETRY_MAX_BACKOFF_SECONDS", 3.0
                ),
            )

        qdrant = None
        qdrant_url = env("QDRANT_URL")
        qdrant_host = env("QDRANT_HOST")
        if qdrant_url or qdrant_host:
            qdrant = QdrantSettings(
                url=qdrant_url,
                host=qdrant_host,
                port=env_int("QDRANT_PORT", 6333),
                grpc_port=env_int("QDRANT_GRPC_PORT", 6334),
                use_ssl=env_bool("QDRANT_USE_SSL", True),
                api_key=SecretStr(raw) if (raw := env("QDRANT_API_KEY")) else None,
                timeout_seconds=env_float("QDRANT_TIMEOUT_SECONDS", 10.0),
                prefer_grpc=env_bool("QDRANT_PREFER_GRPC", False),
                collection_prefix=env("QDRANT_COLLECTION_PREFIX") or "",
            )

        minio = None
        minio_endpoint = env("MINIO_ENDPOINT")
        minio_access_key = env("MINIO_ACCESS_KEY")
        minio_secret_key = env("MINIO_SECRET_KEY")
        if minio_endpoint and minio_access_key and minio_secret_key:
            minio = MinioSettings(
                endpoint=minio_endpoint,
                access_key=SecretStr(minio_access_key),
                secret_key=SecretStr(minio_secret_key),
                bucket=env("MINIO_BUCKET") or "orchid",
                create_bucket_if_missing=env_bool("MINIO_CREATE_BUCKET_IF_MISSING", False),
                secure=env_bool("MINIO_SECURE", True),
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
                access_key=SecretStr(r2_access_key),
                secret_key=SecretStr(r2_secret_key),
                bucket=env("R2_BUCKET") or "orchid",
                create_bucket_if_missing=env_bool("R2_CREATE_BUCKET_IF_MISSING", False),
                secure=env_bool("R2_SECURE", True),
                region=env("R2_REGION") or "auto",
            )

        pgvector = None
        if postgres_dsn:
            pgvector = PgVectorSettings(
                table=env("PGVECTOR_TABLE") or "embeddings",
                dimensions=env_int("PGVECTOR_DIMENSIONS", 1536),
                distance_metric=env("PGVECTOR_DISTANCE_METRIC") or "cosine",
                ivfflat_lists=env_int("PGVECTOR_IVFFLAT_LISTS", 100),
            )

        multi_bucket = None
        mb_endpoint = env("MULTI_BUCKET_ENDPOINT")
        mb_access_key = env("MULTI_BUCKET_ACCESS_KEY")
        mb_secret_key = env("MULTI_BUCKET_SECRET_KEY")
        mb_buckets_json = env("MULTI_BUCKET_BUCKETS")
        if mb_endpoint and mb_access_key and mb_secret_key and mb_buckets_json:
            try:
                buckets = json.loads(mb_buckets_json)
            except (json.JSONDecodeError, ValueError) as json_error:
                raise ValueError(
                    f"MULTI_BUCKET_BUCKETS must be valid JSON: {json_error}"
                ) from json_error
            if not isinstance(buckets, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in buckets.items()
            ):
                raise ValueError(
                    "MULTI_BUCKET_BUCKETS must be a JSON object mapping "
                    "string aliases to string bucket names"
                )
            multi_bucket = MultiBucketSettings(
                endpoint=mb_endpoint,
                access_key=SecretStr(mb_access_key),
                secret_key=SecretStr(mb_secret_key),
                buckets=buckets,
                create_buckets_if_missing=env_bool("MULTI_BUCKET_CREATE_BUCKETS_IF_MISSING", False),
                secure=env_bool("MULTI_BUCKET_SECURE", True),
                region=env("MULTI_BUCKET_REGION"),
            )

        return cls(
            sqlite=sqlite,
            postgres=postgres,
            redis=redis,
            mongodb=mongodb,
            rabbitmq=rabbitmq,
            qdrant=qdrant,
            minio=minio,
            r2=r2,
            pgvector=pgvector,
            multi_bucket=multi_bucket,
        )


ResourcesSettings = ResourceSettings


class AppSettings(BaseModel):
    """Root application settings."""

    model_config = ConfigDict(frozen=True)

    service: ServiceSettings
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    observability: ObservabilitySettings = Field(default_factory=ObservabilitySettings)
    resources: ResourceSettings = Field(default_factory=ResourceSettings)
