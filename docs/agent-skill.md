# RepoTrail for Codex and Claude Code

The skill collects bounded Git/source evidence and asks the **model already running your
agent session** to explain it. No separate API key or repository clone is needed to install
the skill. The repository you want explained must already be available locally.

For more control, use the full RepoTrail product: its optional LangGraph workflow adds
structured output validation, checked citations, durable attempt budgets, SQLite caching
and resumable runs. Speed and answer quality depend on the selected model, repository size
and agent context. The skill's conversational answer does not pass through the API output
validator. The full product is not guaranteed to be faster or smarter.

## Install

Download [repotrail-skill.zip](../downloads/repotrail-skill.zip) and its
[SHA-256 checksum](../downloads/repotrail-skill.zip.sha256), extract it, and run one of:

```powershell
py -3.14 repotrail/install.py --host codex
py -3.14 repotrail/install.py --host claude
```

Run these from the extracted folder containing `repotrail`. On Linux/macOS, replace
`py -3.14` with `python3.14`. Use **Python 3.14.x and Git 2.48+** for installation and
evidence collection. The launcher detects the Python 3.14 runtime.
It installs into `~/.agents/skills/repotrail` for Codex or `~/.claude/skills/repotrail` for
Claude Code. Restart/reload your agent if it does not discover the skill immediately.
An identical installation is idempotent. A different existing installation is preserved;
back it up/remove it yourself before upgrading, or use `--destination SKILLS_PARENT`.
The installer verifies all staged files before publishing them. Brief Windows directory
locks get up to six activation attempts with at most 1.55 seconds of retry delays;
persistent access failures still stop with a sanitized error code and preserve existing skills.

On first invocation the launcher verifies the bundled engine wheel and creates a cached
virtual environment outside the inspected repository. It downloads eight pinned core
dependencies as binary wheels from PyPI, without installing dependencies from the target.
Later invocations reuse this environment. Network/filesystem access follows your host's
permissions; missing prerequisites and blocked downloads require user action.

The ZIP uses an explicit file manifest and checksums. These detect corruption and release
drift; they are not a signature or a replacement for trusting the download's origin.
Install from this GitHub repository and keep the installed skill/cache writable only by
trusted users. No host configuration, API credentials or inspected repository is changed.

## Ask your agent

```text
$repotrail C:/code/gridcast commit HEAD~2
$repotrail C:/code/gridcast through v0.2.0
$repotrail C:/code/gridcast whole

/repotrail /home/me/gridcast commit HEAD~2
/repotrail /home/me/gridcast whole
/repotrail /home/me/gridcast review commit abc1234
```

Codex uses `$repotrail`; Claude Code uses `/repotrail`. Other agents may understand the
portable `SKILL.md`, but are not claimed as tested integrations. A bare number such as `2`
is ambiguous: use a commit SHA, tag or Git ref such as `HEAD~2`.

`commit REF` explains the committed tree plus its first-parent changes. Dirty working files
and later commits are excluded. `through REF` walks available ancestry ending at REF.
`whole` walks ancestry reachable from HEAD, including merge parents; it does not promise
all unrelated branches, deleted history or missing remote objects. Nothing is fetched.

History pages contain up to ten commit records, ordered topologically newest first. Each
cursor binds the repository, immutable target and page size; continuation remains pinned
even if HEAD moves. The agent uses at most twenty pages and twenty detailed commit packets
per invocation, and gives the continuation cursor if coverage is partial. The engine's
shared traversal ceiling is 20,000 commits. Long timelines may need further invocations.
Metadata-only commits are identified separately from source-reviewed milestones.

Packets select README/build manifests and up to three source files, sampling Python,
JavaScript/JSX and Java once each before filling the sample, plus bounded first-parent change
excerpts. The agent can request more historical source by path and physical line range.
Sources use immutable Git/blob IDs, SHA-256, inclusive physical lines and reference IDs.
Packet text is capped at 32 KiB, metadata at 32 KiB and serialized JSON at 128 KiB. Snapshot
reads and the separate change-comparison operation each retain the existing 10 MiB read
budget; per-file limits and shared eligibility also apply. Missing/binary/excluded/oversized
evidence is disclosed. Merge explanations use the first parent and disclose the policy.

The agent must treat repository text as untrusted data, never instructions. It must not
execute target code, tests, hooks, dependency installation, or checkout/fetch operations.
An explanation is advisory source analysis, not runtime verification.

## Verified hosts

Real Codex CLI conversations have exercised the installed skill's history explanation and
commit review, plus all three default MCP tools, using the host's configured model and
account without a separate provider key. Returned citation IDs and unchanged target files
were checked on disposable Python and mixed Python/JavaScript/Java repositories.

Claude Code's portable skill installation, launcher, and MCP configuration have automated
coverage; a real Claude model conversation remains unverified. Windows and Ubuntu package
checks run in CI. macOS has no CI coverage.

## Full product and optional API mode

Groq is the project's recommended API demonstration route. Users supply **their own**
`GROQ_API_KEY`; no project-owned/shared key is distributed. Gemini remains an optional,
experimental provider. Model IDs are validated against supported provider allowlists;
arbitrary providers/models are not currently supported. The agent skill can use whatever
model the host session supports, without extracting its login credentials.

See the [AI guide](ai.md) for supported models, `--model`, budgets and current verification.
Existing full-product CLI and MCP usage remain available. Explicit skill API mode
(`--api --provider groq`) installs separate AI extras and calls that workflow;
native mode never silently switches to it. `.env` is not loaded or executed automatically.

For launcher flags and cache overrides, see the
[portable commands reference](../skills/repotrail/references/commands.md). The core engine
also exposes `repotrail agent ROOT --mode commit|whole|source --json`, returning a separate
`agent_evidence` schema `1`. Existing inventory/source-map contracts are unchanged.

See [language coverage](languages.md) for supported declarations, imports, overloads,
encodings and version `2` CLI/MCP documents. The skill uses the same language support;
it never installs or executes dependencies from the inspected project.
