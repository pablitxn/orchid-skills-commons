# First Release Closure Checklist (`orchid-mcp-commons`)

Checklist to close the first public release cut at **0.1.0** on **2026-02-11**.

## Release Strategy

- Distribution model: **GitHub refs only** (`branch`/`tag` install).
- No package-index publication step (no PyPI/TestPyPI release for this cut).

## Verified Status (2026-02-11)

Evidence executed locally:

- `uv sync --extra all --extra dev` -> OK.
- `uv run ruff check .` -> OK.
- `uv run ruff format --check .` -> OK.
- `uv run mypy src` -> OK.
- `uv run pytest -m "not integration and not e2e" --maxfail=1` -> `277 passed, 33 deselected`.
- `uv run pytest -m integration --maxfail=1` -> `30 passed, 3 skipped, 277 deselected`.
- `uv run pytest -m e2e --maxfail=1` -> `11 passed, 1 skipped, 298 deselected`.
- `uv run pytest -q` -> `307 passed, 3 skipped`.
- `uv run pip-audit` -> `No known vulnerabilities found`.
- `uv build --clear` -> builds `sdist` and `wheel` in `dist/`.
- `uv publish --dry-run --trusted-publishing never` -> artifact validation OK.

## 1) Release Cut

- [x] Target version: `0.1.0`.
- [x] Scope freeze enabled: only release blockers until tag cut.
- [x] `pyproject.toml` has `[project].version = "0.1.0"`.

Quick check:

```bash
rg -n '^version\s*=\s*"' pyproject.toml
```

## 2) Changelog

- [x] Consolidated release content under `## [0.1.0] - 2026-02-11`.
- [x] Release date matches the actual cut date.
- [x] Kept `## [Unreleased]` open for ongoing work.
- [x] Left `Unreleased` empty from a release-content standpoint.

Policy:

- Every behavior-changing PR adds an entry under `Unreleased`.
- On release day, `Unreleased` is cut into the new version section.

## 3) Quality Gate (Mandatory)

```bash
uv sync --extra all --extra dev
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest -m "not integration and not e2e" --maxfail=1
uv run pytest -m integration --maxfail=1
uv run pytest -m e2e --maxfail=1
uv run pip-audit
```

Exit criteria:

- [x] No lint/format/type-check failures.
- [x] Unit + integration + e2e are green.
- [x] No unaccepted critical/high vulnerabilities.

## 4) Build Validation

```bash
rm -rf dist/
uv build --clear
uv publish --dry-run --trusted-publishing never
```

Validation:

- [x] `sdist` and `wheel` are generated in `dist/`.
- [x] `publish --dry-run` completes.

## 5) Tag + GitHub Release (No Index Publish)

```bash
git tag -a v0.1.0 -m "release: v0.1.0"
# push when ready
# git push origin main
# git push origin v0.1.0
```

- [ ] Create GitHub Release notes from `CHANGELOG.md`.
- [ ] Verify CI is green on `main` and on `v0.1.0` tag.

## 6) Post-Release Validation (GitHub Ref Install)

- [ ] Validate installation from GitHub tag:

```bash
tmpdir="$(mktemp -d)"
python3 -m venv "$tmpdir/.venv"
source "$tmpdir/.venv/bin/activate"
python -m pip install "orchid-mcp-commons @ git+https://github.com/pablitxn/orchid-mcp-commons.git@v0.1.0"
python -c "import orchid_commons; print('ok')"
```

- [ ] Announce release internally with highlights and any breaking changes.
- [ ] Keep `Unreleased` open for post-0.1.0 work.

## 7) Release DoD

Release `0.1.0` is closed when:

- [ ] Release commit is on `main`.
- [ ] Tag `v0.1.0` exists locally and is pushed.
- [x] `CHANGELOG.md` is finalized with the real date.
- [ ] CI evidence is green and recorded.
- [ ] GitHub-tag install smoke test is OK.
