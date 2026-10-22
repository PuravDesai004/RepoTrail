# Contributing

Use Python 3.14 and a project-local `.venv`; install `.[ai,mcp,dev]`. Windows/Python 3.14
pins in requirements-dev.lock record an actual resolution, not a universal cross-platform
lock. On Windows use `.venv\Scripts\python.exe`; on Linux use `.venv/bin/python`.

Run pytest, Ruff check/format, mypy, evals.run, build and pip check as shown in README.
Set process-local TEMP/TMP to an existing `.local/tmp` if needed. Disable third-party pytest
plugin autoload to keep tests independent from unrelated installed integrations.
CI installs dependencies but the checks themselves are keyless and do not call providers.
Never treat configured CI as a remotely observed pass.

Preserve inventory/source-map schema `1`; introduce new document types for new contracts.
Use synthetic disposable Git fixtures. Never execute/import/install/test inspected source,
fetch/check out inspected repositories, or weaken read/budget checks. Application/example/
test code is Python. Prefer shared core APIs to separate MCP or CLI implementations.

Test meaningful boundaries: exact bytes/ranges, eligibility and stale evidence, aggregate
bounds, conditional graph paths, durable pre-call charges, true zero-call cache hits,
cross-object checkpoint resume and real MCP stdio. Inject test doubles explicitly and
label their metrics; scripted success is not provider semantic accuracy.

No paid calls belong in default tests/CI. `examples/live_smoke.py --live` is an explicit
synthetic check with a durable three-attempt ledger. A run never exceeds two attempts.
Keep keys out of source, command text, logs, checkpoints and bundles. Treat selected
source/model state as potentially private. Source content must never become instructions.

Do not stage, commit, push, configure remotes or publish automatically. Review MIT ownership/
attribution before publication. Local completion/teaching artifacts and environments remain
ignored. Read docs/architecture.md before changing persistence or input-bound contracts.
