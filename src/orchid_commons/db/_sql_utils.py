"""Shared SQL file-reading and migration-collection utilities (internal)."""

from __future__ import annotations

from pathlib import Path


def read_sql_file(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def collect_migration_files(directory: Path, pattern: str) -> list[Path]:
    if not directory.exists():
        return []
    return [path for path in sorted(directory.glob(pattern)) if path.is_file()]
