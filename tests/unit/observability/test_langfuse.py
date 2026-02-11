"""Tests for Langfuse client helpers and wrappers."""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any

import pytest

import orchid_commons.observability.langfuse as langfuse_module
from orchid_commons.config import AppSettings
from orchid_commons.observability.langfuse import (
    LangfuseClientSettings,
    create_langfuse_client,
    get_default_langfuse_client,
    reset_default_langfuse_client,
    set_default_langfuse_client,
)
from orchid_commons.runtime.errors import MissingDependencyError


class FakeObservation:
    def __init__(self) -> None:
        self.update_calls: list[dict[str, Any]] = []

    def update(self, **kwargs: Any) -> None:
        self.update_calls.append(kwargs)


class FakeObservationContext(AbstractContextManager[FakeObservation]):
    def __init__(self, observation: FakeObservation) -> None:
        self._observation = observation

    def __enter__(self) -> FakeObservation:
        return self._observation

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: Any,
    ) -> None:
        del exc_type, exc, tb
        return None


class FakeLangfuseSdkClient:
    def __init__(self) -> None:
        self.start_calls: list[dict[str, Any]] = []
        self.current_trace_id: str | None = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        self.current_observation_id: str | None = None
        self.current_trace_updates: list[dict[str, Any]] = []
        self.current_span_updates: list[dict[str, Any]] = []
        self.current_generation_updates: list[dict[str, Any]] = []
        self.flush_calls = 0
        self.shutdown_calls = 0
        self.observation = FakeObservation()

    def start_as_current_observation(self, **kwargs: Any) -> FakeObservationContext:
        self.start_calls.append(kwargs)
        self.observation = FakeObservation()
        return FakeObservationContext(self.observation)

    def get_current_trace_id(self) -> str | None:
        return self.current_trace_id

    def get_current_observation_id(self) -> str | None:
        return self.current_observation_id

    def update_current_trace(self, **kwargs: Any) -> None:
        self.current_trace_updates.append(kwargs)

    def update_current_span(self, **kwargs: Any) -> None:
        self.current_span_updates.append(kwargs)

    def update_current_generation(self, **kwargs: Any) -> None:
        self.current_generation_updates.append(kwargs)

    def flush(self) -> None:
        self.flush_calls += 1

    def shutdown(self) -> None:
        self.shutdown_calls += 1


def _build_enabled_settings() -> LangfuseClientSettings:
    return LangfuseClientSettings(
        enabled=True,
        public_key="pk-live",
        secret_key="sk-live",
    )


@pytest.fixture(autouse=True)
def _reset_default_client() -> None:
    set_default_langfuse_client(None)
    yield
    set_default_langfuse_client(None)


def test_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("TEST_LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("TEST_LANGFUSE_SECRET_KEY", "sk")
    monkeypatch.setenv("TEST_LANGFUSE_BASE_URL", "https://example.langfuse.test")
    monkeypatch.setenv("TEST_LANGFUSE_SAMPLE_RATE", "0.25")
    monkeypatch.setenv("TEST_LANGFUSE_FLUSH_AT", "100")

    settings = LangfuseClientSettings.from_env(prefix="TEST_LANGFUSE_")

    assert settings.enabled is True
    assert settings.public_key == "pk"
    assert settings.secret_key == "sk"
    assert settings.base_url == "https://example.langfuse.test"
    assert settings.sample_rate == 0.25
    assert settings.flush_at == 100


def test_settings_from_app_settings() -> None:
    app_settings = AppSettings.model_validate(
        {
            "service": {"name": "svc", "version": "1.0.0"},
            "observability": {
                "enabled": True,
                "langfuse": {
                    "enabled": True,
                    "public_key": "pk",
                    "secret_key": "sk",
                    "environment": "staging",
                },
            },
        }
    )

    settings = LangfuseClientSettings.from_app_settings(app_settings)

    assert settings.enabled is True
    assert settings.public_key == "pk"
    assert settings.secret_key == "sk"
    assert settings.environment == "staging"


def test_settings_from_app_settings_observability_disabled() -> None:
    app_settings = AppSettings.model_validate(
        {
            "service": {"name": "svc", "version": "1.0.0"},
            "observability": {
                "enabled": False,
                "langfuse": {
                    "enabled": True,
                    "public_key": "pk",
                    "secret_key": "sk",
                },
            },
        }
    )

    settings = LangfuseClientSettings.from_app_settings(app_settings)

    assert settings.enabled is False


def test_create_client_is_noop_when_disabled() -> None:
    client = create_langfuse_client(
        settings=LangfuseClientSettings(
            enabled=False,
            public_key="pk",
            secret_key="sk",
        )
    )

    assert client.enabled is False
    assert client.disabled_reason == "disabled by configuration"

    with client.start_span(name="noop") as observation:
        observation.update(output={"ok": True})

    client.flush()
    client.shutdown()


def test_create_client_registers_default_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = FakeLangfuseSdkClient()

    monkeypatch.setattr(
        langfuse_module,
        "_build_langfuse_sdk_client",
        lambda settings: fake_client,
    )

    client = create_langfuse_client(settings=_build_enabled_settings())

    assert get_default_langfuse_client() is client


def test_create_client_can_skip_default_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = FakeLangfuseSdkClient()

    monkeypatch.setattr(
        langfuse_module,
        "_build_langfuse_sdk_client",
        lambda settings: fake_client,
    )

    client = create_langfuse_client(
        settings=_build_enabled_settings(),
        register_as_default=False,
    )

    assert client.enabled is True
    assert get_default_langfuse_client() is None


def test_create_client_is_noop_when_missing_credentials() -> None:
    client = create_langfuse_client(
        settings=LangfuseClientSettings(
            enabled=True,
            public_key=None,
            secret_key=None,
        )
    )

    assert client.enabled is False
    assert client.disabled_reason is not None
    assert "missing Langfuse credentials" in client.disabled_reason


def test_create_client_is_noop_when_dependency_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise_missing_dependency() -> type[Any]:
        raise MissingDependencyError("langfuse not installed")

    monkeypatch.setattr(
        langfuse_module,
        "_import_langfuse_client_class",
        _raise_missing_dependency,
    )

    client = create_langfuse_client(settings=_build_enabled_settings())

    assert client.enabled is False
    assert client.disabled_reason == "langfuse not installed"


def test_start_span_uses_otel_trace_id_for_trace_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = FakeLangfuseSdkClient()
    fake_client.current_observation_id = None

    monkeypatch.setattr(
        langfuse_module,
        "_build_langfuse_sdk_client",
        lambda settings: fake_client,
    )
    monkeypatch.setattr(
        langfuse_module,
        "_current_otel_trace_id",
        lambda: "4bf92f3577b34da6a3ce929d0e0e4736",
    )

    client = create_langfuse_client(settings=_build_enabled_settings())

    with client.start_span(name="workflow.step", metadata={"team": "orchid"}) as observation:
        observation.update(output="ok")

    assert len(fake_client.start_calls) == 1
    payload = fake_client.start_calls[0]
    assert payload["name"] == "workflow.step"
    assert payload["as_type"] == "span"
    assert payload["trace_context"] == {"trace_id": "4bf92f3577b34da6a3ce929d0e0e4736"}
    assert payload["metadata"]["team"] == "orchid"
    assert payload["metadata"]["otel.trace_id"] == "4bf92f3577b34da6a3ce929d0e0e4736"


def test_start_span_does_not_attach_trace_context_when_observation_is_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = FakeLangfuseSdkClient()
    fake_client.current_observation_id = "obs_123"

    monkeypatch.setattr(
        langfuse_module,
        "_build_langfuse_sdk_client",
        lambda settings: fake_client,
    )
    monkeypatch.setattr(
        langfuse_module,
        "_current_otel_trace_id",
        lambda: "4bf92f3577b34da6a3ce929d0e0e4736",
    )

    client = create_langfuse_client(settings=_build_enabled_settings())

    with client.start_span(name="child.span"):
        pass

    payload = fake_client.start_calls[0]
    assert "trace_context" not in payload


def test_observe_generation_decorator_sync_captures_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = FakeLangfuseSdkClient()

    monkeypatch.setattr(
        langfuse_module,
        "_build_langfuse_sdk_client",
        lambda settings: fake_client,
    )

    client = create_langfuse_client(settings=_build_enabled_settings())

    @client.observe_generation(name="llm.answer", model="gpt-4.1-mini")
    def run(prompt: str) -> str:
        return prompt.upper()

    result = run("hola")

    assert result == "HOLA"
    payload = fake_client.start_calls[0]
    assert payload["as_type"] == "generation"
    assert payload["model"] == "gpt-4.1-mini"
    assert payload["input"]["args"] == ["hola"]
    assert fake_client.observation.update_calls[-1]["output"] == "HOLA"


def test_observe_span_decorator_marks_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = FakeLangfuseSdkClient()

    monkeypatch.setattr(
        langfuse_module,
        "_build_langfuse_sdk_client",
        lambda settings: fake_client,
    )

    client = create_langfuse_client(settings=_build_enabled_settings())

    @client.observe_span(name="step.fail")
    def fail() -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        fail()

    assert fake_client.observation.update_calls[-1]["level"] == "ERROR"
    assert "RuntimeError: boom" in fake_client.observation.update_calls[-1]["status_message"]


@pytest.mark.asyncio
async def test_observe_generation_decorator_async(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = FakeLangfuseSdkClient()

    monkeypatch.setattr(
        langfuse_module,
        "_build_langfuse_sdk_client",
        lambda settings: fake_client,
    )

    client = create_langfuse_client(settings=_build_enabled_settings())

    @client.observe_generation(name="llm.async", model="gpt-4.1-mini")
    async def run(value: int) -> int:
        return value + 1

    result = await run(41)

    assert result == 42
    assert fake_client.start_calls[0]["name"] == "llm.async"
    assert fake_client.observation.update_calls[-1]["output"] == 42


def test_update_helpers_proxy_to_underlying_client(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = FakeLangfuseSdkClient()

    monkeypatch.setattr(
        langfuse_module,
        "_build_langfuse_sdk_client",
        lambda settings: fake_client,
    )

    client = create_langfuse_client(settings=_build_enabled_settings())

    client.update_current_trace(user_id="u-123")
    client.update_current_span(level="DEFAULT")
    client.update_current_generation(usage_details={"input": 10, "output": 20})
    client.flush()
    client.shutdown()

    assert fake_client.current_trace_updates == [{"user_id": "u-123"}]
    assert fake_client.current_span_updates == [{"level": "DEFAULT"}]
    assert fake_client.current_generation_updates == [
        {"usage_details": {"input": 10, "output": 20}}
    ]
    assert fake_client.flush_calls == 1
    assert fake_client.shutdown_calls == 1


def test_reset_default_langfuse_client_clears_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = FakeLangfuseSdkClient()

    monkeypatch.setattr(
        langfuse_module,
        "_build_langfuse_sdk_client",
        lambda settings: fake_client,
    )

    create_langfuse_client(settings=_build_enabled_settings())
    assert get_default_langfuse_client() is not None

    reset_default_langfuse_client()
    assert get_default_langfuse_client() is None
