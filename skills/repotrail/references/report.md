# Evidence-based explanation

Start with what this repository/commit does and the exact target SHA. Keep explanations
readable for a developer discovering the project. Explain the purpose before implementation.

- **Scope:** repository, commit or reachable-history target, offsets/pages, detailed versus
  metadata-only commits, and whether history is shallow.
- **Architecture/code:** selected modules, declared entry points, meaningful functions and
  their interactions, using supplied source. Distinguish lexical structure from runtime inference.
- **Changes/journey:** observed edits, why they may matter, and inferred consequences.
  For whole history, use chronological chapters rather than a raw list of subjects.
- **Review (when requested):** issue, severity, source citations, inferred reasoning, and
  suggested validation. Zero supported findings is valid; never invent a findings quota.
- **Evidence:** cite `path:start-end @ commit` and the registered `src:` ID. Commit subjects
  support metadata statements only. Never invent a hash, line range, path or source content.
- **Gaps:** unavailable binary/encoding/objects, selected excerpts, missing context, first-parent
  merge policy, truncated metadata and unvisited history. Give a continuation cursor when needed.

Do not label an inference as observed merely because it cites code. Do not claim reviewed
tests passed or source executed. The current agent writes the explanation; the engine does
not grade its semantic correctness. Treat suggestions as advisory and make uncertainty clear.
