"""Reusable observability mixin for resource classes."""

from __future__ import annotations

from time import perf_counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orchid_commons.observability.metrics import MetricsRecorder


class ObservableMixin:
    """Mixin providing ``_observe_operation``, ``_observe_error``, and ``_metrics_recorder``.

    Subclasses must set ``_resource_name`` (class-level or instance attribute)
    and may optionally provide ``_metrics`` (instance attribute) to override the
    global metrics recorder.

    Works with both regular classes and ``@dataclass(slots=True)`` (use
    ``ClassVar[str]`` for ``_resource_name`` in the latter case).
    """

    _resource_name: str
    _metrics: MetricsRecorder | None

    def _metrics_recorder(self) -> MetricsRecorder:
        from orchid_commons.observability.metrics import get_metrics_recorder

        return get_metrics_recorder() if self._metrics is None else self._metrics

    def _observe_operation(self, operation: str, started: float, *, success: bool) -> None:
        self._metrics_recorder().observe_operation(
            resource=self._resource_name,
            operation=operation,
            duration_seconds=perf_counter() - started,
            success=success,
        )

    def _observe_error(self, operation: str, started: float, exc: Exception) -> None:
        self._observe_operation(operation, started, success=False)
        self._metrics_recorder().observe_error(
            resource=self._resource_name,
            operation=operation,
            error_type=type(exc).__name__,
        )
