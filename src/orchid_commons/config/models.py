"""Typed configuration models with Pydantic validation."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def _r2_endpoint_from_account(account_id: str) -> str:
    return f"{account_id}.r2.cloudflarestorage.com"


class ServiceSettings(BaseModel):
    """Service identification and network settings."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(..., min_length=1, description="Service name")
    version: str = Field(..., min_length=1, description="Service version")
    host: str = Field(default="0.0.0.0", description="Bind host")
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
    public_key: str | None = Field(default=None, min_length=1, description="Langfuse public key")
    secret_key: str | None = Field(default=None, min_length=1, description="Langfuse secret key")
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
        default=True,
        description="Use insecure gRPC OTLP transport (disable TLS)",
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

    dsn: str = Field(..., min_length=1, description="PostgreSQL connection string")
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
    access_key: str = Field(..., min_length=1, description="Access key")
    secret_key: str = Field(..., min_length=1, description="Secret key")
    bucket: str = Field(default="orchid", min_length=1, description="Default bucket name")
    create_bucket_if_missing: bool = Field(
        default=False, description="Create bucket on startup when it is missing"
    )
    secure: bool = Field(default=False, description="Use HTTPS")
    region: str | None = Field(default=None, description="AWS region")

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


class RedisSettings(BaseModel):
    """Redis cache connection settings."""

    model_config = ConfigDict(frozen=True)

    url: str = Field(..., min_length=1, description="Redis connection URL")
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

    uri: str = Field(..., min_length=1, description="MongoDB connection URI")
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

    url: str = Field(..., min_length=1, description="RabbitMQ connection URL")
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


class QdrantSettings(BaseModel):
    """Qdrant vector database settings."""

    model_config = ConfigDict(frozen=True)

    url: str | None = Field(default=None, min_length=1, description="Qdrant base URL")
    host: str | None = Field(default=None, min_length=1, description="Qdrant host")
    port: int = Field(default=6333, ge=1, le=65535, description="Qdrant HTTP port")
    grpc_port: int = Field(default=6334, ge=1, le=65535, description="Qdrant gRPC port")
    use_ssl: bool = Field(default=False, description="Use HTTPS/TLS")
    api_key: str | None = Field(default=None, min_length=1, description="Qdrant API key")
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

    access_key: str = Field(..., min_length=1, description="R2 access key")
    secret_key: str = Field(..., min_length=1, description="R2 secret key")
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
            "access_key": self.access_key,
            "secret_key": self.secret_key,
            "secure": self.secure,
            "region": self.region,
        }

    def presign_base_url(self) -> str:
        scheme = "https" if self.secure else "http"
        return f"{scheme}://{self.resolved_endpoint}"


class ResourcesSettings(BaseModel):
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


class AppSettings(BaseModel):
    """Root application settings."""

    model_config = ConfigDict(frozen=True)

    service: ServiceSettings
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    observability: ObservabilitySettings = Field(default_factory=ObservabilitySettings)
    resources: ResourcesSettings = Field(default_factory=ResourcesSettings)
