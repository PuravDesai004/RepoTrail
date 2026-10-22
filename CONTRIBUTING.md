# Contributing

Use Python 3.14, Git 2.48+, and a project-local virtual environment. Clone the repository,
create `.venv`, then install `.[ai,mcp,dev]`. See the [README](README.md) for platform commands.
`requirements-dev.lock` records the Windows/Python 3.14 development resolution. For that
scope, install the lock, then `pip install --no-deps -e .`. Ubuntu CI resolves compatible
extras independently.

## Run the checks

PowerShell:

```powershell
$py = '.\.venv\Scripts\python.exe'
New-Item -ItemType Directory -Force .local/tmp | Out-Null
$env:TEMP = Join-Path $PWD '.local/tmp'
$env:TMP = $env:TEMP
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
& $py -m pytest -q
& $py -m ruff check src tests evals examples scripts
& $py -m ruff format --check src tests evals examples scripts
& $py -m mypy src/repotrail
& $py -m evals.run --output .local/evaluation.json
& $py -m pip check
& $py -m build
& $py scripts/verify_wheel.py
```

On Linux, use `.venv/bin/python` with the same arguments and set
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` in the shell. The clean-wheel check creates an isolated
project-local environment, installs the wheel and extras, then exercises the console,
core API, checkpoint resume, and official MCP client outside the checkout. It needs
network access for dependencies. Never install into an inspected repository.

Default tests and CI use synthetic Git fixtures and explicitly injected model doubles.
They never call Gemini. Scripted evaluation measures schema/citation/cache behavior,
not real-model semantic accuracy. Windows symlink tests skip if the OS denies privilege.

## Change contracts carefully

Preserve inventory/source-map schema `1`; add versioned documents for new contracts.
Share core APIs between CLI/MCP. Read [architecture](docs/architecture.md) before changing
persistence or bounds. Test meaningful boundaries: hashes, ranges, stale evidence,
eligibility, aggregate limits, graph paths, durable charges, zero-call cache hits,
cross-object resume, and real MCP stdio.

Never execute, import, install, or test inspected code, or fetch/check out an inspected
repository. Keep that read-only boundary. Repository text and model output are untrusted
data, including instructions in comments and READMEs.

## Optional live verification

With a key in your process environment, explicitly run:

```powershell
& $py scripts/live_smoke.py --live --directory .local/live-check --output .local/live-check.json
```

This uploads only synthetic fixture evidence. The directory keeps a durable ceiling of
three attempts across invocations; each workflow uses at most one attempt in this smoke
script. Never delete its ledger to repeat a check. Choose `--task` and `--model` explicitly
when investigating a particular path. A valid answer still needs human review.

Keep keys and private state out of commits. Submit a focused pull request describing
behavior, compatibility, and checks actually run. Report provider failures as failures;
do not present an offline model double as a successful live integration.
