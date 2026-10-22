# Verification and release status

The deterministic CLI, Python API, and local MCP tools are implemented. Groq-backed
project explanation, change explanation, and diff review have passed bounded real-provider
checks. Gemini remains experimental: its schema/prompt correction is covered offline,
while successful generation with that provider has not been revalidated.

## Groq acceptance — 22 October 2026

The explicitly selected provider/model was `groq` / `openai/gpt-oss-20b` using strict
structured output. Only synthetic fixture source was uploaded.

| Final workflow | Observed result | Recreated-service cache check |
| --- | --- | --- |
| Project explanation | Accepted schema and registered citations; completed. | Hit, zero provider calls. |
| Change explanation | Accepted schema/citations; explicitly incomplete because binary evidence is unavailable. | Hit, zero provider calls. |
| Diff review | Accepted schema/citations; identified the fractional-discount regression and disclosed unavailable binary contents. | Hit, zero provider calls. |

The review explained that `[10, 20]` with a fractional discount of `0.1` should produce
`27.0`, while the changed subtraction formula produces `29.9`. Its finding remained an
inference with references to the old/new source. Explanation spot-checks confirmed the
package declaration, entry point, and supplied pricing implementation.

The round used six charged attempts including prompt refinement and one rejected HTTP400
change-explanation request. Each final successful workflow used one attempt. The ledger
was preserved; no provider fallback or budget reset occurred. Missing usage on a failed
request is not evidence of zero cost. Cache checks make no extra requests.

This is a fixture-based acceptance check, not a general semantic-accuracy benchmark.
Model prose can still misstate names or mix observations with interpretation; returned
citation metadata is authoritative for paths/ranges, and users should review the source.

## Offline and delivery checks

Version 1.1 adds a downloadable native-model skill for Codex and Claude Code. Its installer
and keyless launcher have automated checks; actual host UI discovery and Claude session
invocation remain manual validation. The skill inherits the current session model and does
not distribute provider keys. See the [skill guide](agent-skill.md) for prerequisites,
whole-history coverage limits and the full-product/API tradeoff.

Windows and Ubuntu Python3.14 CI previously passed the full offline suite, lint/format,
type checks, scripted grounding/cache evaluation, builds, and clean wheel installation.
The README badge links to the latest branch results; consult that run before assuming
that a newer commit passed remotely. Maintainer tests use injected models and remain local.
Public CI checks builds, bundle contents, lint and types; it makes no provider calls.

The installed-wheel check exercises core-only commands before AI/MCP extras, then
checkpoint pause/resume without credentials and the official MCP stdio client outside
the checkout. Provider regressions verify strict schemas, SDK attempt settings, provider
identity, invalid basis rejection, and sanitized failures. macOS and a particular desktop
MCP host have not been tested.

## Gemini status

Earlier default-model requests returned HTTP503 high demand. A diagnostic Flash Lite
response finished normally but labeled a review finding `observed`; the contract requires
`inferred`. The schema now includes the supported singleton enum and explicit descriptions,
and the prompt separates observed edits from inferred findings. Local validation remains
strict and never relabels a response automatically. Gemini has not been retested live after
this correction; no successful Gemini integration is claimed.

## Scope of v1

The requested Groq explanation/review/cache acceptance gates are complete. Treat AI as
advisory interpretation over bounded source, and inspect diagnostics on incomplete results.
A demo of the local evidence tool and its verified Groq integration is ready to share.
PyPI publication, broader Python support, macOS CI, desktop-specific MCP packaging, a web
UI, other languages, and renewed Gemini acceptance are future work.
