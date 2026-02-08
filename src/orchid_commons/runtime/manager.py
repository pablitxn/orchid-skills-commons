"""Central registry for shared resource connections."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from time import perf_counter
from typing import TYPE_CHECKING, Any, cast

from orchid_commons.observability.metrics import MetricsRecorder, get_metrics_recorder
from orchid_commons.runtime.errors import (
    MissingRequiredResourceError,
    ResourceNotFoundError,
    ShutdownError,
)
from orchid_commons.runtime.health import (
    HealthCheck,
    HealthReport,
    HealthStatus,
    aggregate_health_checks,
)

if TYPE_CHECKING:
    from orchid_commons.config.resources import ResourceSettings


@dataclass(slots=True)
class ResourceManager:
    """Stores resource instances and closes them gracefully.

    Example usage::

        manager = ResourceManager()

        # Register resources manually
        manager.register("db", db_pool)
        manager.register("cache", redis_client)

        # Or bootstrap from settings
        await manager.startup(settings, required=["sqlite"])

        # Access resources
        db = manager.get("db")

        # Shutdown all resources (accumulates errors)
        await manager.close_all()
    """

    _resources: dict[str, Any] = field(default_factory=dict)
    _metrics: MetricsRecorder | None = None

    def register(self, name: str, resource: Any) -> None:
        """Register a resource by name."""
        self._resources[name] = resource

    def has(self, name: str) -> bool:
        """Check if a resource is registered."""
        return name in self._resources

    def get(self, name: str) -> Any:
        """Retrieve a registered resource by name."""
        if name not in self._resources:
            raise ResourceNotFoundError(f"Resource not found: {name}")
        return self._resources[name]

    async def health_report(
        self,
        *,
        timeout_seconds: float | None = None,
        include_optional_checks: bool = True,
        observability_handle: Any | None = None,
        langfuse_client: Any | None = None,
    ) -> HealthReport:
        """Aggregate health checks for registered resources and optional observability backends."""
        checks = self._resource_health_checks()
        if include_optional_checks:
            checks.update(
                _optional_health_checks(
                    observability_handle=observability_handle,
                    langfuse_client=langfuse_client,
                )
            )
        return await aggregate_health_checks(checks, timeout_seconds=timeout_seconds)

    async def health_payload(
        self,
        *,
        timeout_seconds: float | None = None,
        include_optional_checks: bool = True,
        observability_handle: Any | None = None,
        langfuse_client: Any | None = None,
    ) -> dict[str, Any]:
        """Return a JSON-serializable health payload suitable for `/health` endpoints."""
        report = await self.health_report(
            timeout_seconds=timeout_seconds,
            include_optional_checks=include_optional_checks,
            observability_handle=observability_handle,
            langfuse_client=langfuse_client,
        )
        return report.to_dict()

    async def startup(
        self,
        settings: ResourceSettings,
        *,
        required: list[str] | None = None,
    ) -> None:
        """Initialize resources from settings configuration.

        Args:
            settings: ResourceSettings with configured resource options.
            required: List of resource names that must be initialized.
                      Raises MissingRequiredResourceError if any are missing.
        """
        started = perf_counter()
        try:
            await bootstrap_resources(settings, self)

            if required:
                missing = [name for name in required if not self.has(name)]
                if missing:
                    raise MissingRequiredResourceError(
                        f"Required resources not configured: {', '.join(missing)}"
                    )
        except Exception as exc:
            self._metrics_recorder().observe_operation(
                resource="runtime",
                operation="startup",
                duration_seconds=perf_counter() - started,
                success=False,
            )
            self._metrics_recorder().observe_error(
                resource="runtime",
                operation="startup",
                error_type=type(exc).__name__,
            )
            raise

        self._metrics_recorder().observe_operation(
            resource="runtime",
            operation="startup",
            duration_seconds=perf_counter() - started,
            success=True,
        )

    async def close_all(self) -> None:
        """Close all registered resources, accumulating any errors.

        Attempts to close every resource regardless of individual failures.
        If any resources fail to close, raises ShutdownError with all errors.
        """
        started = perf_counter()
        errors: dict[str, Exception] = {}
        try:
            for name, resource in self._resources.items():
                try:
                    close = getattr(resource, "close", None)
                    if close is None:
                        continue
                    maybe_awaitable = close()
                    if hasattr(maybe_awaitable, "__await__"):
                        await maybe_awaitable
                except Exception as exc:
                    errors[name] = exc

            self._resources.clear()

            if errors:
                raise ShutdownError(errors)
        except Exception as exc:
            self._metrics_recorder().observe_operation(
                resource="runtime",
                operation="shutdown",
                duration_seconds=perf_counter() - started,
                success=False,
            )
            self._metrics_recorder().observe_error(
                resource="runtime",
                operation="shutdown",
                error_type=type(exc).__name__,
            )
            raise

        self._metrics_recorder().observe_operation(
            resource="runtime",
            operation="shutdown",
            duration_seconds=perf_counter() - started,
            success=True,
        )

    def _metrics_recorder(self) -> MetricsRecorder:
        return get_metrics_recorder() if self._metrics is None else self._metrics

    def _resource_health_checks(self) -> dict[str, HealthCheck]:
        checks: dict[str, HealthCheck] = {}
        for name, resource in self._resources.items():
            health_check = getattr(resource, "health_check", None)
            if callable(health_check):
                checks[name] = cast(HealthCheck, health_check)
        return checks


ResourceFactory = Callable[..., Coroutine[Any, Any, Any]]

_RESOURCE_FACTORIES: dict[str, tuple[str, ResourceFactory]] = {}
_BUILTIN_FACTORIES_REGISTERED = False


def _optional_health_checks(
    *,
    observability_handle: Any | None,
    langfuse_client: Any | None,
) -> dict[str, HealthCheck]:
    checks: dict[str, HealthCheck] = {}

    resolved_observability = (
        _active_observability_handle() if observability_handle is None else observability_handle
    )
    if getattr(resolved_observability, "enabled", False):
        checks["otel"] = _threaded_health_check(
            _check_otel_health,
            resolved_observability,
        )

    resolved_langfuse = _active_langfuse_client() if langfuse_client is None else langfuse_client
    langfuse_settings = getattr(resolved_langfuse, "settings", None)
    if getattr(langfuse_settings, "enabled", False):
        checks["langfuse"] = _threaded_health_check(
            _check_langfuse_health,
            resolved_langfuse,
        )

    return checks


def _active_observability_handle() -> Any | None:
    try:
        from orchid_commons.observability.otel import get_observability_handle
    except Exception:
        return None
    return get_observability_handle()


def _threaded_health_check(
    check: Callable[[Any], HealthStatus],
    target: Any,
) -> HealthCheck:
    async def _run() -> HealthStatus:
        return await asyncio.to_thread(check, target)

    return _run


def _active_langfuse_client() -> Any | None:
    try:
        from orchid_commons.observability.langfuse import get_default_langfuse_client
    except Exception:
        return None
    return get_default_langfuse_client()


def _check_otel_health(handle: Any) -> HealthStatus:
    started = perf_counter()
    endpoint = getattr(handle, "otlp_endpoint", None)
    details = {"otlp_endpoint": str(endpoint)} if endpoint else None

    has_providers = (
        getattr(handle, "tracer_provider", None) is not None
        or getattr(handle, "meter_provider", None) is not None
    )
    message = (
        "OpenTelemetry providers are active"
        if has_providers
        else "OpenTelemetry is enabled but providers are unavailable"
    )
    return HealthStatus(
        healthy=has_providers,
        latency_ms=(perf_counter() - started) * 1000,
        message=message,
        details=details,
    )


def _check_langfuse_health(client: Any) -> HealthStatus:
    started = perf_counter()
    disabled_reason = getattr(client, "disabled_reason", None)

    try:
        flush = getattr(client, "flush", None)
        if callable(flush):
            flush()
    except Exception as exc:
        return HealthStatus(
            healthy=False,
            latency_ms=(perf_counter() - started) * 1000,
            message=f"Langfuse health check failed: {exc}",
            details={"error_type": type(exc).__name__},
        )

    healthy = bool(getattr(client, "enabled", False))
    details = {"disabled_reason": str(disabled_reason)} if disabled_reason else None
    message = (
        "Langfuse client is active"
        if healthy
        else "Langfuse is enabled by config but client is unavailable"
    )
    return HealthStatus(
        healthy=healthy,
        latency_ms=(perf_counter() - started) * 1000,
        message=message,
        details=details,
    )


def register_factory(
    name: str,
    settings_attr: str,
    factory: ResourceFactory,
) -> None:
    """Register a factory function for bootstrapping a resource type.

    Args:
        name: The resource name to register (e.g., "sqlite", "postgres").
        settings_attr: Attribute name on ResourceSettings to check for config.
        factory: Async function that takes settings and returns a resource.
    """
    _RESOURCE_FACTORIES[name] = (settings_attr, factory)


def _ensure_builtin_factories() -> None:
    """Register built-in resource factories once."""
    global _BUILTIN_FACTORIES_REGISTERED
    if _BUILTIN_FACTORIES_REGISTERED:
        return

    from orchid_commons.blob.minio import create_minio_profile
    from orchid_commons.blob.router import create_multi_bucket_router
    from orchid_commons.db import (
        create_mongodb_resource,
        create_postgres_provider,
        create_qdrant_vector_store,
        create_rabbitmq_broker,
        create_redis_cache,
        create_sqlite_resource,
    )

    if "sqlite" not in _RESOURCE_FACTORIES:
        register_factory("sqlite", "sqlite", create_sqlite_resource)
    if "postgres" not in _RESOURCE_FACTORIES:
        register_factory("postgres", "postgres", create_postgres_provider)
    if "redis" not in _RESOURCE_FACTORIES:
        register_factory("redis", "redis", create_redis_cache)
    if "mongodb" not in _RESOURCE_FACTORIES:
        register_factory("mongodb", "mongodb", create_mongodb_resource)
    if "rabbitmq" not in _RESOURCE_FACTORIES:
        register_factory("rabbitmq", "rabbitmq", create_rabbitmq_broker)
    if "qdrant" not in _RESOURCE_FACTORIES:
        register_factory("qdrant", "qdrant", create_qdrant_vector_store)
    if "minio" not in _RESOURCE_FACTORIES:
        register_factory("minio", "minio", create_minio_profile)
    if "multi_bucket" not in _RESOURCE_FACTORIES:
        register_factory("multi_bucket", "multi_bucket", create_multi_bucket_router)
    _BUILTIN_FACTORIES_REGISTERED = True


async def bootstrap_resources(
    settings: ResourceSettings,
    manager: ResourceManager,
) -> None:
    """Initialize resources based on settings configuration.

    Iterates through registered factories and initializes any resource
    whose settings are configured (not None).

    Args:
        settings: ResourceSettings instance with resource configurations.
        manager: ResourceManager to register initialized resources.
    """
    _ensure_builtin_factories()

    for name, (settings_attr, factory) in _RESOURCE_FACTORIES.items():
        resource_settings = getattr(settings, settings_attr, None)
        if resource_settings is not None:
            resource = await factory(resource_settings)
            manager.register(name, resource)
