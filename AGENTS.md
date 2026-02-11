# Repository Guidelines

## Project Structure & Module Organization
- Library code lives in `src/orchid_commons/` and is grouped by domain: `config/`, `runtime/`, `db/`, `blob/`, and `observability/`.
- Tests are under `tests/`: unit suites in `tests/unit/`, integration suites in `tests/integration/`, and shared fixtures in `tests/fixtures/` and `tests/conftest.py`.
- Long-form technical notes are in `docs/`; runnable examples and local stacks are in `examples/`.
- Keep new modules close to the relevant domain package and expose public APIs intentionally through `src/orchid_commons/__init__.py`.

## Build, Test, and Development Commands
- `uv sync --extra all --extra dev`: install runtime integrations + developer tooling.
- `uv run pytest`: run the default test suite.
- `uv run pytest -m integration`: run integration tests (Docker/testcontainers or external services via env vars).
- `uv run ruff check .`: lint code.
- `uv run ruff format --check .`: verify formatting.
- `uv run mypy src`: run type checks.
- `uv run pip-audit`: audit dependencies.
- `uv build`: build wheel/sdist using Hatchling.

## Coding Style & Naming Conventions
- Target Python `>=3.11`, 4-space indentation, and explicit typing for new/changed code.
- Ruff is the style authority (`line-length = 100`; lint rules include `E,F,I,UP,B,ASYNC,RUF`).
- Use `snake_case` for modules/functions/variables, `PascalCase` for classes, and `UPPER_SNAKE_CASE` for constants.
- Follow existing async patterns for resource connectors (`async def`, typed exceptions, clear lifecycle methods like `health_check()` and `close()`).

## Testing Guidelines
- Frameworks: `pytest`, `pytest-asyncio`, and `pytest-cov`.
- Name tests as `test_*.py`; keep unit tests isolated and place integration coverage behind the `integration` marker.
- For CI parity, run: `uv run pytest --cov=src --cov-report=term-missing --cov-report=xml`.
- Prefer regression tests with every bug fix, especially around resource startup/shutdown and typed error mapping.

## Commit & Pull Request Guidelines
- Follow Conventional Commits seen in history: `feat:`, `fix:`, `refactor:`, `docs:`, `ci:`, `chore:`.
- Keep commit scopes focused and messages imperative (example: `fix: handle Redis timeout as transient error`).
- PRs should include: concise summary, linked issue/context, test evidence (commands run), and docs/changelog updates when behavior changes.
- Ensure CI is green: Ruff lint/format, mypy, and pytest matrix (`3.11`, `3.12`, `3.13`).
