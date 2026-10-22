# Verification and release status

The deterministic CLI, Python API, and local MCP tools are implemented. Optional Gemini
analysis is **experimental** until live explanation and review pass their acceptance checks.
Offline correctness checks and real-provider results are tracked separately.

## Verified

- Windows and Ubuntu Python 3.14 CI passed the complete offline suite, lint/format checks,
  type checking, scripted grounding/cache evaluation, builds, and clean wheel installation.
- The installed-wheel check exercises core-only commands before installing AI/MCP extras,
  then checkpoint pause/resume without credentials and the official MCP stdio client.
- Offline tests cover schema/citation rejection, bounded repair, durable charges, cache
  invalidation, zero-call cache hits, and stale cross-object resume. Default CI is keyless.

The README CI badge links to current branch results. These checks use synthetic fixtures
and injected models; they do not demonstrate real-model accuracy. macOS and a particular
desktop MCP host have not been tested.

## Live Gemini check — 22 October 2026

| Task / explicitly selected model | Observed result |
| --- | --- |
| Project explanation / `gemini-3.8-flash` | HTTP 503 high demand; `provider_unavailable`; no answer. |
| Diff review / `gemini-3.8-flash` | HTTP 503 high demand; `provider_unavailable`; no answer. |
| Diff review / `gemini-3.5-flash-lite` | Output/usage received; structured response rejected as `invalid_schema`; no accepted answer. |

Each check used one charged attempt on synthetic source. No automatic fallback occurred.
No successful live explanation or review is claimed.

## Remaining acceptance gates

1. Diagnose the live structured-response failure and obtain schema/citation-valid
   explanation and review using a real supported Gemini model within an explicit budget.
2. Review answers against the known fixture: explanation must match source; review should
   identify the fractional-discount regression and acknowledge incomplete evidence.
3. Verify live cache reuse makes zero additional calls and retain reproducible sanitized
   integration evidence. Offline tests already exercise this behavior.

After these gates pass, AI can leave experimental status. Core-only use is available now.
PyPI publication, broader Python support, macOS CI, desktop-specific MCP packaging, a web UI,
and other languages are separate future work, not required for the current local tool.
