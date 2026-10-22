# RepoTrail

[![Checks](https://github.com/PuravDesai004/RepoTrail/actions/workflows/ci.yml/badge.svg)](https://github.com/PuravDesai004/RepoTrail/actions/workflows/ci.yml)
[![Python 3.14](https://img.shields.io/badge/python-3.14-blue)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**Repository context you can trace back to the source.**

RepoTrail inspects a local Python Git repository, maps declarations, retrieves exact source,
and compares committed changes. Use the CLI, Python API, or MCP tools to give yourself
and your coding agent evidence with paths, line numbers, and content hashes.
Inspected code is never imported, executed, installed, tested, or modified.

- **Understand a repository:** inventory, Python symbols, README, package declarations,
  entry points, and local history.
- **Find relevant code:** retrieve declarations with decorators, numbered source, and SHA-256 references.
- **Inspect changes:** compare two local commits with patches, historical references,
  and overlapping Python symbols.
- **Connect an agent:** expose the same APIs through a repository-bound MCP server.
- **Opt into AI:** explanations and diff reviews through explicitly selected Groq or Gemini,
  with checked citations, bounded attempts, local cache, and resumable runs.

## Use your agent's model — no API key

Download the [RepoTrail skill ZIP](downloads/repotrail-skill.zip), extract it and run
`python repotrail/install.py --host codex` or `--host claude`. With Python 3.14 and Git
available, the skill automatically prepares its isolated core runtime on first use.

```text
$repotrail C:/code/gridcast commit HEAD~2
/repotrail /home/me/gridcast whole
```

Codex uses `$repotrail`; Claude Code uses `/repotrail`. Your current agent model explains
the evidence. Commit snapshots and paged available-history explanations need no separate
provider key. See the [skill guide](docs/agent-skill.md) for setup, scope and coverage limits.

Use the **full product** below for more control, structured validation, caching and the
resumable LangGraph/API workflow. Performance depends on the model and repository; the
skill is the convenient entry point. Groq is the recommended API option, using your own
key. Gemini is optional/experimental; supported models are listed in the [AI guide](docs/ai.md).

## Install the full product

Requires **Python 3.14.x** and **Git 2.48+**. Install from this repository; there is no PyPI
release. Core commands need no API key. Install `.` for core only or `.[ai,mcp]` for both integrations.

<details open>
<summary>Windows / PowerShell</summary>

```powershell
git clone https://github.com/PuravDesai004/RepoTrail.git
cd RepoTrail
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install '.[ai,mcp]'
$rt = '.\.venv\Scripts\repotrail.exe'
& $rt --help
```

</details>

<details>
<summary>Linux / macOS</summary>

```bash
git clone https://github.com/PuravDesai004/RepoTrail.git
cd RepoTrail
python3.14 -m venv .venv
.venv/bin/python -m pip install '.[ai,mcp]'
.venv/bin/repotrail --help
```

Windows and Ubuntu are covered by CI. macOS uses portable paths but has no CI coverage.

</details>

## Try it in a minute

PowerShell, continuing from the install above:

```powershell
# Create a disposable repository with two commits.
& $rt demo .local/demo --json
& $rt overview .local/demo
& $rt context .local/demo pricing.py total_price
& $rt changes .local/demo HEAD~1 HEAD --json
```

On Linux/macOS, replace `& $rt` with `.venv/bin/repotrail`. The demo includes a binary
change, so `changes` returns exit code **1** with valid, explicitly incomplete evidence.
It also contains a discount-formula regression for review. No external repository is changed.

For your repository, replace `.local/demo` with its Git working-tree root:

```powershell
& $rt overview C:/code/my-project --json
& $rt symbols C:/code/my-project --json
& $rt context C:/code/my-project src/app.py App.run --json
& $rt changes C:/code/my-project main HEAD --json
```

`overview` includes dirty and eligible untracked Python source. `changes` compares **two
committed trees**. Omit `--json` for human-readable output.
See the [CLI reference](docs/cli.md) for commands and exit codes.

## Optional AI analysis

```powershell
$env:GROQ_API_KEY = '<your-key>'
& $rt explain .local/demo --provider groq --task explain_project --json
& $rt explain .local/demo --provider groq --task review_diff --base-ref HEAD~1 --head-ref HEAD --json
```

AI sends selected eligible excerpts to your explicitly selected provider. Each run permits at most two provider
attempts; citations are checked against retained evidence. Read the [AI guide](docs/ai.md)
before using private source. Credentials come from the process environment; `.env` files
are not loaded automatically.

Groq uses strict structured output with `openai/gpt-oss-20b` by default when selected.
The Gemini default remains available for compatibility; it never falls back to Groq.
See [verification and release status](docs/status.md) for actual provider results and limits.

## Use it from an agent or Python

Start the local MCP server with `repotrail mcp --repo /absolute/path/to/repository`.
It exposes `get_project_overview`, `get_symbol_context`, and `summarize_changes`.
AI tools require `--enable-ai`. See the [MCP guide](docs/mcp.md),
[client example](examples/mcp_client.py), and [configuration template](examples/mcp-config.json).

```python
from repotrail.evidence import get_project_overview, get_symbol_context
from repotrail.history import compare_revisions

overview = get_project_overview('/path/to/repository')
context = get_symbol_context('/path/to/repository', 'src/app.py', 'App.run')
changes = compare_revisions('/path/to/repository', 'main', 'HEAD')
print(context.model_dump_json(indent=2))
```

## Boundaries

RepoTrail reads local files and existing Git objects. It never checks out or fetches an
inspected repository. It excludes known environment/build paths, credential-like names,
symlinks, submodules, and Windows reparse points. Filename filters are not a secret scanner.
Unavailable, binary, oversized, or unparseable evidence is reported explicitly.

A source map describes syntax, not runtime behavior. A valid citation confirms its source
reference, not the correctness of an AI interpretation. See [limits](docs/limits.md)
and [architecture](docs/architecture.md) for contracts and tradeoffs.

## Development

```text
src/repotrail/   Application, Python API, CLI, and MCP server
tests/          Offline contract, workflow, and integration tests
evals/          Scripted grounding and cache evaluation
examples/       MCP client and host configuration example
scripts/        Opt-in live smoke and clean wheel verification
skills/         Portable Codex / Claude Code skill and bundled engine
downloads/      Verified skill ZIP and SHA-256 checksum
docs/           Usage, configuration, architecture, and release status
.github/        Windows and Ubuntu CI
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup and verification commands.
Issues and pull requests are welcome. Licensed under [MIT](LICENSE).
