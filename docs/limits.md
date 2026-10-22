# Scope and limits

RepoTrail supports local Python Git working trees on Python 3.14. It reads eligible
tracked/untracked paths, parses syntax, and reads existing local commit objects. It never
runs inspected code, resolves installed dependencies, builds a runtime call graph,
fetches history, checks out revisions, or edits an inspected repository.

| Bound | Default |
| --- | --- |
| Inventory / Git candidate records per pass | 10,000 / 20,000 |
| Each Git subprocess | 8 MiB stdout / 10 seconds |
| Python candidates / each source / aggregate reads | 500 / 1 MiB / 10 MiB |
| AST node visits per source | 50,000 after bounded parsing |
| History / eligible changed files | 100 / 100 |
| Returned patch and source context | 1 MiB combined |
| README/TOML read / declaration excerpt | 64 KiB / 16 KiB |
| AI source / prompt and schema metadata | 32 KiB / 32 KiB |
| Provider attempts / timeout / output tokens | 2 / 30 seconds / 1,500 |
| Cache entries / app runs | 128 / 256 |

Aggregate overflow fails the operation. Individual unavailable files produce diagnostics
and incomplete results. Deleted working files are omitted from current inventory; deletion
evidence comes from the old commit. Tracked includes intent-to-add index entries.

Known environment/cache/build directories, credential-like names, symlinks, gitlinks,
linked parents, and Windows reparse points are excluded. This is path filtering, not a
secret scanner. Repository configuration/ignore files remain Git inputs. Historical
virtual environments rely on known directory/filename and Git-mode exclusions.

Comparisons resolve immutable endpoint commits, disable external diff/textconv and
replacement objects, and represent renames as deletion plus addition. The `BASE..HEAD`
reachability log is separate from endpoint edits; it does not establish causation.
Binary data and unsupported encodings are unavailable as text. Packaging records are
TOML declarations, not resolved dependencies or verified executable entry points.

References bind relative path, inclusive physical lines, and raw-byte SHA-256; historical
references also bind commit/blob. Symbol overlap describes syntax, not runtime impact or
stable identity across edits. Metadata rechecks are best effort, not an atomic filesystem
snapshot or a hostile-code sandbox. Bounded AI selection can omit relevant distant code.
See the [skill guide](agent-skill.md) for historical packet and pagination limits.
