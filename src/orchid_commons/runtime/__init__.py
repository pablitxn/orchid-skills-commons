"""Runtime primitives: lifecycle, health and error contracts."""

from orchid_commons.runtime.errors import (
    InvalidResourceNameError,
    MissingDependencyError,
    MissingRequiredResourceError,
    OrchidCommonsError,
    ResourceNotFoundError,
    ShutdownError,
)
from orchid_commons.runtime.health import (
    HealthCheck,
    HealthReport,
    HealthStatus,
    HealthSummary,
    Resource,
    aggregate_health_checks,
)
from orchid_commons.runtime.manager import (
    ResourceFactory,
    ResourceManager,
    bootstrap_resources,
    register_factory,
)

__all__ = [
    "HealthCheck",
    "HealthReport",
    "HealthStatus",
    "HealthSummary",
    "InvalidResourceNameError",
    "MissingDependencyError",
    "MissingRequiredResourceError",
    "OrchidCommonsError",
    "Resource",
    "ResourceFactory",
    "ResourceManager",
    "ResourceNotFoundError",
    "ShutdownError",
    "aggregate_health_checks",
    "bootstrap_resources",
    "register_factory",
]
