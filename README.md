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
release. Core and default MCP commands need no API key. The commands below install `.[mcp]`;
use `.` for core only or `.[ai,mcp]` to also install the optional provider/LangGraph integration.

<details open>
<summary>Windows / PowerShell</summary>

```powershell
git clone https://github.com/PuravDesai004/RepoTrail.git
cd RepoTrail
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install '.[mcp]'
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
.venv/bin/python -m pip install '.[mcp]'
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

From the RepoTrail installation directory, add the AI extra before using provider mode:

```powershell
.\.venv\Scripts\python.exe -m pip install '.[ai,mcp]'
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

## Connect Codex or Claude Code through MCP — no API key

MCP gives your agent callable RepoTrail tools. **Your current Codex or Claude Code model
explains the returned evidence**; RepoTrail makes no model API calls in this default mode.
Your host's normal account, model access, and usage limits still apply. The skill above
and MCP are independent entry points; you can use either or both.

### 1. Install the MCP extra

Follow [Install the full product](#install-the-full-product) above, which includes `.[mcp]`.
If you previously installed core only, run its pip command again with `'.[mcp]'`.
Keep the RepoTrail installation separate from the repository you want to inspect.

In the examples below, replace **both absolute paths**: RepoTrail's installed Python interpreter
and your own local Git repository. Keep the quotes if a path contains spaces.
The agent starts this local **stdio** process automatically; no server URL or port is needed.

### 2a. Register with Codex

Run in a terminal with the Codex CLI available:

**Windows / PowerShell:**

```powershell
codex mcp add repotrail -- "C:/tools/RepoTrail/.venv/Scripts/python.exe" -m repotrail mcp --repo "C:/code/my-project"
codex mcp list
```

**Linux / macOS:**

```bash
codex mcp add repotrail -- "/absolute/path/RepoTrail/.venv/bin/python" -m repotrail mcp --repo "/absolute/path/my-project"
codex mcp list
```

Alternatively, add this table to `~/.codex/config.toml`, preserving existing settings:

```toml
[mcp_servers.repotrail]
command = "C:/tools/RepoTrail/.venv/Scripts/python.exe"
args = ["-m", "repotrail", "mcp", "--repo", "C:/code/my-project"]
```

For Linux/macOS, use the absolute interpreter/repository paths from the Bash example.
Restart your Codex app, IDE extension, or CLI session after configuration. In the CLI,
run `/mcp` to check the active tools. `codex mcp list` confirms registration; a successful
tool call confirms the connection. See [official Codex MCP setup](https://developers.openai.com/codex/mcp/).

### 2b. Register with Claude Code

Open a terminal in the repository you want explained, then run:

**Windows / PowerShell:**

```powershell
cd "C:/code/my-project"
claude mcp add --transport stdio --scope local repotrail -- "C:/tools/RepoTrail/.venv/Scripts/python.exe" -m repotrail mcp --repo "C:/code/my-project"
claude mcp get repotrail
```

**Linux / macOS:**

```bash
cd "/absolute/path/my-project"
claude mcp add --transport stdio --scope local repotrail -- "/absolute/path/RepoTrail/.venv/bin/python" -m repotrail mcp --repo "/absolute/path/my-project"
claude mcp get repotrail
```

`--scope local` keeps this registration private to you and available in that project.
Start a new Claude Code session there and run `/mcp`; confirm `repotrail` is connected
and its three tools are available. See [official Claude Code MCP setup](https://code.claude.com/docs/en/mcp).

### 3. Ask your agent to use RepoTrail

Enter a normal chat prompt in either host; MCP does not require a skill slash command:

> Use RepoTrail's get_project_overview to explain this project's structure and entry points.
> Cite the evidence and disclose any missing coverage.

> Use RepoTrail's get_symbol_context for src/app.py, qualified_name App.run.
> Explain it using your current model and cite its path and line numbers.

> Use RepoTrail's summarize_changes between main and HEAD. Explain the changes and review
> possible risks using your current model, with citations and coverage limits.

Replace paths, qualified names, and refs with values present in your repository; use the
overview's source map to find symbols. Both refs must exist locally. `summarize_changes`
compares committed endpoints, so uncommitted edits are excluded. For paged whole-history
explanations, use the [skill's `whole` / `through` workflow](docs/agent-skill.md).

Each MCP process stays bound to the repository passed in `--repo`, even if you open another
project. To change it, remove the registration and repeat setup with the new path:
`codex mcp remove repotrail`, or `claude mcp remove repotrail --scope local` from the original
Claude project. Alternatively, use a distinct server name for each repository.

If tools do not appear, check both absolute paths, confirm `.[mcp]` is installed in that
interpreter's environment, and restart the host. For startup diagnosis, run that Python
with `-m repotrail mcp --repo YOUR_PATH` in a terminal: it waits for MCP input when healthy; stop it with
Ctrl+C and let your host launch it. See the [MCP guide](docs/mcp.md) for configuration details.

**Optional provider mode:** `--enable-ai --provider groq` adds `explain_project` and
`review_diff`, which use LangGraph and your own `GROQ_API_KEY`. This requires the AI extra
and deliberate credential forwarding; see the [AI guide](docs/ai.md). Leave `--enable-ai`
out to keep explanations and reviews in your current agent. No key belongs in the default
MCP configuration.

## Use the Python API

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
