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

The skill uses **the model already running in your Codex or Claude Code session**.
You do not need a separate API key or a clone of RepoTrail.

### 1. Download and extract

Have **Python 3.14.x**, **Git 2.48+**, and Codex or Claude Code installed.

[Download repotrail-skill.zip](https://github.com/PuravDesai004/RepoTrail/raw/refs/heads/main/downloads/repotrail-skill.zip)
and extract the complete ZIP. The [SHA-256 checksum](downloads/repotrail-skill.zip.sha256)
is also available. Open a terminal in the extracted folder that contains the `repotrail`
directory; the installer should be at `repotrail/install.py` relative to that terminal.

### 2. Install once for your host

Run the command for your host, or both commands if you use both.

**Windows / PowerShell:**

```powershell
# Codex
py -3.14 repotrail/install.py --host codex

# Claude Code
py -3.14 repotrail/install.py --host claude
```

**Linux / macOS:**

```bash
# Codex
python3.14 repotrail/install.py --host codex

# Claude Code
python3.14 repotrail/install.py --host claude
```

The installer places the skill in `~/.agents/skills/repotrail` for Codex or
`~/.claude/skills/repotrail` for Claude Code, so it is available across your repositories.
Restart the host or start a new session if the skill does not appear.

### 3. Open your repository and ask

Open your **own local Git repository** in Codex, or start Claude Code from its root.
Enter these prompts in the agent chat:

| What you want | Codex | Claude Code |
| --- | --- | --- |
| Explain the available project history | `$repotrail . whole` | `/repotrail . whole` |
| Explain a committed snapshot and its changes | `$repotrail . commit HEAD~2` | `/repotrail . commit HEAD~2` |
| Explain history ending at a tag or commit | `$repotrail . through v1.0` | `/repotrail . through v1.0` |

`.` means the repository open in your agent. You can also provide its absolute local path.
Replace `HEAD~2` or `v1.0` with a commit SHA, tag, or Git ref that exists in your repository.
Use `commit REF` for a snapshot and `through REF` for the timeline ending there.

On first use, RepoTrail automatically creates an isolated runtime and downloads its pinned
core dependencies from PyPI. Allow setup access if your host asks; later uses reuse the
runtime. Your current agent then explains the evidence with source citations. No Groq or
Gemini key is needed for this workflow.

`whole` follows history reachable from the selected local HEAD. Long histories are paged
and may need continuation; missing history and source coverage limits are disclosed.
See the [skill guide](docs/agent-skill.md) for more setup details and supported scope.

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

## Use it on your repository

PowerShell, continuing from the install above:

```powershell
& $rt overview C:/code/my-project --json
& $rt symbols C:/code/my-project --json
& $rt context C:/code/my-project src/app.py App.run --json
& $rt changes C:/code/my-project main HEAD --json
```

`overview` includes dirty and eligible untracked Python source. `changes` compares **two
committed trees**. Omit `--json` for human-readable output.
On Linux/macOS, replace `& $rt` with `.venv/bin/repotrail`.
See the [CLI reference](docs/cli.md) for commands and exit codes.

## Optional AI analysis

```powershell
$env:GROQ_API_KEY = '<your-key>'
& $rt explain C:/code/my-project --provider groq --task explain_project --json
& $rt explain C:/code/my-project --provider groq --task review_diff --base-ref main --head-ref HEAD --json
```

AI sends selected eligible excerpts to your explicitly selected provider. Each run permits at most two provider
attempts; citations are checked against retained evidence. Read the [AI guide](docs/ai.md)
before using private source. Credentials come from the process environment; `.env` files
are not loaded automatically.

Groq uses strict structured output with `openai/gpt-oss-20b` by default when selected.
The Gemini default remains available for compatibility; it never falls back to Groq.
Gemini remains experimental; see the [AI guide](docs/ai.md) for configuration and limitations.

## Use it from an agent or Python

Start the local MCP server with `repotrail mcp --repo /absolute/path/to/repository`.
It exposes `get_project_overview`, `get_symbol_context`, and `summarize_changes`.
AI tools require `--enable-ai`. See the [MCP guide](docs/mcp.md) for configuration and tools.

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
for supported scope and tradeoffs.

## Product files

```text
src/repotrail/   Application, Python API, CLI, and MCP server
skills/         Portable Codex / Claude Code skill and bundled engine
downloads/      Verified skill ZIP and SHA-256 checksum
docs/           Installation, CLI, API providers, MCP, and supported limits
.github/        Windows and Ubuntu package checks
```

Issues and pull requests are welcome. Licensed under [MIT](LICENSE).
