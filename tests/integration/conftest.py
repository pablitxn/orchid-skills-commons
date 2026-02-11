"""Reusable fixtures for end-to-end integration tests."""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from contextlib import suppress
from urllib.request import urlopen
from uuid import uuid4

import pytest

from orchid_commons.config.resources import (
    MinioSettings,
    MongoDbSettings,
    MultiBucketSettings,
    PostgresSettings,
    QdrantSettings,
    RabbitMqSettings,
    RedisSettings,
    SqliteSettings,
)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _require_docker() -> None:
    docker = pytest.importorskip("docker")
    try:
        client = docker.from_env()
        client.ping()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"Docker is not available for integration tests: {exc}")


def _wait_for_http_ready(url: str, timeout_seconds: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=1.0) as response:
                if response.status < 500:
                    return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError(f"Timed out waiting for service readiness: {url}")


@pytest.fixture
def sqlite_settings(tmp_path) -> SqliteSettings:
    return SqliteSettings(db_path=tmp_path / "integration.sqlite3")


@pytest.fixture(scope="session")
def postgres_dsn() -> Iterator[str]:
    pytest.importorskip("asyncpg")

    external_dsn = os.getenv("ORCHID_POSTGRES_DSN")
    if external_dsn:
        yield external_dsn
        return

    _require_docker()
    testcontainers_postgres = pytest.importorskip("testcontainers.postgres")
    PostgresContainer = testcontainers_postgres.PostgresContainer
    container = PostgresContainer(os.getenv("ORCHID_POSTGRES_IMAGE", "postgres:16-alpine"))

    try:
        container.start()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"Could not start postgres container: {exc}")

    try:
        dsn = container.get_connection_url()
        yield dsn.replace("postgresql+psycopg2://", "postgresql://", 1)
    finally:
        with suppress(Exception):
            container.stop()


@pytest.fixture
def postgres_settings(postgres_dsn: str) -> PostgresSettings:
    return PostgresSettings(
        dsn=postgres_dsn,
        min_pool_size=1,
        max_pool_size=3,
        command_timeout_seconds=1.0,
    )


@pytest.fixture(scope="session")
def minio_settings() -> Iterator[MinioSettings]:
    pytest.importorskip("minio")

    endpoint = os.getenv("ORCHID_MINIO_ENDPOINT")
    access_key = os.getenv("ORCHID_MINIO_ACCESS_KEY", "minioadmin")
    secret_key = os.getenv("ORCHID_MINIO_SECRET_KEY", "minioadmin")
    bucket = os.getenv("ORCHID_MINIO_BUCKET", f"orchid-integration-{uuid4().hex[:8]}")
    secure = _env_bool("ORCHID_MINIO_SECURE", False)
    region = os.getenv("ORCHID_MINIO_REGION")

    if endpoint:
        yield MinioSettings(
            endpoint=endpoint,
            access_key=access_key,
            secret_key=secret_key,
            bucket=bucket,
            create_bucket_if_missing=True,
            secure=secure,
            region=region,
        )
        return

    _require_docker()
    DockerContainer = pytest.importorskip("testcontainers.core.container").DockerContainer
    image = os.getenv("ORCHID_MINIO_IMAGE", "minio/minio:latest")
    container = (
        DockerContainer(image)
        .with_env("MINIO_ROOT_USER", access_key)
        .with_env("MINIO_ROOT_PASSWORD", secret_key)
        .with_exposed_ports(9000)
        .with_command("server /data --address :9000")
    )

    try:
        container.start()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"Could not start MinIO container: {exc}")

    try:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(9000)
        resolved_endpoint = f"{host}:{port}"
        _wait_for_http_ready(f"http://{resolved_endpoint}/minio/health/live")

        yield MinioSettings(
            endpoint=resolved_endpoint,
            access_key=access_key,
            secret_key=secret_key,
            bucket=bucket,
            create_bucket_if_missing=True,
            secure=False,
            region=region,
        )
    finally:
        with suppress(Exception):
            container.stop()


@pytest.fixture(scope="session")
def multi_bucket_settings(minio_settings: MinioSettings) -> MultiBucketSettings:
    """Build MultiBucketSettings from the same MinIO instance used for single-bucket tests."""
    unique_suffix = uuid4().hex[:8]
    return MultiBucketSettings(
        endpoint=minio_settings.endpoint,
        access_key=minio_settings.access_key,
        secret_key=minio_settings.secret_key,
        buckets={
            "videos": f"integration-videos-{unique_suffix}",
            "chunks": f"integration-chunks-{unique_suffix}",
            "frames": f"integration-frames-{unique_suffix}",
        },
        create_buckets_if_missing=True,
        secure=minio_settings.secure,
        region=minio_settings.region,
    )


@pytest.fixture(scope="session")
def qdrant_settings() -> Iterator[QdrantSettings]:
    pytest.importorskip("qdrant_client")

    url = os.getenv("ORCHID_QDRANT_URL")
    host = os.getenv("ORCHID_QDRANT_HOST")
    port = int(os.getenv("ORCHID_QDRANT_PORT", "6333"))
    grpc_port = int(os.getenv("ORCHID_QDRANT_GRPC_PORT", "6334"))
    use_ssl = _env_bool("ORCHID_QDRANT_USE_SSL", False)
    api_key = os.getenv("ORCHID_QDRANT_API_KEY")
    timeout_seconds = float(os.getenv("ORCHID_QDRANT_TIMEOUT_SECONDS", "10.0"))
    prefer_grpc = _env_bool("ORCHID_QDRANT_PREFER_GRPC", False)
    collection_prefix = os.getenv("ORCHID_QDRANT_COLLECTION_PREFIX", f"orchid-it-{uuid4().hex[:8]}")

    if url or host:
        yield QdrantSettings(
            url=url,
            host=host,
            port=port,
            grpc_port=grpc_port,
            use_ssl=use_ssl,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            prefer_grpc=prefer_grpc,
            collection_prefix=collection_prefix,
        )
        return

    _require_docker()
    DockerContainer = pytest.importorskip("testcontainers.core.container").DockerContainer
    image = os.getenv("ORCHID_QDRANT_IMAGE", "qdrant/qdrant:v1.16.2")
    container = DockerContainer(image).with_exposed_ports(6333, 6334)

    try:
        container.start()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"Could not start qdrant container: {exc}")

    try:
        resolved_host = container.get_container_host_ip()
        resolved_port = int(container.get_exposed_port(6333))
        resolved_grpc_port = int(container.get_exposed_port(6334))
        _wait_for_http_ready(f"http://{resolved_host}:{resolved_port}/collections")

        yield QdrantSettings(
            host=resolved_host,
            port=resolved_port,
            grpc_port=resolved_grpc_port,
            use_ssl=False,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            prefer_grpc=prefer_grpc,
            collection_prefix=collection_prefix,
        )
    finally:
        with suppress(Exception):
            container.stop()


# -- Redis fixtures --


@pytest.fixture(scope="session")
def redis_settings() -> Iterator[RedisSettings]:
    external_url = os.getenv("ORCHID_REDIS_URL")
    if external_url:
        yield RedisSettings(url=external_url)
        return

    _require_docker()
    DockerContainer = pytest.importorskip("testcontainers.core.container").DockerContainer
    image = os.getenv("ORCHID_REDIS_IMAGE", "redis:7-alpine")
    container = DockerContainer(image).with_exposed_ports(6379)

    try:
        container.start()
    except Exception as exc:
        pytest.skip(f"Could not start Redis container: {exc}")

    try:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(6379)
        yield RedisSettings(url=f"redis://{host}:{port}/0")
    finally:
        with suppress(Exception):
            container.stop()


# -- MongoDB fixtures --


@pytest.fixture(scope="session")
def mongodb_settings() -> Iterator[MongoDbSettings]:
    external_uri = os.getenv("ORCHID_MONGODB_URI")
    external_db = os.getenv("ORCHID_MONGODB_DATABASE", "orchid_integration_test")
    if external_uri:
        yield MongoDbSettings(uri=external_uri, database=external_db)
        return

    _require_docker()
    DockerContainer = pytest.importorskip("testcontainers.core.container").DockerContainer
    image = os.getenv("ORCHID_MONGODB_IMAGE", "mongo:7")
    container = DockerContainer(image).with_exposed_ports(27017)

    try:
        container.start()
    except Exception as exc:
        pytest.skip(f"Could not start MongoDB container: {exc}")

    try:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(27017)
        yield MongoDbSettings(
            uri=f"mongodb://{host}:{port}",
            database=external_db,
        )
    finally:
        with suppress(Exception):
            container.stop()


# -- RabbitMQ fixtures --


@pytest.fixture(scope="session")
def rabbitmq_settings() -> Iterator[RabbitMqSettings]:
    external_url = os.getenv("ORCHID_RABBITMQ_URL")
    if external_url:
        yield RabbitMqSettings(url=external_url)
        return

    _require_docker()
    DockerContainer = pytest.importorskip("testcontainers.core.container").DockerContainer
    image = os.getenv("ORCHID_RABBITMQ_IMAGE", "rabbitmq:3-alpine")
    container = DockerContainer(image).with_exposed_ports(5672)

    try:
        container.start()
    except Exception as exc:
        pytest.skip(f"Could not start RabbitMQ container: {exc}")

    try:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(5672)
        # Wait for AMQP protocol handshake readiness (not only open TCP port).
        import socket

        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            try:
                with socket.create_connection((host, int(port)), timeout=1.0) as sock:
                    sock.settimeout(1.0)
                    sock.sendall(b"AMQP\x00\x00\x09\x01")
                    if sock.recv(8).startswith(b"AMQP"):
                        break
            except OSError:
                time.sleep(0.3)
        else:
            pytest.skip("RabbitMQ AMQP handshake did not become ready within 30 seconds")

        yield RabbitMqSettings(
            url=f"amqp://guest:guest@{host}:{port}/",
            connect_timeout_seconds=10.0,
        )
    finally:
        with suppress(Exception):
            container.stop()
