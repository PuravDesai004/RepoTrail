"""Historical evidence and paged local history. No model, checkout, or inspected code."""

import base64
import json
import re
import tomllib
from pathlib import Path
from typing import Any, Literal

from .agent_models import AgentEvidence
from .analysis import ReadBudget, decode_source
from .evidence import EvidenceUnavailable, make_excerpt, safe_relative
from .git import MAX_CANDIDATES, InventoryError, decode_git, repository_root, run_git
from .history import commit_log, compare_revisions, read_blob, resolve_commit
from .inventory import MAX_FILES, excluded_path
from .product_models import EvidenceDiagnostic, SourceExcerpt
from .selection import repository_id, source_text, trim_source
from .source_models import DEFAULT_LIMITS

MAX_TEXT = 32768
MAX_METADATA = 32768
MAX_PACKET = 131072


def revision_tree(root: Path, oid: str) -> tuple[list[tuple[str, str]], int]:
    raw, _ = run_git(root, ["ls-tree", "-r", "-z", "--full-tree", oid])
    records = raw.removesuffix(b"\0").split(b"\0") if raw else []
    if len(records) > MAX_CANDIDATES:
        raise InventoryError("Historical tree exceeds the Git candidate limit.")
    files = []
    excluded = 0
    for record in records:
        header, separator, name = record.partition(b"\t")
        fields = header.split()
        if not separator or len(fields) != 3:
            raise InventoryError("Historical tree contains malformed records.")
        mode, kind, blob = fields
        path = safe_relative(decode_git(name))
        if excluded_path(path) or mode not in (b"100644", b"100755") or kind != b"blob":
            excluded += 1
            continue
        files.append((path, blob.decode("ascii")))
    if len(files) > MAX_FILES or len({path for path, _ in files}) != len(files):
        raise InventoryError("Historical tree exceeds inventory bounds or has duplicate paths.")
    return sorted(files), excluded


def shallow_repository(root: Path) -> bool:
    raw, _ = run_git(root, ["rev-parse", "--is-shallow-repository"])
    return raw.strip() == b"true"


def packet(
    root: Path,
    oid: str,
    mode: Literal["commit", "whole", "source"],
    metadata: dict[str, Any],
    candidates: list[SourceExcerpt],
    omissions: list[str],
    diagnostics: list[EvidenceDiagnostic],
    *,
    next_cursor: str | None = None,
) -> AgentEvidence:
    selected: list[SourceExcerpt] = []
    seen = set()
    for candidate in candidates:
        if candidate.reference.id in seen:
            continue
        seen.add(candidate.reference.id)
        if len(selected) >= 200:
            omissions.append("Source omitted: packet reference count limit.")
            continue
        available = MAX_TEXT - len(source_text(selected).encode()) - (2 if selected else 0)
        retained = trim_source(candidate, max(0, available))
        if retained is None:
            omissions.append(f"Source omitted for {candidate.reference.path}: text budget.")
        else:
            selected.append(retained)
            if not retained.complete:
                omissions.append(f"Excerpt truncated for {candidate.reference.path}.")
    # Reduce optional samples, never history records/cursor coverage, to fit metadata.
    while len(json.dumps(metadata, ensure_ascii=True).encode()) > MAX_METADATA:
        removable = next(
            (key for key in ("files", "changed_files", "declarations") if metadata.get(key)), None
        )
        if removable is None:
            raise InventoryError("Agent metadata exceeds its byte limit.")
        metadata[removable].pop()
        if "Metadata samples reduced to the byte limit." not in omissions:
            omissions.append("Metadata samples reduced to the byte limit.")
    result = AgentEvidence(
        mode=mode,
        project_name=root.name,
        target_oid=oid,
        metadata=metadata,
        sources=selected,
        omissions=omissions[:100],
        diagnostics=diagnostics[:100],
        complete=not omissions and not diagnostics,
        next_cursor=next_cursor,
        source_text_bytes=len(source_text(selected).encode()),
        metadata_bytes=len(json.dumps(metadata, ensure_ascii=True).encode()),
    )
    if len(result.model_dump_json().encode()) > MAX_PACKET:
        raise InventoryError("Agent packet exceeds its serialized byte limit.")
    return result


def source_excerpt(
    root: Path,
    oid: str,
    path: str,
    blob: str,
    budget: ReadBudget,
    start: int = 1,
    end: int | None = None,
) -> SourceExcerpt:
    raw = read_blob(root, blob, budget)
    if b"\0" in raw:
        raise EvidenceUnavailable("binary", "Binary contents are unavailable as text evidence.")
    try:
        source, count = decode_source(raw)
    except (UnicodeError, LookupError, SyntaxError) as exc:
        raise EvidenceUnavailable("encoding", "Historical source encoding is unsupported.") from exc
    if count == 0 or not 1 <= start <= count or (end is not None and not start <= end <= count):
        raise EvidenceUnavailable("range", "Requested physical source range is unavailable.")
    excerpt = make_excerpt(
        path,
        raw,
        source,
        start,
        min(count, end or start + 79),
        16384,
        commit_oid=oid,
        blob_oid=blob,
    )
    if excerpt is None:
        raise EvidenceUnavailable(
            "context_limit", "No complete source line fits the excerpt limit."
        )
    return excerpt


def get_revision_source(
    supplied: str | Path, ref: str, path: str, *, start: int = 1, end: int | None = None
) -> AgentEvidence:
    root = repository_root(supplied)
    oid = resolve_commit(root, ref)
    path = safe_relative(path)
    files, _ = revision_tree(root, oid)
    blob = dict(files).get(path)
    if blob is None:
        raise InventoryError("Source path is excluded or absent at the selected commit.")
    try:
        excerpt = source_excerpt(
            root, oid, path, blob, ReadBudget(DEFAULT_LIMITS.max_total_bytes), start, end
        )
    except EvidenceUnavailable as exc:
        raise InventoryError(str(exc)) from exc
    omissions = [] if excerpt.complete else ["Source range exceeded its excerpt byte limit."]
    return packet(
        root, oid, "source", {"path": path, "working_tree_used": False}, [excerpt], omissions, []
    )


def get_commit_evidence(supplied: str | Path, ref: str = "HEAD") -> AgentEvidence:
    root = repository_root(supplied)
    oid = resolve_commit(root, ref)
    records, _ = commit_log(root, oid, 1)
    commit = records[0]
    # Raw commit headers preserve parents even at a shallow boundary.
    raw, _ = run_git(root, ["cat-file", "commit", oid])
    parents = [
        line[7:].decode("ascii")
        for line in raw.split(b"\n\n", 1)[0].splitlines()
        if line.startswith(b"parent ")
    ]
    files, excluded = revision_tree(root, oid)
    python = [(path, blob) for path, blob in files if Path(path).suffix.lower() == ".py"]
    selected = [(path, blob) for path, blob in files if path in ("README.md", "pyproject.toml")]
    selected.extend(python[:3])
    candidates = []
    diagnostics = []
    omissions = [
        "Snapshot source is selected README/TOML and up to three Python files; request additional historical source when needed."
    ]
    metadata: dict[str, Any] = {
        "commit": commit.model_dump(),
        "parents": parents[:32],
        "parent_count": len(parents),
        "comparison_policy": "first_parent",
        "working_tree_used": False,
        "repository_shallow": shallow_repository(root),
        "eligible_file_count": len(files),
        "excluded_file_count": excluded,
        "python_file_count": len(python),
        "files": [path for path, _ in files[:50]],
        "file_sample_limit": 50,
    }
    budget = ReadBudget(DEFAULT_LIMITS.max_total_bytes)
    for path, blob in selected:
        try:
            excerpt = source_excerpt(root, oid, path, blob, budget)
            candidates.append(excerpt)
            if path == "pyproject.toml":
                # Only selected literal TOML declarations; never packaging execution.
                text = "\n".join(line.text for line in excerpt.lines)
                try:
                    project = tomllib.loads(text).get("project", {})
                    metadata["packaging"] = {
                        key: project[key]
                        for key in ("name", "version", "scripts", "dependencies")
                        if key in project
                    }
                except tomllib.TOMLDecodeError, TypeError:
                    diagnostics.append(
                        EvidenceDiagnostic(
                            code="toml_excerpt",
                            message="Packaging declarations could not be parsed from the supplied excerpt.",
                            path=path,
                        )
                    )
        except EvidenceUnavailable as exc:
            diagnostics.append(EvidenceDiagnostic(code=exc.code, message=str(exc), path=path))
    if len(parents) > 32:
        omissions.append("Parent metadata is limited to the first 32 parent IDs.")
    if metadata["repository_shallow"]:
        omissions.append("Repository is shallow; only locally available history can be explained.")
    if parents:
        try:
            comparison = compare_revisions(root, parents[0], oid)
            metadata["changed_files"] = [
                {
                    "path": file.new_path or file.old_path,
                    "status": file.status,
                    "old_symbols": [s.qualified_name[:512] for s in file.old_symbols[:10]],
                    "new_symbols": [s.qualified_name[:512] for s in file.new_symbols[:10]],
                }
                for file in comparison.files[:20]
            ]
            metadata["changed_file_count"] = len(comparison.files)
            if len(comparison.files) > 20:
                omissions.append("Changed-file metadata is sampled to twenty paths.")
            for file in comparison.files:
                diagnostics.extend(file.diagnostics)
                for hunk in file.hunks:
                    candidates.extend(
                        x for x in (hunk.old_excerpt, hunk.new_excerpt) if x is not None
                    )
        except InventoryError:
            omissions.append(
                "First-parent comparison is unavailable or exceeds shared evidence bounds."
            )
    return packet(root, oid, "commit", metadata, candidates, omissions, diagnostics)


def history_cursor(root: Path, target: str, offset: int, page_size: int) -> str:
    fields = {
        "v": 1,
        "repository": repository_id(root),
        "target": target,
        "offset": offset,
        "page_size": page_size,
    }
    return (
        base64.urlsafe_b64encode(json.dumps(fields, separators=(",", ":")).encode())
        .decode()
        .rstrip("=")
    )


def get_history_page(
    supplied: str | Path, ref: str | None = None, *, cursor: str | None = None, page_size: int = 10
) -> AgentEvidence:
    root = repository_root(supplied)
    if not 1 <= page_size <= 10:
        raise InventoryError("History page size must be between 1 and 10.")
    offset = 0
    target = resolve_commit(root, ref or "HEAD") if cursor is None or ref is not None else ""
    if cursor is not None:
        try:
            if len(cursor) > 2048:
                raise ValueError()
            data = json.loads(
                base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
            )
            if (
                set(data) != {"v", "repository", "target", "offset", "page_size"}
                or data["v"] != 1
                or data["repository"] != repository_id(root)
                or type(data["offset"]) is not int
                or not 0 <= data["offset"] < MAX_CANDIDATES
                or data["page_size"] != page_size
                or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", data["target"])
            ):
                raise ValueError()
            if ref is not None and target != data["target"]:
                raise ValueError()
            target = resolve_commit(root, data["target"])
            offset = data["offset"]
        except (ValueError, TypeError, KeyError, UnicodeError) as exc:
            raise InventoryError(
                "History cursor is malformed or belongs to another repository/target/page size."
            ) from exc
    raw, _ = run_git(
        root,
        [
            "log",
            "--topo-order",
            f"--skip={offset}",
            f"--max-count={page_size + 1}",
            "--format=%H",
            target,
            "--",
        ],
    )
    ids = raw.decode("ascii").splitlines()
    has_more = len(ids) > page_size
    commits = [commit_log(root, oid, 1)[0][0].model_dump() for oid in ids[:page_size]]
    following = offset + len(commits)
    next_cursor = (
        history_cursor(root, target, following, page_size)
        if has_more and following < MAX_CANDIDATES
        else None
    )
    shallow = shallow_repository(root)
    omissions = [
        "This page is commit metadata, not source analysis; retrieve commit/source evidence for code claims."
    ]
    if shallow:
        omissions.append("History is shallow; missing ancestors are not fetched.")
    if has_more and next_cursor is None:
        omissions.append(
            "History traversal reached the shared candidate ceiling; coverage remains partial."
        )
    return packet(
        root,
        target,
        "whole",
        {
            "commits": commits,
            "offset": offset,
            "returned": len(commits),
            "page_size": page_size,
            "order": "topological_newest_first",
            "has_more": has_more,
            "repository_shallow": shallow,
            "coverage": "page_only",
            "working_tree_used": False,
        },
        [],
        omissions,
        [],
        next_cursor=next_cursor,
    )
