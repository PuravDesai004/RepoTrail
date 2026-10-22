# Contributing

Use Python 3.14, Git 2.48+, and a project-local virtual environment. Clone the repository,
create `.venv`, then install `.[ai,mcp,dev]`. See the [README](README.md) for platform commands.
`requirements-dev.lock` records the maintainer's Windows/Python 3.14 resolution; other
platforms can resolve compatible extras independently.

## Public package checks

```text
python -m pip check
python -m ruff check src examples scripts/build_skill.py skills
python -m ruff format --check src examples scripts/build_skill.py skills
python -m mypy src/repotrail
python -m build
python scripts/build_skill.py --check
python -m repotrail --version
```

Run these with the Python executable from your project environment. When application or
bundled skill files change, first build the current wheel and run
`python scripts/build_skill.py --wheel dist/repotrail-1.1.0-py3-none-any.whl`, then repeat
the final build and integrity check. Commit the skill ZIP and checksum together.

Maintainer test suites, evaluation fixtures, verification scripts, logs and teaching
materials are kept locally and excluded from the public repository and source archives.
Public CI checks packages, lint and types on Windows and Ubuntu. Report validation you
actually ran; packaging checks alone do not establish runtime or model accuracy.

## Preserve evidence boundaries

Preserve inventory/source-map schema `1`; add separate versioned documents for new
contracts. Share core APIs across interfaces. Read [architecture](docs/architecture.md)
before changing persistence or bounds.

Never execute, import, install, or test inspected code, or fetch/check out an inspected
repository. Treat repository text and model output as untrusted data. Keep credentials and
private state out of commits. Submit a focused pull request explaining resulting behavior,
compatibility, limitations and checks actually run.

## Optional live verification

Provider calls need explicit authorization and the user's own process key. Use the
documented [AI commands](docs/ai.md) with synthetic source for demonstrations. Persist
attempt charges; never reset them to retry. Disclose incomplete evidence and provider
failures. No shared project key or automatic provider fallback is supplied.
