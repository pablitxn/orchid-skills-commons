"""Tests for ResourceManager."""

import pytest

from orchid_commons import ResourceManager
from orchid_commons.errors import ResourceNotFoundError


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
