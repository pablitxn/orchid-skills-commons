# Commons-First Python Quality Standard (v1)

## Goal

Define one shared baseline for Python services in Orchid so every repo starts
from `orchid_commons` for runtime behavior and from `uv`/`ruff` for code quality.

This standard is the reference for:
- `matrix-orchid-bot`
- `youtube-mcp`
- `romy-skills`
- New Python services in Orchid ecosystem

## Mandatory baseline

### 1) Runtime config must use `appsettings`

Required files:
- `config/appsettings.json`
- `config/appsettings.development.json`
- `config/appsettings.production.json`

Environment selection:
- `ORCHID_ENV` (default: `development`)

Required top-level sections in `appsettings.json`:
- `service`
- `logging`
- `observability`
- `resources`

Loading and wiring must start from `orchid_commons`:

```python
from orchid_commons import (
    ResourceManager,
    ResourceSettings,
    bootstrap_logging_from_app_settings,
    bootstrap_observability,
    load_config,
)

settings = load_config(config_dir="config")
bootstrap_logging_from_app_settings(settings)
bootstrap_observability(settings)

manager = ResourceManager()
await manager.startup(ResourceSettings.from_app_settings(settings))
```

### 2) Dependency and task management must use `uv`

Source of truth:
- `pyproject.toml`
- `uv.lock`

Minimum workflow:

```bash
uv sync --extra dev
uv run pytest
```

If repo has runtime integrations (db/blob/observability), use:

```bash
uv sync --extra all --extra dev
```

### 3) Lint and format must use `ruff`

Mandatory commands:

```bash
uv run ruff check .
uv run ruff format --check .
```

Rules:
- No broad `# noqa` for whole files.
- Exceptions must be local and justified.
- New/edited modules must stay ruff-clean.

### 4) Types must be progressive but explicit

Current baseline for all repos:

```bash
uv run mypy src
```

Policy:
- New modules must have explicit typing.
- Touched modules should reduce existing type debt.
- Full strict mode can be adopted per repo once external stubs/debt are addressed.

### 5) Tests must run in pytest with explicit markers

Mandatory:

```bash
uv run pytest
```

If integration tests exist, they must use marker `integration` and run separately:

```bash
uv run pytest -m integration
```

### 6) CI minimum quality gate

Each service repo should have a CI workflow that runs:
1. `uv sync --extra dev` (or `--extra all --extra dev` when needed).
2. `uv run ruff check .`
3. `uv run ruff format --check .`
4. `uv run pytest`

`mypy` should be added when repo baseline supports it without global ignores.

## PR definition of done

- Runtime config uses `appsettings` + `ORCHID_ENV`.
- `uv.lock` is updated when dependencies change.
- Ruff checks pass for changed code.
- Tests pass locally (`uv run pytest`).
- Health/lifecycle goes through `ResourceManager` for runtime resources.

## Matrix-Orchid-Bot profile

For `matrix-orchid-bot`, this standard means:
- Runtime settings (`service/logging/observability/resources`) stay in `appsettings`.
- Domain bot definitions remain in `config/bots.yaml`.
- Startup path keeps this order:
  1. `load_config` (commons)
  2. `bootstrap_logging_from_app_settings`
  3. `bootstrap_observability`
  4. `ResourceManager.startup(...)`
- Daily dev command set uses only `uv`, not `pip install -r requirements.txt`.

## Templates

A reusable template is available in:
- `examples/quality/python-service-template/README.md`
- `examples/quality/python-service-template/pyproject.toml`
- `examples/quality/python-service-template/config/appsettings.json`
- `examples/quality/python-service-template/config/appsettings.development.json`
- `examples/quality/python-service-template/config/appsettings.production.json`
