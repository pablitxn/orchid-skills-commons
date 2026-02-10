"""Langfuse tracing client helpers with safe fallbacks and decorators."""

from __future__ import annotations

import inspect
import json
import os
from collections.abc import Callable, Mapping
from contextlib import nullcontext
from dataclasses import dataclass
from functools import wraps
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypeVar, cast

from orchid_commons.runtime.errors import MissingDependencyError

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

    from orchid_commons.config.models import AppSettings

ObservationType = Literal["span", "generation"]
_T = TypeVar("_T")
_DEFAULT_LANGFUSE_CLIENT: LangfuseClient | None = None


class LangfuseObservation(Protocol):
    """Minimal mutable observation contract used by wrappers."""

    def update(self, **kwargs: Any) -> Any: ...


class LangfuseClientProtocol(Protocol):
    """Langfuse SDK methods used by this module."""

    def start_as_current_observation(self, **kwargs: Any) -> AbstractContextManager[Any]: ...

    def get_current_trace_id(self) -> str | None: ...

    def get_current_observation_id(self) -> str | None: ...

    def update_current_trace(self, **kwargs: Any) -> Any: ...

    def update_current_span(self, **kwargs: Any) -> Any: ...

    def update_current_generation(self, **kwargs: Any) -> Any: ...

    def flush(self) -> Any: ...

    def shutdown(self) -> Any: ...


class _NoopObservation:
    def update(self, **kwargs: Any) -> None:
        del kwargs


@dataclass(slots=True, frozen=True)
class LangfuseClientSettings:
    """Runtime settings for Langfuse client creation."""

    enabled: bool = True
    public_key: str | None = None
    secret_key: str | None = None
    base_url: str = "https://cloud.langfuse.com"
    environment: str | None = None
    release: str | None = None
    timeout_seconds: int = 5
    flush_at: int = 512
    flush_interval_seconds: float = 5.0
    sample_rate: float = 1.0
    debug: bool = False

    def __post_init__(self) -> None:
        if self.timeout_seconds < 1:
            raise ValueError("timeout_seconds must be >= 1")
        if self.flush_at < 1:
            raise ValueError("flush_at must be >= 1")
        if self.flush_interval_seconds <= 0:
            raise ValueError("flush_interval_seconds must be > 0")
        if not 0.0 <= self.sample_rate <= 1.0:
            raise ValueError("sample_rate must be between 0.0 and 1.0")

    @property
    def has_credentials(self) -> bool:
        return bool(self.public_key and self.secret_key)

    @classmethod
    def from_env(cls, prefix: str = "ORCHID_LANGFUSE_") -> LangfuseClientSettings:
        """Build Langfuse settings from environment variables."""

        def env(name: str) -> str | None:
            return os.getenv(f"{prefix}{name}")

        def env_bool(name: str, default: bool) -> bool:
            value = env(name)
            if value is None:
                return default
            return value.strip().lower() in {"1", "true", "yes", "on"}

        return cls(
            enabled=env_bool("ENABLED", True),
            public_key=env("PUBLIC_KEY"),
            secret_key=env("SECRET_KEY"),
            base_url=env("BASE_URL") or "https://cloud.langfuse.com",
            environment=env("ENVIRONMENT"),
            release=env("RELEASE"),
            timeout_seconds=int(env("TIMEOUT_SECONDS") or 5),
            flush_at=int(env("FLUSH_AT") or 512),
            flush_interval_seconds=float(env("FLUSH_INTERVAL_SECONDS") or 5.0),
            sample_rate=float(env("SAMPLE_RATE") or 1.0),
            debug=env_bool("DEBUG", False),
        )

    @classmethod
    def from_app_settings(cls, app_settings: AppSettings) -> LangfuseClientSettings:
        """Build runtime settings from typed appsettings."""
        langfuse = app_settings.observability.langfuse
        return cls(
            enabled=bool(app_settings.observability.enabled and langfuse.enabled),
            public_key=langfuse.public_key.get_secret_value() if langfuse.public_key else None,
            secret_key=langfuse.secret_key.get_secret_value() if langfuse.secret_key else None,
            base_url=langfuse.base_url,
            environment=langfuse.environment,
            release=langfuse.release,
            timeout_seconds=langfuse.timeout_seconds,
            flush_at=langfuse.flush_at,
            flush_interval_seconds=langfuse.flush_interval_seconds,
            sample_rate=langfuse.sample_rate,
            debug=langfuse.debug,
        )


class LangfuseClient:
    """Safe wrapper over Langfuse SDK with no-op fallback mode."""

    def __init__(
        self,
        *,
        settings: LangfuseClientSettings,
        client: LangfuseClientProtocol | None = None,
        disabled_reason: str | None = None,
    ) -> None:
        self._settings = settings
        self._client = client
        self._disabled_reason = disabled_reason
        self._noop_observation = _NoopObservation()

    @property
    def settings(self) -> LangfuseClientSettings:
        return self._settings

    @property
    def enabled(self) -> bool:
        return self._client is not None

    @property
    def disabled_reason(self) -> str | None:
        return self._disabled_reason

    def get_current_trace_id(self) -> str | None:
        if self._client is not None:
            try:
                return self._client.get_current_trace_id()
            except Exception:
                return _current_otel_trace_id()
        return _current_otel_trace_id()

    def flush(self) -> None:
        if self._client is None:
            return
        self._client.flush()

    def shutdown(self) -> None:
        if self._client is None:
            return
        self._client.shutdown()

    def update_current_trace(self, **kwargs: Any) -> None:
        if self._client is None:
            return
        self._client.update_current_trace(**kwargs)

    def update_current_span(self, **kwargs: Any) -> None:
        if self._client is None:
            return
        self._client.update_current_span(**kwargs)

    def update_current_generation(self, **kwargs: Any) -> None:
        if self._client is None:
            return
        self._client.update_current_generation(**kwargs)

    def start_span(
        self,
        *,
        name: str,
        input: Any | None = None,
        output: Any | None = None,
        metadata: Mapping[str, Any] | None = None,
        trace_id: str | None = None,
        **kwargs: Any,
    ) -> AbstractContextManager[Any]:
        return self._start_observation(
            as_type="span",
            name=name,
            input=input,
            output=output,
            metadata=metadata,
            trace_id=trace_id,
            **kwargs,
        )

    def start_generation(
        self,
        *,
        name: str,
        input: Any | None = None,
        output: Any | None = None,
        metadata: Mapping[str, Any] | None = None,
        model: str | None = None,
        model_parameters: Mapping[str, Any] | None = None,
        usage_details: Mapping[str, Any] | None = None,
        cost_details: Mapping[str, Any] | None = None,
        trace_id: str | None = None,
        **kwargs: Any,
    ) -> AbstractContextManager[Any]:
        extra: dict[str, Any] = dict(kwargs)
        if model is not None:
            extra["model"] = model
        if model_parameters is not None:
            extra["model_parameters"] = _normalize_payload(dict(model_parameters))
        if usage_details is not None:
            extra["usage_details"] = _normalize_payload(dict(usage_details))
        if cost_details is not None:
            extra["cost_details"] = _normalize_payload(dict(cost_details))

        return self._start_observation(
            as_type="generation",
            name=name,
            input=input,
            output=output,
            metadata=metadata,
            trace_id=trace_id,
            **extra,
        )

    def observe_span(
        self,
        *,
        name: str | None = None,
        capture_input: bool = True,
        capture_output: bool = True,
        metadata: Mapping[str, Any] | None = None,
    ) -> Callable[[Callable[..., _T]], Callable[..., _T]]:
        return self._observe(
            as_type="span",
            name=name,
            capture_input=capture_input,
            capture_output=capture_output,
            metadata=metadata,
        )

    def observe_generation(
        self,
        *,
        name: str | None = None,
        model: str | None = None,
        capture_input: bool = True,
        capture_output: bool = True,
        metadata: Mapping[str, Any] | None = None,
    ) -> Callable[[Callable[..., _T]], Callable[..., _T]]:
        return self._observe(
            as_type="generation",
            name=name,
            capture_input=capture_input,
            capture_output=capture_output,
            metadata=metadata,
            model=model,
        )

    def _start_observation(
        self,
        *,
        as_type: ObservationType,
        name: str,
        input: Any | None,
        output: Any | None,
        metadata: Mapping[str, Any] | None,
        trace_id: str | None,
        **kwargs: Any,
    ) -> AbstractContextManager[Any]:
        if self._client is None:
            return nullcontext(self._noop_observation)

        payload: dict[str, Any] = {
            "name": name,
            "as_type": as_type,
        }

        normalized_metadata = _build_metadata(metadata)
        if normalized_metadata is not None:
            payload["metadata"] = normalized_metadata

        if input is not None:
            payload["input"] = _normalize_payload(input)
        if output is not None:
            payload["output"] = _normalize_payload(output)

        resolved_trace_id = trace_id or _current_otel_trace_id()
        if resolved_trace_id and self._should_attach_trace_context():
            payload["trace_context"] = {"trace_id": resolved_trace_id}

        payload.update(kwargs)
        return self._client.start_as_current_observation(**payload)

    def _should_attach_trace_context(self) -> bool:
        if self._client is None:
            return False
        try:
            return self._client.get_current_observation_id() is None
        except Exception:
            return False

    def _observe(
        self,
        *,
        as_type: ObservationType,
        name: str | None,
        capture_input: bool,
        capture_output: bool,
        metadata: Mapping[str, Any] | None,
        model: str | None = None,
    ) -> Callable[[Callable[..., _T]], Callable[..., _T]]:
        def decorator(func: Callable[..., _T]) -> Callable[..., _T]:
            observation_name = name or f"{func.__module__}.{func.__qualname__}"

            if inspect.iscoroutinefunction(func):

                @wraps(func)
                async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                    observation_input = _capture_call_input(args, kwargs) if capture_input else None
                    start_kwargs: dict[str, Any] = {}
                    if as_type == "generation" and model is not None:
                        start_kwargs["model"] = model

                    with self._start_observation(
                        as_type=as_type,
                        name=observation_name,
                        input=observation_input,
                        output=None,
                        metadata=metadata,
                        trace_id=None,
                        **start_kwargs,
                    ) as observation:
                        try:
                            result = await cast(Callable[..., Any], func)(*args, **kwargs)
                        except Exception as exc:
                            _mark_observation_error(observation, exc)
                            raise
                        if capture_output:
                            _safe_observation_update(
                                observation,
                                output=_normalize_payload(result),
                            )
                        return result

                return cast(Callable[..., _T], async_wrapper)

            @wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                observation_input = _capture_call_input(args, kwargs) if capture_input else None
                start_kwargs: dict[str, Any] = {}
                if as_type == "generation" and model is not None:
                    start_kwargs["model"] = model

                with self._start_observation(
                    as_type=as_type,
                    name=observation_name,
                    input=observation_input,
                    output=None,
                    metadata=metadata,
                    trace_id=None,
                    **start_kwargs,
                ) as observation:
                    try:
                        result = cast(Callable[..., Any], func)(*args, **kwargs)
                    except Exception as exc:
                        _mark_observation_error(observation, exc)
                        raise
                    if capture_output:
                        _safe_observation_update(
                            observation,
                            output=_normalize_payload(result),
                        )
                    return result

            return cast(Callable[..., _T], sync_wrapper)

        return decorator


def get_default_langfuse_client() -> LangfuseClient | None:
    """Return the process-wide default Langfuse client, if one was registered."""
    return _DEFAULT_LANGFUSE_CLIENT


def set_default_langfuse_client(client: LangfuseClient | None) -> None:
    """Set or clear the process-wide default Langfuse client."""
    global _DEFAULT_LANGFUSE_CLIENT
    _DEFAULT_LANGFUSE_CLIENT = client


def reset_default_langfuse_client() -> None:
    """Clear the process-wide default Langfuse client."""
    global _DEFAULT_LANGFUSE_CLIENT
    _DEFAULT_LANGFUSE_CLIENT = None


def create_langfuse_client(
    *,
    settings: LangfuseClientSettings | None = None,
    app_settings: AppSettings | None = None,
    env_prefix: str = "ORCHID_LANGFUSE_",
    register_as_default: bool = True,
) -> LangfuseClient:
    """Create Langfuse client wrapper from explicit settings, appsettings, or env vars."""
    resolved_settings = _resolve_settings(
        settings=settings,
        app_settings=app_settings,
        env_prefix=env_prefix,
    )

    if not resolved_settings.enabled:
        client = LangfuseClient(
            settings=resolved_settings,
            client=None,
            disabled_reason="disabled by configuration",
        )
    elif not resolved_settings.has_credentials:
        client = LangfuseClient(
            settings=resolved_settings,
            client=None,
            disabled_reason="missing Langfuse credentials (public_key/secret_key)",
        )
    else:
        try:
            sdk_client = _build_langfuse_sdk_client(resolved_settings)
        except MissingDependencyError as exc:
            client = LangfuseClient(
                settings=resolved_settings,
                client=None,
                disabled_reason=str(exc),
            )
        else:
            client = LangfuseClient(
                settings=resolved_settings,
                client=sdk_client,
                disabled_reason=None,
            )

    if register_as_default:
        set_default_langfuse_client(client)
    return client


def _resolve_settings(
    *,
    settings: LangfuseClientSettings | None,
    app_settings: AppSettings | None,
    env_prefix: str,
) -> LangfuseClientSettings:
    if settings is not None:
        return settings
    if app_settings is not None:
        return LangfuseClientSettings.from_app_settings(app_settings)
    return LangfuseClientSettings.from_env(prefix=env_prefix)


def _build_langfuse_sdk_client(settings: LangfuseClientSettings) -> LangfuseClientProtocol:
    client_class = _import_langfuse_client_class()
    return cast(
        LangfuseClientProtocol,
        client_class(
            public_key=settings.public_key,
            secret_key=settings.secret_key,
            host=settings.base_url,
            enabled=settings.enabled,
            timeout=settings.timeout_seconds,
            flush_at=settings.flush_at,
            flush_interval=settings.flush_interval_seconds,
            environment=settings.environment,
            release=settings.release,
            sample_rate=settings.sample_rate,
            debug=settings.debug,
        ),
    )


def _import_langfuse_client_class() -> type[Any]:
    try:
        from langfuse import Langfuse
    except ImportError as exc:  # pragma: no cover - exercised when extras are absent
        raise MissingDependencyError(
            "Langfuse tracing requires optional dependency 'langfuse'. "
            "Install with: uv sync --extra observability"
        ) from exc
    return Langfuse


def _build_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any] | None:
    normalized: dict[str, Any] = {}
    if metadata is not None:
        normalized.update(_normalize_metadata(metadata))
    otel_trace_id = _current_otel_trace_id()
    if otel_trace_id is not None:
        normalized.setdefault("otel.trace_id", otel_trace_id)
    return normalized or None


def _normalize_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): _normalize_payload(value) for key, value in metadata.items()}


def _capture_call_input(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {"args": [_normalize_payload(value) for value in args]}
    if kwargs:
        payload["kwargs"] = {str(key): _normalize_payload(value) for key, value in kwargs.items()}
    return payload


def _safe_observation_update(observation: Any, **kwargs: Any) -> None:
    update = getattr(observation, "update", None)
    if update is None or not callable(update):
        return
    try:
        update(**kwargs)
    except Exception:
        # Never break business logic because tracing failed.
        return


def _mark_observation_error(observation: Any, error: Exception) -> None:
    _safe_observation_update(
        observation,
        level="ERROR",
        status_message=f"{type(error).__name__}: {error}",
    )


def _normalize_payload(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, Mapping):
        return {str(key): _normalize_payload(sub_value) for key, sub_value in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_normalize_payload(item) for item in value]

    try:
        json.dumps(value)
        return value
    except TypeError:
        return repr(value)


def _current_otel_trace_id() -> str | None:
    try:
        from opentelemetry import trace as otel_trace
    except Exception:
        return None

    span = otel_trace.get_current_span()
    span_context = span.get_span_context()
    if not span_context.is_valid or span_context.trace_id == 0:
        return None
    return f"{span_context.trace_id:032x}"
