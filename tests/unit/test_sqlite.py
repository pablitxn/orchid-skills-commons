"""Tests for SQLite provider and ResourceManager integration."""

from pathlib import Path

import aiosqlite
import pytest

from orchid_commons import ResourceManager
from orchid_commons.config.resources import ResourceSettings, SqliteSettings
from orchid_commons.db import SqliteResource, create_sqlite_resource


class TestSqliteResource:
    async def test_connect_creates_directory_and_enables_foreign_keys(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "nested" / "test.db"
        resource = SqliteResource(SqliteSettings(db_path=db_path))

        try:
            assert not resource.is_connected

            await resource.connect()

            assert resource.is_connected
            assert db_path.parent.exists()

            row = await resource.fetchone("PRAGMA foreign_keys")
            assert row is not None
            assert row[0] == 1
        finally:
            await resource.close()

    async def test_execute_fetch_and_executemany(self, tmp_path: Path) -> None:
        resource = SqliteResource(SqliteSettings(db_path=tmp_path / "queries.db"))

        try:
            await resource.execute(
                "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL)",
                commit=True,
            )
            await resource.execute(
                "INSERT INTO users(id, name) VALUES (?, ?)",
                (1, "Ada"),
                commit=True,
            )
            await resource.executemany(
                "INSERT INTO users(id, name) VALUES (?, ?)",
                [(2, "Grace"), (3, "Linus")],
                commit=True,
            )

            one = await resource.fetchone("SELECT name FROM users WHERE id = ?", (1,))
            all_rows = await resource.fetchall("SELECT name FROM users ORDER BY id")

            assert one is not None
            assert one["name"] == "Ada"
            assert [row["name"] for row in all_rows] == ["Ada", "Grace", "Linus"]
        finally:
            await resource.close()

    async def test_transaction_commit_and_rollback(self, tmp_path: Path) -> None:
        resource = SqliteResource(SqliteSettings(db_path=tmp_path / "tx.db"))

        try:
            await resource.execute(
                "CREATE TABLE events (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)",
                commit=True,
            )

            async with resource.transaction() as connection:
                await connection.execute(
                    "INSERT INTO events(payload) VALUES (?)",
                    ("ok",),
                )

            with pytest.raises(RuntimeError):
                async with resource.transaction() as connection:
                    await connection.execute(
                        "INSERT INTO events(payload) VALUES (?)",
                        ("rollback",),
                    )
                    raise RuntimeError("force rollback")

            row = await resource.fetchone("SELECT COUNT(*) AS total FROM events")
            assert row is not None
            assert row["total"] == 1
        finally:
            await resource.close()

    async def test_execute_script_and_migrations(self, tmp_path: Path) -> None:
        resource = SqliteResource(SqliteSettings(db_path=tmp_path / "migrations.db"))

        script_file = tmp_path / "schema.sql"
        script_file.write_text(
            """
            CREATE TABLE parents (
                id INTEGER PRIMARY KEY
            );
            CREATE TABLE children (
                id INTEGER PRIMARY KEY,
                parent_id INTEGER NOT NULL,
                FOREIGN KEY(parent_id) REFERENCES parents(id)
            );
            """,
            encoding="utf-8",
        )

        migrations_dir = tmp_path / "migrations"
        migrations_dir.mkdir()
        (migrations_dir / "001_seed.sql").write_text(
            "INSERT INTO parents(id) VALUES (1);",
            encoding="utf-8",
        )
        (migrations_dir / "002_child.sql").write_text(
            "INSERT INTO children(id, parent_id) VALUES (1, 1);",
            encoding="utf-8",
        )

        try:
            await resource.execute_script_file(script_file)
            executed = await resource.run_migrations(migrations_dir)

            assert [file.name for file in executed] == ["001_seed.sql", "002_child.sql"]

            parent_count = await resource.fetchone("SELECT COUNT(*) AS total FROM parents")
            child_count = await resource.fetchone("SELECT COUNT(*) AS total FROM children")
            assert parent_count is not None
            assert child_count is not None
            assert parent_count["total"] == 1
            assert child_count["total"] == 1

            with pytest.raises(aiosqlite.IntegrityError):
                await resource.execute(
                    "INSERT INTO children(id, parent_id) VALUES (2, 999)",
                    commit=True,
                )
        finally:
            await resource.close()

    async def test_factory_connects_resource(self, tmp_path: Path) -> None:
        resource = await create_sqlite_resource(SqliteSettings(db_path=tmp_path / "factory.db"))
        try:
            assert resource.is_connected
        finally:
            await resource.close()


class TestSqliteResourceManagerIntegration:
    async def test_startup_bootstraps_sqlite(self, tmp_path: Path) -> None:
        manager = ResourceManager()
        settings = ResourceSettings(
            sqlite=SqliteSettings(db_path=tmp_path / "resource_manager.db")
        )

        await manager.startup(settings, required=["sqlite"])
        sqlite_resource = manager.get("sqlite")
        assert isinstance(sqlite_resource, SqliteResource)

        row = await sqlite_resource.fetchone("PRAGMA foreign_keys")
        assert row is not None
        assert row[0] == 1

        await manager.close_all()
        assert not manager.has("sqlite")
