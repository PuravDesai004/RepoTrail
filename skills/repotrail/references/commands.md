# Launcher commands

Replace `PYTHON`, `SKILL`, and `ROOT` with discovered executable/absolute paths. Python 3.14
and Git 2.48+ are prerequisites. The launcher may itself run under a recent Python 3, then
find Python 3.14 via its interpreter, `py -3.14`, or `python3.14`. A missing runtime is
reported; Python or Git are never installed globally.

```text
PYTHON "SKILL/scripts/repotrail.py" --repo "ROOT" --mode commit --ref "HEAD"
PYTHON "SKILL/scripts/repotrail.py" --repo "ROOT" --mode whole --ref "v1.0"
PYTHON "SKILL/scripts/repotrail.py" --repo "ROOT" --mode whole --cursor "CURSOR"
PYTHON "SKILL/scripts/repotrail.py" --repo "ROOT" --mode source --ref "SHA" --path "src/app.py" --start-line 1 --end-line 60
```

`--page-size` defaults to 10 and must be 1..10. Reuse the same size with a cursor. Each page
is at most 32 KiB metadata; retained numbered source is at most 32 KiB per packet. Historical
source uses the same file/aggregate/Git bounds as the application. A source excerpt is at
most 16 KiB and may be incomplete. JSON with exit 1 remains usable; disclose its omissions.
`--cache-dir` overrides the user-local runtime cache and must stay outside ROOT.

First setup needs PyPI network access. The engine wheel is included and hash-checked;
only its pinned core dependencies are downloaded. A checksum establishes bundle integrity,
not publisher identity: install from the trusted RepoTrail GitHub distribution.

Optional provider invocation, only after explicit user request:

```text
PYTHON "SKILL/scripts/repotrail.py" --repo "ROOT" --api --provider groq --task review_diff --base-ref "BASE" --ref "HEAD"
```

This installs AI extras in a separate isolated environment. Set GROQ_API_KEY for Groq or
GEMINI_API_KEY/GOOGLE_API_KEY for Gemini in the launching process. No `.env` is loaded and
no credentials are accepted as command arguments. Provider requests use existing bounded
LangGraph/cache/checkpoint logic. Default native mode calls no provider API.

Add `--model MODEL_ID` for a supported provider/model pair. Groq defaults to
`openai/gpt-oss-20b` and also supports `openai/gpt-oss-120b`. Gemini remains experimental;
consult the full product's AI guide for current supported IDs and verification status.
No shared project key is included, and arbitrary providers are not accepted. Full product
mode adds structured validation, caching and resumable execution; performance still depends
on the model. Native mode uses the host model without a separate provider key.
