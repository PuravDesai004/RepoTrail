# Language coverage

The CLI, downloadable Codex/Claude Code skill, MCP evidence tools, and optional LangGraph
evidence selection support mixed repositories. The current agent explains and reviews
returned evidence by default; no separate provider API key is required.

| Language | Files | Static facts |
| --- | --- | --- |
| Python | `.py` | Existing AST classes, functions, methods, decorators and imports. |
| JavaScript / JSX | `.js`, `.jsx`, `.mjs`, `.cjs` | Classes, named functions/generators, functions/arrows assigned to identifier bindings or class fields, methods, ES module imports/re-exports, literal `require()` and `import()`. |
| Java | `.java` | Classes, interfaces, enums, records, annotation types, methods, constructors, package and import declarations. |

JavaScript and Java use pinned Python Tree-sitter bindings and grammars. Python uses
the existing standard-library AST parser. No inspected file is imported, evaluated,
compiled, built, installed or tested. Node.js/JDK/build tools are unnecessary.
`package.json`, `pom.xml`, `build.gradle`, and `build.gradle.kts` are quoted as source
only. XML entities, lifecycle scripts, Gradle expressions and plugins are never evaluated.

`repotrail symbols ROOT --json` returns a `repository_source_map` version `2` with
per-file language, declarations, physical ranges, syntax kinds and counts by language.
`overview` / MCP `get_project_overview` return `repository_overview` version `2`.
`changes` / MCP `summarize_changes` return `repository_changes` version `2`, with old/new
language-aware symbol overlap. Historical evidence always uses immutable Git blobs.
The skill samples one file from each present supported language before filling its
three-file snapshot sample; use source mode for additional files or later lines.

Qualified names describe named lexical containers, excluding Java packages. Interfaces,
enums, records and annotations use `kind: class`; `syntax_kind` preserves the actual
declaration form. Assigned JS functions use their binding name, and anonymous default
exports use `default`. Unnamed callbacks, object-literal methods and Java anonymous-class
members have no stable qualified name in this map. Their bodies are not traversed for
structured facts; their source remains available in
historical source/hunk evidence. Computed properties, destructured function bindings,
dynamic imports/require paths, runtime exports, generated members, dependency resolution,
type checking, dispatch and call graphs are outside the contract. A literal `require`
is an observed syntax pattern, not proof of module loading or identifier resolution.
TypeScript and other languages have no structured symbol parser yet; generic eligible
text/hunk evidence remains available. Unsupported grammar syntax produces an explicit
error, with no recovered declarations presented as successful facts.

JavaScript/Java require UTF-8 (optional BOM), and normalize physical CRLF/CR lines for
working-tree source. Python retains its encoding-cookie behavior. Historical diff evidence
rejects incompatible encodings/newline coordinates. Definition columns are one-based
UTF-8 byte columns for JS/Java; Python compatibility entries use column 1. Overloads are
identified by path, qualified name, definition line and column. For ambiguity, select
both `--definition-line` and `--definition-column` from the symbol map; MCP accepts the
same optional selectors. Citations still identify exact retained physical lines and
raw-byte hashes, independently of symbol IDs.

Limits apply across all three languages together: 500 files, 1 MiB each, 10 MiB aggregate,
50,000 visited AST/named syntax nodes per file. JS/Java parsing also has a cooperative
one-second deadline. Per-file failure marks the map incomplete; aggregate overflow
rejects the operation. See [limits](limits.md).

Inventory schema `1` and Python source-map schema `1` remain unchanged. Existing APIs
`analysis.analyze_python_repository`, `evidence.get_project_overview` and
`history.compare_revisions` preserve their old documents. CLI `--python-only` selects
these legacy documents. New APIs are `multilang.analyze_repository`,
`multilang.get_repository_overview`, and `history.compare_repository_revisions`.
`evidence.get_symbol_context` now accepts supported languages while retaining its
line-based context contract. Default MCP tool names and keyless behavior are unchanged;
clients validating old overview/change output schemas must adopt the version `2` schemas.
