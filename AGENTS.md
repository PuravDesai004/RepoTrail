# RepoTrail

- Complete the authorized RepoTrail v1 autonomously, using .local/BUILD_STATE.md checkpoints.
- Preserve M01/M02 APIs and schemas; add versioned documents for new capabilities.
- Application and test code must be Python. Support Windows/PowerShell and portable paths.
- Never execute code, tests, hooks, or dependency installation from an inspected repository.
- Use `.venv\Scripts\python.exe`; install dependencies only in this project environment.
- Verify with `.venv\Scripts\python.exe -m pytest -q` (set process-local TEMP/TMP to
  `.local\tmp` when the sandbox default temp directory is inaccessible).
- Reuse eligibility and bounds. Never checkout/fetch/change an inspected repository.
- AI is explicit and grounded; bound provider attempts, inputs, outputs, and SQLite retention.
- Stage, commit, configure a remote, and push only when explicitly requested by the user.
  No package publishing, deployment, or global configuration edits.
- Teach completed work with the exact local gist contract in `.local/skills/gist/SKILL.md`
  and `.local/skills/gist/references/output-template.md`. Preserve both milestone reports
  and the verbatim local gist copies. Deliver RepoTrail_Final_Report.md,
  RepoTrail_Teaching_Packet.md, and the explicit-allowlist review bundle under .local/handoffs/.
