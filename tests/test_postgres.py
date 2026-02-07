"""Tests for PostgreSQL provider and ResourceManager integration."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from orchid_commons import ResourceManager
from orchid_commons.settings import PostgresSettings, ResourceSettings
from orchid_commons.db import PostgresProvider, create_postgres_provider


class FakeTransaction:
    def __init__(self, connection: FakeConnection) -> None:
        self._connection = connection

    async def __aenter__(self) -> None:
        self._connection.transaction_entered += 1

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: Any,
    ) -> None:
        self._connection.transaction_exited += 1
        if exc is None:
            self._connection.transaction_committed += 1
        else:
            self._connection.transaction_rolled_back += 1


class FakeConnection:
    def __init__(self) -> None:
        self.execute_result = "OK"
        self.fetchrow_result: dict[str, Any] | None = {"id": 1}
        self.fetchall_result: list[dict[str, Any]] = [{"id": 1}, {"id": 2}]
        self.fetchval_result: Any = 2
        self.fetchval_error: Exception | None = None
        self.queries: list[tuple[str, tuple[Any, ...]]] = []
        self.executemany_calls: list[tuple[str, list[tuple[Any, ...]]]] = []
        self.transaction_entered = 0
        self.transaction_exited = 0
        self.transaction_committed = 0
        self.transaction_rolled_back = 0

    def transaction(self) -> FakeTransaction:
        return FakeTransaction(self)

    async def execute(self, query: str, *args: Any) -> str:
        self.queries.append((query, args))
        return self.execute_result

    async def executemany(self, query: str, rows: list[tuple[Any, ...]]) -> None:
        self.executemany_calls.append((query, rows))

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        self.queries.append((query, args))
        return self.fetchrow_result

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        self.queries.append((query, args))
        return self.fetchall_result

    async def fetchval(self, query: str, *args: Any) -> Any:
        self.queries.append((query, args))
        if self.fetchval_error is not None:
            raise self.fetchval_error
        return self.fetchval_result


class FakeAcquire:
    def __init__(self, pool: FakePool) -> None:
        self._pool = pool

    async def __aenter__(self) -> FakeConnection:
        self._pool.acquire_calls += 1
        if self._pool.acquire_errors:
            raise self._pool.acquire_errors.pop(0)
        return self._pool.connection

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: Any,
    ) -> None:
        return None


class FakePool:
    def __init__(self) -> None:
        self.connection = FakeConnection()
        self.acquire_calls = 0
        self.acquire_errors: list[Exception] = []
        self.close_delay_seconds = 0.0
        self.close_calls = 0
        self.terminated = False

    def acquire(self) -> FakeAcquire:
        return FakeAcquire(self)

    async def close(self) -> None:
        self.close_calls += 1
        if self.close_delay_seconds > 0:
            await asyncio.sleep(self.close_delay_seconds)

    def terminate(self) -> None:
        self.terminated = True


def build_provider(pool: FakePool) -> PostgresProvider:
    return PostgresProvider(
        _pool=pool,
        command_timeout_seconds=1.0,
        retry_attempts=2,
        retry_backoff_seconds=0.0,
        close_timeout_seconds=0.01,
    )


class TestPostgresProvider:
    async def test_execute_and_fetch_helpers(self) -> None:
        pool = FakePool()
        provider = build_provider(pool)

        status = await provider.execute("INSERT INTO users(id) VALUES($1)", (1,), commit=True)
        await provider.executemany(
            "INSERT INTO users(id) VALUES($1)",
            [(2,), (3,)],
            commit=True,
        )
        one = await provider.fetchone("SELECT id FROM users WHERE id=$1", (1,))
        many = await provider.fetchall("SELECT id FROM users ORDER BY id")
        value = await provider.fetchval("SELECT COUNT(*) FROM users")
        alias_one = await provider.fetch_one("SELECT id FROM users WHERE id=$1", 1)
        alias_many = await provider.fetch_all("SELECT id FROM users ORDER BY id")
        alias_value = await provider.fetch_val("SELECT COUNT(*) FROM users")

        assert status == "OK"
        assert one == {"id": 1}
        assert many == [{"id": 1}, {"id": 2}]
        assert value == 2
        assert alias_one == {"id": 1}
        assert alias_many == [{"id": 1}, {"id": 2}]
        assert alias_value == 2
        assert pool.connection.executemany_calls == [
            ("INSERT INTO users(id) VALUES($1)", [(2,), (3,)])
        ]

    async def test_transaction_context(self) -> None:
        pool = FakePool()
        provider = build_provider(pool)

        async with provider.transaction() as connection:
            await connection.execute("SELECT 1")

        assert pool.connection.transaction_entered == 1
        assert pool.connection.transaction_exited == 1
        assert pool.connection.transaction_committed == 1
        assert pool.connection.transaction_rolled_back == 0

    async def test_retries_on_connection_error(self) -> None:
        pool = FakePool()
        pool.acquire_errors = [ConnectionError("temporary failure")]
        provider = build_provider(pool)

        result = await provider.execute("SELECT 1")

        assert result == "OK"
        assert pool.acquire_calls == 2

    async def test_health_check_unhealthy(self) -> None:
        pool = FakePool()
        pool.connection.fetchval_error = RuntimeError("boom")
        provider = build_provider(pool)

        status = await provider.health_check()

        assert not status.healthy
        assert status.message == "boom"
        assert status.details == {"error_type": "RuntimeError"}

    async def test_close_timeout_terminates_pool(self) -> None:
        pool = FakePool()
        pool.close_delay_seconds = 0.05
        provider = build_provider(pool)

        await provider.close()

        assert pool.close_calls == 1
        assert pool.terminated
        assert not provider.is_connected

    async def test_execute_script_file_and_migrations(self, tmp_path) -> None:
        pool = FakePool()
        provider = build_provider(pool)

        script_file = tmp_path / "schema.sql"
        script_file.write_text("CREATE TABLE IF NOT EXISTS users(id INT);", encoding="utf-8")

        migrations_dir = tmp_path / "migrations"
        migrations_dir.mkdir()
        (migrations_dir / "001.sql").write_text("INSERT INTO users VALUES (1);", encoding="utf-8")
        (migrations_dir / "002.sql").write_text("INSERT INTO users VALUES (2);", encoding="utf-8")

        await provider.execute_script_file(script_file)
        executed = await provider.run_migrations(migrations_dir)

        assert [path.name for path in executed] == ["001.sql", "002.sql"]
        executed_queries = [query for query, _ in pool.connection.queries]
        assert "CREATE TABLE IF NOT EXISTS users(id INT);" in executed_queries
        assert "INSERT INTO users VALUES (1);" in executed_queries
        assert "INSERT INTO users VALUES (2);" in executed_queries


class TestPostgresResourceManagerIntegration:
    async def test_startup_bootstraps_postgres(self) -> None:
        class CreatedProvider:
            async def close(self) -> None:
                return None

        created = CreatedProvider()

        async def fake_factory(settings: PostgresSettings) -> CreatedProvider:
            assert settings.dsn == "postgresql://test:test@localhost:5432/test"
            return created

        from orchid_commons.manager import register_factory

        register_factory("postgres", "postgres", fake_factory)

        manager = ResourceManager()
        settings = ResourceSettings(
            postgres=PostgresSettings(
                dsn="postgresql://test:test@localhost:5432/test",
                min_pool_size=1,
                max_pool_size=2,
                command_timeout_seconds=3.0,
            )
        )

        await manager.startup(settings, required=["postgres"])
        assert manager.get("postgres") is created
        await manager.close_all()


class TestPostgresFactory:
    async def test_factory_rejects_invalid_pool_sizes(self) -> None:
        with pytest.raises(ValueError):
            await create_postgres_provider(
                PostgresSettings(
                    dsn="postgresql://invalid",
                    min_pool_size=3,
                    max_pool_size=2,
                    command_timeout_seconds=1.0,
                )
            )
