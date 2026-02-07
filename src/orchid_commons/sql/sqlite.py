"""SQLite resource provider and migration helpers."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter
from typing import Any

import aiosqlite

from orchid_commons.config.resources import SqliteSettings
from orchid_commons.observability.metrics import MetricsRecorder, get_metrics_recorder
from orchid_commons.runtime.health import HealthStatus


def _read_sql_file(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _collect_migration_files(migrations_path: Path, pattern: str) -> list[Path]:
    if not migrations_path.exists():
        return []
    return [path for path in sorted(migrations_path.glob(pattern)) if path.is_file()]


class SqliteResource:
    """Managed SQLite connection with helper methods for common operations."""

    def __init__(
        self,
        settings: SqliteSettings,
        *,
        row_factory: Any = aiosqlite.Row,
        metrics: MetricsRecorder | None = None,
    ) -> None:
        self._settings = settings
        self._row_factory = row_factory
        self._connection: aiosqlite.Connection | None = None
        self._metrics = metrics

    @property
    def db_path(self) -> Path:
        """Configured database file path."""
        return self._settings.db_path

    @property
    def is_connected(self) -> bool:
        """Whether a SQLite connection is currently open."""
        return self._connection is not None

    async def connect(self) -> aiosqlite.Connection:
        """Create the underlying connection if needed and return it."""
        if self._connection is not None:
            return self._connection

        started = perf_counter()
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            connection = await aiosqlite.connect(self.db_path)
            connection.row_factory = self._row_factory
            await connection.execute("PRAGMA foreign_keys = ON")
            await connection.commit()
            self._connection = connection
        except Exception as exc:
            self._observe_error("connect", started, exc)
            raise

        self._metrics_recorder().observe_operation(
            resource="sqlite",
            operation="connect",
            duration_seconds=perf_counter() - started,
            success=True,
        )
        return self._connection

    async def close(self) -> None:
        """Close the underlying SQLite connection."""
        started = perf_counter()
        if self._connection is not None:
            try:
                await self._connection.close()
                self._connection = None
            except Exception as exc:
                self._observe_error("close", started, exc)
                raise
        self._metrics_recorder().observe_operation(
            resource="sqlite",
            operation="close",
            duration_seconds=perf_counter() - started,
            success=True,
        )

    async def health_check(self) -> HealthStatus:
        """Probe resource health using a lightweight query."""
        start = perf_counter()
        try:
            connection = await self.connect()
            await connection.execute("SELECT 1")
            latency_ms = (perf_counter() - start) * 1000
            return HealthStatus(healthy=True, latency_ms=latency_ms)
        except Exception as exc:
            latency_ms = (perf_counter() - start) * 1000
            return HealthStatus(healthy=False, latency_ms=latency_ms, message=str(exc))

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[aiosqlite.Connection]:
        """Yield a live SQLite connection."""
        connection = await self.connect()
        yield connection

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        """Run a block inside a transaction with automatic commit/rollback."""
        connection = await self.connect()
        await connection.execute("BEGIN")
        try:
            yield connection
        except Exception:
            await connection.rollback()
            raise
        else:
            await connection.commit()

    async def execute(
        self,
        query: str,
        params: Sequence[Any] | None = None,
        *,
        commit: bool = False,
    ) -> aiosqlite.Cursor:
        """Execute a SQL query."""
        started = perf_counter()
        try:
            connection = await self.connect()
            cursor = await connection.execute(query, tuple(params or ()))
            if commit:
                await connection.commit()
        except Exception as exc:
            self._observe_error("execute", started, exc)
            raise

        self._metrics_recorder().observe_operation(
            resource="sqlite",
            operation="execute",
            duration_seconds=perf_counter() - started,
            success=True,
        )
        return cursor

    async def executemany(
        self,
        query: str,
        rows: Iterable[Sequence[Any]],
        *,
        commit: bool = False,
    ) -> aiosqlite.Cursor:
        """Execute the same SQL query for multiple rows."""
        started = perf_counter()
        try:
            connection = await self.connect()
            cursor = await connection.executemany(query, rows)
            if commit:
                await connection.commit()
        except Exception as exc:
            self._observe_error("executemany", started, exc)
            raise

        self._metrics_recorder().observe_operation(
            resource="sqlite",
            operation="executemany",
            duration_seconds=perf_counter() - started,
            success=True,
        )
        return cursor

    async def executescript(self, sql_script: str, *, commit: bool = True) -> None:
        """Execute a SQL script."""
        started = perf_counter()
        try:
            connection = await self.connect()
            await connection.executescript(sql_script)
            if commit:
                await connection.commit()
        except Exception as exc:
            self._observe_error("executescript", started, exc)
            raise

        self._metrics_recorder().observe_operation(
            resource="sqlite",
            operation="executescript",
            duration_seconds=perf_counter() - started,
            success=True,
        )

    async def fetchone(
        self,
        query: str,
        params: Sequence[Any] | None = None,
    ) -> aiosqlite.Row | None:
        """Execute query and return first row."""
        started = perf_counter()
        try:
            connection = await self.connect()
            cursor = await connection.execute(query, tuple(params or ()))
            row = await cursor.fetchone()
        except Exception as exc:
            self._observe_error("fetchone", started, exc)
            raise

        self._metrics_recorder().observe_operation(
            resource="sqlite",
            operation="fetchone",
            duration_seconds=perf_counter() - started,
            success=True,
        )
        return row

    async def fetchall(
        self,
        query: str,
        params: Sequence[Any] | None = None,
    ) -> list[aiosqlite.Row]:
        """Execute query and return all rows."""
        started = perf_counter()
        try:
            connection = await self.connect()
            cursor = await connection.execute(query, tuple(params or ()))
            rows = await cursor.fetchall()
        except Exception as exc:
            self._observe_error("fetchall", started, exc)
            raise

        self._metrics_recorder().observe_operation(
            resource="sqlite",
            operation="fetchall",
            duration_seconds=perf_counter() - started,
            success=True,
        )
        return list(rows)

    def _observe_error(self, operation: str, started: float, exc: Exception) -> None:
        self._metrics_recorder().observe_operation(
            resource="sqlite",
            operation=operation,
            duration_seconds=perf_counter() - started,
            success=False,
        )
        self._metrics_recorder().observe_error(
            resource="sqlite",
            operation=operation,
            error_type=type(exc).__name__,
        )

    def _metrics_recorder(self) -> MetricsRecorder:
        return get_metrics_recorder() if self._metrics is None else self._metrics

    async def execute_script_file(self, script_path: Path | str) -> None:
        """Execute a SQL script from file."""
        script_file = Path(script_path)
        script = _read_sql_file(script_file)
        await self.executescript(script, commit=True)

    async def run_migrations(
        self,
        migrations_dir: Path | str,
        *,
        pattern: str = "*.sql",
    ) -> list[Path]:
        """Execute migrations files in lexicographic order."""
        migrations_path = Path(migrations_dir)
        executed: list[Path] = []
        for migration_file in _collect_migration_files(migrations_path, pattern):
            await self.execute_script_file(migration_file)
            executed.append(migration_file)
        return executed


async def create_sqlite_resource(settings: SqliteSettings) -> SqliteResource:
    """Factory used by ResourceManager startup/bootstrap."""
    resource = SqliteResource(settings)
    await resource.connect()
    return resource
