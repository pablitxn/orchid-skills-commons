"""End-to-end integration tests for the PostgreSQL provider."""

from __future__ import annotations

import asyncio

import pytest

from orchid_commons import ResourceManager
from orchid_commons.config.resources import ResourceSettings
from orchid_commons.db import PostgresProvider, create_postgres_provider

pytestmark = pytest.mark.integration


async def test_postgres_provider_roundtrip(postgres_settings) -> None:
    provider = await create_postgres_provider(postgres_settings)
    try:
        await provider.execute(
            "CREATE TABLE IF NOT EXISTS skills (id SERIAL PRIMARY KEY, name TEXT NOT NULL)"
        )
        await provider.execute("TRUNCATE TABLE skills RESTART IDENTITY")

        await provider.execute("INSERT INTO skills(name) VALUES($1)", ("romy",))
        await provider.executemany(
            "INSERT INTO skills(name) VALUES($1)",
            [("youtube",), ("orchid",)],
        )

        first = await provider.fetchone(
            "SELECT id, name FROM skills WHERE id = $1",
            (1,),
        )
        rows = await provider.fetchall("SELECT name FROM skills ORDER BY id")
        total = await provider.fetchval("SELECT COUNT(*) FROM skills")

        assert first is not None
        assert first["name"] == "romy"
        assert [row["name"] for row in rows] == ["romy", "youtube", "orchid"]
        assert total == 3

        with pytest.raises(RuntimeError):
            async with provider.transaction() as connection:
                await connection.execute("INSERT INTO skills(name) VALUES($1)", "rollback")
                raise RuntimeError("rollback")

        rollback_count = await provider.fetchval(
            "SELECT COUNT(*) FROM skills WHERE name = $1",
            ("rollback",),
        )
        assert rollback_count == 0
        assert (await provider.health_check()).healthy is True
    finally:
        await provider.close()


async def test_postgres_transient_timeout(postgres_settings) -> None:
    provider = await create_postgres_provider(postgres_settings)
    try:
        provider.command_timeout_seconds = 0.05
        provider.retry_attempts = 1
        provider.retry_backoff_seconds = 0.0

        with pytest.raises((TimeoutError, asyncio.TimeoutError)):
            await provider.fetchval("SELECT pg_sleep(0.2)")
    finally:
        await provider.close()


async def test_postgres_resource_manager_bootstrap(postgres_settings) -> None:
    manager = ResourceManager()
    settings = ResourceSettings(postgres=postgres_settings)

    await manager.startup(settings, required=["postgres"])
    provider = manager.get("postgres")
    assert isinstance(provider, PostgresProvider)
    await manager.close_all()
