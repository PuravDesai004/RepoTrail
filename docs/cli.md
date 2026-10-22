# CLI reference

Run `repotrail --help` or `repotrail COMMAND --help`. `python -m repotrail` is equivalent.
`ROOT` is the Git working-tree root; paths inside it use relative POSIX slashes. Add
`--json` for the validated document. Human output renders the same fields. JSON escapes
Unicode for Windows console compatibility.

| Command | Purpose |
| --- | --- |
| `inspect ROOT [--max-files N]` | Tracked and eligible nonignored untracked files. |
| `symbols ROOT` | Python definitions, imports, decorators, and diagnostics. |
| `overview ROOT` | Inventory, source map, README, packaging, and local history. |
| `context ROOT PATH QUALIFIED_NAME [--definition-line N]` | Exact declaration, numbered lines, hash, and citation. |
| `history ROOT [--ref HEAD] [--limit 20]` | Bounded local commit metadata. |
| `changes ROOT BASE HEAD` | Committed endpoint trees, statuses, patches, and symbol overlap. |
| `explain ROOT [--task TASK]` | Explicit AI workflow; default `explain_project`. |
| `resume ROOT RUN_ID` | Continue a persisted workflow after checking evidence/configuration. |
| `cache-prune ROOT [--keep 100]` | Prune older app runs/cache and corresponding graph checkpoints. |
| `demo DESTINATION` | Synthetic two-commit repository in a new or empty directory. |
| `mcp --repo ROOT [--enable-ai]` | Repository-bound stdio MCP server. |

Duplicate symbol names return candidates; repeat `context` with one candidate's
`--definition-line`. Names describe lexical scopes, not import resolution or dynamic dispatch.
`changes` reads existing local objects, excludes uncommitted edits, and represents renames
as deletion plus addition. It never fetches or checks out revisions.

## AI arguments

`explain --task` accepts `explain_project`, `explain_changes`, and `review_diff`.
Change tasks require `--base-ref REF --head-ref REF`. `--provider {gemini,groq}`, `--model`, `--state-dir`, and
`--max-attempts {1,2}` configure a run. Pause before any provider request:

```powershell
repotrail explain C:/code/project --run-id review-session --pause-before-generate --json
repotrail resume C:/code/project review-session --json
```

Resume with the same provider, model, attempt setting, repository, source, and comparison refs.
Changes reject the checkpoint as stale. IDs cannot erase charges. See [AI configuration](ai.md).

## Exit codes

| Interface | `0` | `1` | `2` |
| --- | --- | --- | --- |
| `inspect` | Valid inventory | — | Fatal error |
| Evidence commands | Complete | Valid incomplete/unavailable result | Fatal error |
| `explain`, `resume` | Completed | Failed, incomplete, interrupted, or stale | Invocation/setup error |
| `demo`, `cache-prune` | Success | — | Invocation/setup error |

Fatal errors go to stderr without partial stdout. Exit `1` can accompany useful evidence;
inspect completeness, status, and diagnostics. MCP reserves stdout for protocol messages.
