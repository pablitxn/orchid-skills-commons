"""Tests for ResourceManager."""

import asyncio
from time import perf_counter
from unittest.mock import AsyncMock, MagicMock

import pytest

from orchid_commons import ResourceManager
from orchid_commons.runtime import manager as manager_module
from orchid_commons.runtime.errors import ResourceNotFoundError, ShutdownError
from orchid_commons.runtime.manager import reset_resource_factories


class TestResourceManager:
    def test_register_and_get(self) -> None:
        manager = ResourceManager()
        manager.register("test", "value")

        assert manager.has("test")
        assert manager.get("test") == "value"

    def test_has_returns_false_for_missing(self) -> None:
        manager = ResourceManager()

        assert not manager.has("missing")

    def test_get_raises_for_missing(self) -> None:
        manager = ResourceManager()

        with pytest.raises(ResourceNotFoundError):
            manager.get("missing")

    async def test_close_all_clears_resources(self) -> None:
        manager = ResourceManager()
        manager.register("test", "value")

        await manager.close_all()

        assert not manager.has("test")

    async def test_startup_rolls_back_on_partial_failure(self) -> None:
        manager = ResourceManager()

        # Pre-register two resources with mock close methods
        res_a = MagicMock()
        res_a.close = AsyncMock()
        res_b = MagicMock()
        res_b.close = AsyncMock()
        manager.register("a", res_a)
        manager.register("b", res_b)

        # Patch bootstrap_resources to simulate a third factory failing
        async def _failing_bootstrap(settings: object, mgr: ResourceManager) -> None:
            raise RuntimeError("factory exploded")

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(manager_module, "bootstrap_resources", _failing_bootstrap)
        try:
            with pytest.raises(RuntimeError, match="factory exploded"):
                await manager.startup(MagicMock())

            # Both previously registered resources should have been closed
            res_a.close.assert_awaited_once()
            res_b.close.assert_awaited_once()

            # _resources should be empty after rollback
            assert not manager._resources
        finally:
            monkeypatch.undo()

    async def test_close_all_retains_resources_that_failed(self) -> None:
        manager = ResourceManager()

        good = MagicMock()
        good.close = AsyncMock()

        bad = MagicMock()
        bad.close = AsyncMock(side_effect=RuntimeError("close failed"))

        manager.register("good", good)
        manager.register("bad", bad)

        with pytest.raises(ShutdownError) as exc_info:
            await manager.close_all()

        # The successfully closed resource should be removed
        assert not manager.has("good")
        # The failed resource should remain for potential retry
        assert manager.has("bad")
        assert "bad" in exc_info.value.errors

    def test_builtin_factories_include_data_and_queue_resources(self) -> None:
        original_factories = dict(manager_module._RESOURCE_FACTORIES)
        original_registered = manager_module._BUILTIN_FACTORIES_REGISTERED
        try:
            reset_resource_factories()
            manager_module._ensure_builtin_factories()

            assert {
                "sqlite",
                "postgres",
                "redis",
                "mongodb",
                "rabbitmq",
                "qdrant",
                "minio",
                "r2",
            }.issubset(
                manager_module._RESOURCE_FACTORIES.keys()
            )
        finally:
            reset_resource_factories()
            manager_module._RESOURCE_FACTORIES.update(original_factories)
            manager_module._BUILTIN_FACTORIES_REGISTERED = original_registered

    async def test_bootstrap_resources_runs_factories_in_parallel(self) -> None:
        original_factories = dict(manager_module._RESOURCE_FACTORIES)
        original_registered = manager_module._BUILTIN_FACTORIES_REGISTERED
        try:
            reset_resource_factories()
            manager_module._BUILTIN_FACTORIES_REGISTERED = True  # skip builtin registration

            async def _slow_factory(settings: object) -> str:
                await asyncio.sleep(0.1)
                return f"resource-{id(settings)}"

            settings = MagicMock()
            for name in ("res_a", "res_b", "res_c"):
                manager_module.register_factory(name, name, _slow_factory)
                setattr(settings, name, MagicMock())

            mgr = ResourceManager()
            start = perf_counter()
            await manager_module.bootstrap_resources(settings, mgr)
            elapsed = perf_counter() - start

            # Sequential would take >= 0.3s; parallel should be ~0.1s
            assert elapsed < 0.25, f"Expected parallel execution, took {elapsed:.3f}s"
            assert mgr.has("res_a")
            assert mgr.has("res_b")
            assert mgr.has("res_c")
        finally:
            reset_resource_factories()
            manager_module._RESOURCE_FACTORIES.update(original_factories)
            manager_module._BUILTIN_FACTORIES_REGISTERED = original_registered

    async def test_bootstrap_resources_registers_successes_on_partial_failure(self) -> None:
        original_factories = dict(manager_module._RESOURCE_FACTORIES)
        original_registered = manager_module._BUILTIN_FACTORIES_REGISTERED
        try:
            reset_resource_factories()
            manager_module._BUILTIN_FACTORIES_REGISTERED = True

            async def _ok_factory(settings: object) -> str:
                return "ok"

            async def _bad_factory(settings: object) -> str:
                raise RuntimeError("factory exploded")

            settings = MagicMock()
            manager_module.register_factory("good_a", "good_a", _ok_factory)
            manager_module.register_factory("bad", "bad", _bad_factory)
            manager_module.register_factory("good_b", "good_b", _ok_factory)
            for attr in ("good_a", "bad", "good_b"):
                setattr(settings, attr, MagicMock())

            mgr = ResourceManager()
            with pytest.raises(RuntimeError, match="factory exploded"):
                await manager_module.bootstrap_resources(settings, mgr)

            # Successful resources should still be registered for cleanup
            assert mgr.has("good_a")
            assert mgr.has("good_b")
            assert not mgr.has("bad")
        finally:
            reset_resource_factories()
            manager_module._RESOURCE_FACTORIES.update(original_factories)
            manager_module._BUILTIN_FACTORIES_REGISTERED = original_registered

    def test_reset_resource_factories_clears_state(self) -> None:
        original_factories = dict(manager_module._RESOURCE_FACTORIES)
        original_registered = manager_module._BUILTIN_FACTORIES_REGISTERED
        try:
            # Ensure builtins are loaded
            manager_module._ensure_builtin_factories()
            assert manager_module._BUILTIN_FACTORIES_REGISTERED is True
            assert len(manager_module._RESOURCE_FACTORIES) > 0

            reset_resource_factories()

            assert manager_module._BUILTIN_FACTORIES_REGISTERED is False
            assert len(manager_module._RESOURCE_FACTORIES) == 0
        finally:
            reset_resource_factories()
            manager_module._RESOURCE_FACTORIES.update(original_factories)
            manager_module._BUILTIN_FACTORIES_REGISTERED = original_registered
