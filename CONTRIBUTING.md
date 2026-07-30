# Contributing to Flow2Skill

Flow2Skill is intentionally narrow: compile one successful Playwright demonstration into a protected workflow, a portable skill, and executable proof. Changes should strengthen that contract rather than add autonomous planning or cloud dependencies.

## Development setup

```bash
python -m venv .venv
python -m pip install -e ".[dev]"
python -m playwright install chromium
flow2skill doctor
```

On Windows, use `.venv\Scripts\python.exe` and `.venv\Scripts\flow2skill.exe`.

## Before opening a pull request

```bash
ruff format --check src tests
ruff check src tests
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q
python -m build
python -m twine check dist/*
```

Add regression tests for every parser, redaction, manifest, replay, recorder, or Studio security change. A parser change must state whether unsupported input is rejected or preserved. Silent omission is not acceptable.

## Pull request rules

- Keep recordings synthetic. Never commit real credentials, cookies, storage state, private URLs, or generated raw captures.
- Preserve Python 3.10–3.13 and Windows/Linux/macOS behavior.
- Do not add shell execution around user-controlled recorder arguments.
- Do not weaken default input protection or action gates.
- Do not claim a workflow is proven without an executable assertion.
- Update `CHANGELOG.md` for user-visible changes.

## Reporting security issues

Follow [SECURITY.md](SECURITY.md). Do not disclose vulnerabilities or sensitive recordings in public issues.
