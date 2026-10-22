# Gemini configuration and troubleshooting

Install `.[ai]` from the checkout in its own environment. AI runs only when explicitly
requested by `explain` or an enabled AI MCP tool. Selected source is sent to Google;
review eligibility and contents before using private code. Deterministic commands need no key.

## Credentials and model

PowerShell:

```powershell
$env:GEMINI_API_KEY = '<your-key>'
repotrail explain C:/code/project --task explain_project --json
repotrail explain C:/code/project --task review_diff --base-ref main --head-ref HEAD --json
```

Bash:

```bash
export GEMINI_API_KEY='<your-key>'
repotrail explain /path/to/project --task explain_project --json
```

Use the installed executable from your environment, or activate it first.
`GOOGLE_API_KEY` is also accepted; `GEMINI_API_KEY` takes precedence. RepoTrail reads the
current process environment. A `.env` file alone does not configure it. PowerShell
assignments such as `$env:GEMINI_API_KEY = '...'` are shell commands, not dotenv syntax.
Use your shell or a trusted environment loader; never execute an inspected `.env` file.

`--model` explicitly selects `gemini-3.8-flash` (default), `gemini-3.6-flash`, or
`gemini-3.5-flash-lite`. Models are never silently substituted. Account availability is
required. See [Google's model documentation](https://ai.google.dev/gemini-api/docs/models).
`GenerationConfig.thinking_level` defaults to `low` and participates in cache/resume identity.
See [current live verification status](status.md).

## Budgets, grounding, and state

Each workflow permits at most **two actual provider attempts**, including one repair or
transient retry. `--max-attempts 1` lowers that limit. The adapter uses one total SDK request
per attempt, a 30-second timeout, and a 1,500-token output limit. A durable charge precedes
the call; a crash can consume an attempt without a response. Billing is not exactly-once.
Selected source and prompt/schema metadata each have a 32 KiB input limit.

Malformed schemas and unknown citation IDs are rejected. Claims/findings cite retained
source references. Observations and inferred consequences are separate fields. Structural
validation does not establish semantic correctness or coverage of omitted code.

Cache identity includes repository identity, the full observed evidence manifest, selected
references/text, task, backend/model, settings, and policy versions. Dirty source or untracked
membership can invalidate a cache with unchanged HEAD. Valid cache hits make zero provider
calls; schemas/citations are revalidated.

State defaults to `%LOCALAPPDATA%/RepoTrail` on Windows, Application Support on macOS,
and `$XDG_STATE_HOME/repotrail` or `~/.local/state/repotrail` on Linux. Override using
`--state-dir` or `REPOTRAIL_STATE_DIR`. Inside an inspected repository, only intentionally
excluded locations such as `.local` are accepted.

`cache.sqlite3` and `checkpoints.sqlite3` contain selected source and model output; keep
them private. Retention is 128 cache entries and 256 app runs. `cache-prune ROOT --keep 50`
prunes older runs and graph checkpoints. Remote tracing is disabled.
`REPOTRAIL_LOG_LEVEL=INFO` enables sanitized timing events on stderr.

## Errors

| Result | Meaning / next step |
| --- | --- |
| Missing credentials | Export a valid key into the same process that launches RepoTrail. |
| `provider_configuration` | Check key, model access, and request configuration; HTTP 400/401/403/404 is terminal. |
| `provider_unavailable` | Google returned a 5xx service error. Try a later explicit run; never erase charges. |
| `rate_limit` | Check account quota; a remaining graph attempt may retry once. |
| `timeout` / `provider_connection` | Bounded request could not complete; check network/service access. |
| `invalid_schema` / `invalid_output` | Response was rejected; a remaining graph attempt may repair it. |
| `unsupported_citation` | Model cited outside the selected registry; the answer is rejected. |
| Stale result | Evidence/configuration changed; start a new run. |

Exception bodies are not printed because they may contain request data or keys. Reported
token usage is returned when available; missing usage is not zero cost. The explicit
[synthetic live smoke](../CONTRIBUTING.md#optional-live-verification) avoids uploading your source.
