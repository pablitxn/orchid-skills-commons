"""PostgreSQL provider backed by an asyncpg pool."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from orchid_commons.errors import MissingDependencyError
from orchid_commons.health import HealthStatus
from orchid_commons.metrics import MetricsRecorder, get_metrics_recorder
from orchid_commons.settings import PostgresSettings

_T = TypeVar("_T")


def _import_asyncpg() -> Any:
    try:
        import asyncpg
    except ImportError as exc:  # pragma: no cover - exercised when extras are absent
        raise MissingDependencyError(
            "PostgreSQL provider requires optional dependency 'asyncpg'. "
            "Install with: uv sync --extra sql"
        ) from exc
    return asyncpg


def _build_retryable_exceptions(asyncpg: Any) -> tuple[type[BaseException], ...]:
    names = (
        "PostgresConnectionError",
        "CannotConnectNowError",
        "ConnectionDoesNotExistError",
        "InterfaceError",
    )
    retryable: list[type[BaseException]] = [asyncio.TimeoutError, ConnectionError, OSError]
    for name in names:
        exc_type = getattr(asyncpg, name, None)
        if isinstance(exc_type, type) and issubclass(exc_type, BaseException):
            retryable.append(exc_type)
    return tuple(retryable)


def _read_sql_file(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _collect_migration_files(migrations_path: Path, pattern: str) -> list[Path]:
    if not migrations_path.exists():
        return []
    return [path for path in sorted(migrations_path.glob(pattern)) if path.is_file()]


@dataclass(slots=True)
class PostgresProvider:
    """Managed PostgreSQL pool with SQLite-like query helpers."""

    _pool: Any
    command_timeout_seconds: float
    retry_attempts: int = 2
    retry_backoff_seconds: float = 0.1
    close_timeout_seconds: float = 10.0
    _metrics: MetricsRecorder | None = None
    _closed: bool = False

    @classmethod
    async def create(
        cls,
        settings: PostgresSettings,
        *,
        retry_attempts: int = 2,
        retry_backoff_seconds: float = 0.1,
        close_timeout_seconds: float = 10.0,
    ) -> PostgresProvider:
        """Create a provider and initialize the asyncpg pool."""
        if settings.min_pool_size > settings.max_pool_size:
            msg = "PostgreSQL min_pool_size must be <= max_pool_size"
            raise ValueError(msg)

        asyncpg = _import_asyncpg()
        pool = await asyncpg.create_pool(
            dsn=settings.dsn,
            min_size=settings.min_pool_size,
            max_size=settings.max_pool_size,
            command_timeout=settings.command_timeout_seconds,
        )
        return cls(
            _pool=pool,
            command_timeout_seconds=settings.command_timeout_seconds,
            retry_attempts=retry_attempts,
            retry_backoff_seconds=retry_backoff_seconds,
            close_timeout_seconds=close_timeout_seconds,
        )

    @property
    def pool(self) -> Any:
        """Expose the underlying asyncpg pool for advanced usage."""
        return self._pool

    @property
    def is_connected(self) -> bool:
        """Whether the provider is ready to serve queries."""
        return not self._closed

    async def connect(self) -> Any:
        """Compatibility method mirroring SqliteResource.connect()."""
        return self._pool

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[Any]:
        """Yield a pooled connection."""
        async with self._pool.acquire() as connection:
            yield connection

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[Any]:
        """Run a block inside a transaction with automatic commit/rollback."""
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                yield connection

    async def execute(
        self,
        query: str,
        params: Sequence[Any] | None = None,
        *,
        commit: bool = False,  # kept for SQLite API compatibility
    ) -> str:
        """Execute a SQL statement and return asyncpg status text."""
        del commit

        async def operation(connection: Any) -> str:
            return await connection.execute(query, *(params or ()))

        return await self._run_with_retries(operation, operation_name="execute")

    async def executemany(
        self,
        query: str,
        rows: Iterable[Sequence[Any]],
        *,
        commit: bool = False,  # kept for SQLite API compatibility
    ) -> None:
        """Execute a SQL statement for multiple parameter rows."""
        del commit
        values = [tuple(row) for row in rows]

        async def operation(connection: Any) -> None:
            await connection.executemany(query, values)
            return None

        await self._run_with_retries(operation, operation_name="executemany")

    async def fetchone(
        self,
        query: str,
        params: Sequence[Any] | None = None,
    ) -> dict[str, Any] | None:
        """Execute query and return first row as a dictionary."""

        async def operation(connection: Any) -> dict[str, Any] | None:
            record = await connection.fetchrow(query, *(params or ()))
            if record is None:
                return None
            return dict(record)

        return await self._run_with_retries(operation, operation_name="fetchone")

    async def fetchall(
        self,
        query: str,
        params: Sequence[Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Execute query and return all rows as dictionaries."""

        async def operation(connection: Any) -> list[dict[str, Any]]:
            records = await connection.fetch(query, *(params or ()))
            return [dict(record) for record in records]

        return await self._run_with_retries(operation, operation_name="fetchall")

    async def fetchval(
        self,
        query: str,
        params: Sequence[Any] | None = None,
    ) -> Any:
        """Execute query and return a single scalar value."""

        async def operation(connection: Any) -> Any:
            return await connection.fetchval(query, *(params or ()))

        return await self._run_with_retries(operation, operation_name="fetchval")

    async def execute_script_file(self, script_path: Path | str) -> None:
        """Execute SQL script from a file."""
        script_file = Path(script_path)
        script = _read_sql_file(script_file)
        await self.executescript(script, commit=True)

    async def executescript(self, sql_script: str, *, commit: bool = True) -> None:
        """Execute SQL script text.

        `commit` is accepted for API compatibility with SqliteResource.
        """

        async def operation(connection: Any) -> None:
            if commit:
                async with connection.transaction():
                    await connection.execute(sql_script)
            else:
                await connection.execute(sql_script)
            return None

        await self._run_with_retries(operation, operation_name="executescript")

    async def run_migrations(
        self,
        migrations_dir: Path | str,
        *,
        pattern: str = "*.sql",
    ) -> list[Path]:
        """Execute migration files in lexicographic order."""
        migrations_path = Path(migrations_dir)
        executed: list[Path] = []
        for migration_file in _collect_migration_files(migrations_path, pattern):
            await self.execute_script_file(migration_file)
            executed.append(migration_file)
        return executed

    async def fetch_one(self, query: str, *args: Any) -> dict[str, Any] | None:
        """Alias for compatibility with snake_case variant."""
        return await self.fetchone(query, args)

    async def fetch_all(self, query: str, *args: Any) -> list[dict[str, Any]]:
        """Alias for compatibility with snake_case variant."""
        return await self.fetchall(query, args)

    async def fetch_val(self, query: str, *args: Any) -> Any:
        """Alias for compatibility with snake_case variant."""
        return await self.fetchval(query, args)

    async def health_check(self) -> HealthStatus:
        """Verify provider liveness with a quick SELECT 1."""
        start = time.perf_counter()
        try:
            await self.fetchval("SELECT 1")
            return HealthStatus(
                healthy=True,
                latency_ms=(time.perf_counter() - start) * 1000,
                message="ok",
            )
        except Exception as exc:
            return HealthStatus(
                healthy=False,
                latency_ms=(time.perf_counter() - start) * 1000,
                message=str(exc),
                details={"error_type": exc.__class__.__name__},
            )

    async def close(self) -> None:
        """Close pool gracefully and terminate on timeout."""
        started = time.perf_counter()
        try:
            try:
                await asyncio.wait_for(self._pool.close(), timeout=self.close_timeout_seconds)
            except TimeoutError:
                self._pool.terminate()
        except Exception as exc:
            self._observe_operation("close", started, success=False)
            self._metrics_recorder().observe_error(
                resource="postgres",
                operation="close",
                error_type=type(exc).__name__,
            )
            raise
        finally:
            self._closed = True

        self._observe_operation("close", started, success=True)

    async def _run_with_retries(
        self,
        operation: Callable[[Any], Awaitable[_T]],
        *,
        operation_name: str,
    ) -> _T:
        started = time.perf_counter()
        retryable_exceptions: tuple[type[BaseException], ...] = (
            asyncio.TimeoutError,
            ConnectionError,
            OSError,
        )
        try:
            asyncpg = _import_asyncpg()
            retryable_exceptions = _build_retryable_exceptions(asyncpg)
        except MissingDependencyError:
            # Unit tests can exercise behavior with a fake pool even without asyncpg installed.
            pass
        attempt = 0
        while True:
            try:
                async with self._pool.acquire() as connection:
                    result = await asyncio.wait_for(
                        operation(connection),
                        timeout=self.command_timeout_seconds,
                    )
                    self._observe_operation(operation_name, started, success=True)
                    self._observe_postgres_pool_usage()
                    return result
            except Exception as exc:
                if not isinstance(exc, retryable_exceptions) or attempt >= self.retry_attempts:
                    self._observe_operation(operation_name, started, success=False)
                    self._metrics_recorder().observe_error(
                        resource="postgres",
                        operation=operation_name,
                        error_type=type(exc).__name__,
                    )
                    self._observe_postgres_pool_usage()
                    raise
                attempt += 1
                await asyncio.sleep(self.retry_backoff_seconds * attempt)

    def _observe_operation(self, operation_name: str, started: float, *, success: bool) -> None:
        self._metrics_recorder().observe_operation(
            resource="postgres",
            operation=operation_name,
            duration_seconds=time.perf_counter() - started,
            success=success,
        )

    def _observe_postgres_pool_usage(self) -> None:
        size = self._pool_metric("get_size")
        idle = self._pool_metric("get_idle_size")
        if size is None and idle is None:
            return

        resolved_size = size if size is not None else max(0, idle or 0)
        resolved_idle = idle if idle is not None else 0
        min_size = self._pool_metric("get_min_size")
        max_size = self._pool_metric("get_max_size")

        self._metrics_recorder().observe_postgres_pool(
            used_connections=max(0, resolved_size - resolved_idle),
            idle_connections=max(0, resolved_idle),
            min_connections=(resolved_size if min_size is None else max(0, min_size)),
            max_connections=(resolved_size if max_size is None else max(0, max_size)),
        )

    def _metrics_recorder(self) -> MetricsRecorder:
        return get_metrics_recorder() if self._metrics is None else self._metrics

    def _pool_metric(self, getter_name: str) -> int | None:
        getter = getattr(self._pool, getter_name, None)
        if not callable(getter):
            return None
        try:
            return int(getter())
        except Exception:
            return None


async def create_postgres_provider(settings: PostgresSettings) -> PostgresProvider:
    """Factory used by ResourceManager bootstrap."""
    return await PostgresProvider.create(settings)
