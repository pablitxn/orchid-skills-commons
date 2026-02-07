"""Tests for ResourceManager."""

import pytest

from orchid_commons import ResourceManager
from orchid_commons.runtime import manager as manager_module
from orchid_commons.runtime.errors import ResourceNotFoundError


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

    def test_builtin_factories_include_data_and_queue_resources(self) -> None:
        original_factories = dict(manager_module._RESOURCE_FACTORIES)
        original_registered = manager_module._BUILTIN_FACTORIES_REGISTERED
        try:
            manager_module._RESOURCE_FACTORIES.clear()
            manager_module._BUILTIN_FACTORIES_REGISTERED = False
            manager_module._ensure_builtin_factories()

            assert {
                "sqlite",
                "postgres",
                "redis",
                "mongodb",
                "rabbitmq",
                "qdrant",
                "minio",
            }.issubset(
                manager_module._RESOURCE_FACTORIES.keys()
            )
        finally:
            manager_module._RESOURCE_FACTORIES.clear()
            manager_module._RESOURCE_FACTORIES.update(original_factories)
            manager_module._BUILTIN_FACTORIES_REGISTERED = original_registered
