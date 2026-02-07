"""Custom exceptions for orchid commons resources."""


class OrchidCommonsError(Exception):
    """Base exception for this package."""


class MissingDependencyError(OrchidCommonsError):
    """Raised when an optional dependency is required but not installed."""


class ResourceNotFoundError(OrchidCommonsError):
    """Raised when requesting an unknown resource from the manager."""


class InvalidResourceNameError(OrchidCommonsError):
    """Raised when a SQL identifier/resource name is invalid."""


class MissingRequiredResourceError(OrchidCommonsError):
    """Raised when a required resource was not initialized during bootstrap."""


class ShutdownError(OrchidCommonsError):
    """Raised when one or more resources fail to close during shutdown."""

    def __init__(self, errors: dict[str, Exception]) -> None:
        self.errors = errors
        names = ", ".join(errors.keys())
        super().__init__(f"Failed to close resources: {names}")
