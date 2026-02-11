"""End-to-end integration tests for SQLite resource behavior."""

from __future__ import annotations

from contextlib import suppress

import aiosqlite
import pytest

from orchid_commons.db import create_sqlite_resource

pytestmark = pytest.mark.integration


async def test_sqlite_roundtrip_and_transaction(sqlite_settings) -> None:
    resource = await create_sqlite_resource(sqlite_settings)
    try:
        await resource.execute(
            "CREATE TABLE IF NOT EXISTS skills (id INTEGER PRIMARY KEY, name TEXT NOT NULL)",
            commit=True,
        )

        async with resource.transaction() as connection:
            await connection.execute("INSERT INTO skills(name) VALUES (?)", ("romy",))
            await connection.execute("INSERT INTO skills(name) VALUES (?)", ("orchid",))

        rows = await resource.fetchall("SELECT name FROM skills ORDER BY id")
        assert [row["name"] for row in rows] == ["romy", "orchid"]
        assert (await resource.health_check()).healthy is True
    finally:
        await resource.close()


async def test_sqlite_transient_lock_error(sqlite_settings) -> None:
    resource = await create_sqlite_resource(sqlite_settings)
    lock_connection = await aiosqlite.connect(sqlite_settings.db_path)
    try:
        await resource.execute(
            "CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)",
            commit=True,
        )
        await resource.execute("PRAGMA busy_timeout = 0", commit=True)
        await lock_connection.execute("PRAGMA busy_timeout = 0")
        await lock_connection.execute("BEGIN EXCLUSIVE")

        with pytest.raises(aiosqlite.OperationalError, match="locked"):
            await resource.execute(
                "INSERT INTO events(payload) VALUES (?)",
                ("locked",),
                commit=True,
            )
    finally:
        with suppress(Exception):
            await lock_connection.rollback()
        await lock_connection.close()
        await resource.close()
