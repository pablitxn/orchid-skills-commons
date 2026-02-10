# Python Service Template (Commons-First)

This template is the starter baseline for Orchid Python services.

It enforces:
- Runtime config via `appsettings` + `ORCHID_ENV`.
- Dependency and task management via `uv`.
- Code quality gates via `ruff` + `pytest` (and optional `mypy`).

## How to use

1. Copy this folder into the target repo root.
2. Merge `pyproject.toml` sections with repo-specific metadata and dependencies.
3. Keep `config/appsettings*.json` as runtime source of truth.
4. Wire startup using `orchid_commons.load_config(...)`.

## Baseline commands

```bash
uv sync --extra dev
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

For repos with infra extras:

```bash
uv sync --extra all --extra dev
```
