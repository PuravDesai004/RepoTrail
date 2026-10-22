# RepoTrail agent skill

The current model in Codex or Claude Code explains verified local repository evidence.
No separate API key or RepoTrail clone is needed. Python 3.14 and Git 2.48+ must be installed.
First use needs PyPI access for pinned core dependencies; later runs reuse an isolated cache.

After downloading/extracting the complete bundle, install for one or both hosts:

```text
python repotrail/install.py --host codex
python repotrail/install.py --host claude
```

Use `python3` or `py` if that is your Python command. The installer copies this verified
folder to `~/.agents/skills/repotrail` for Codex or `~/.claude/skills/repotrail` for Claude
Code. It does not edit global configuration or overwrite a different existing skill.
Restart the host if needed to discover it. You can install without network; first invocation
prepares the bundled engine and its pinned dependencies in a user-local virtual environment.

```text
Codex:       $repotrail gridcast commit HEAD~2
Claude Code: /repotrail gridcast commit HEAD~2

$repotrail gridcast whole
/repotrail gridcast through v1.0
```

Run these inside an accessible local repository, or give the agent its absolute local path.
`commit` reads that immutable snapshot; `through` ends history at that ref; `whole` follows
all available reachable history with pages and explicit coverage limits. No checkout/fetch
is performed. Binary and omitted source remain gaps. The agent writes the explanation
in the conversation and cites returned source references. Its prose still needs source review.

The launcher accepts `--cache-dir` for a permitted cache outside the inspected repository.
Setup may require host approval. It never installs Python/Git globally or target-project
dependencies. Optional `--api --provider groq|gemini` keeps the standalone LangGraph mode
available and requires the user's process API key. Native mode never falls back to API mode.

See `SKILL.md` and `references/commands.md` for the exact workflow. The bundled MIT license
applies to project code; dependencies retain their own licenses.

For more control, use the full RepoTrail CLI/MCP product: optional LangGraph mode provides
structured validation, caching and resumable runs. Speed and answer quality depend on
the model and repository. Groq is the recommended API demonstration route, with each
user's own key; no shared project key is distributed. Gemini remains experimental.
API mode supports the documented Groq/Gemini model IDs, not arbitrary providers.
Use `--model` to select a supported model. Native skill mode inherits the host model.
