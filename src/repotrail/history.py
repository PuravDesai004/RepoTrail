"""Bounded local-object history and endpoint comparisons; no checkout/fetch/helpers."""

import hashlib
import re
from pathlib import Path, PurePosixPath
from typing import Any

from .analysis import ReadBudget, decode_source
from .evidence import EvidenceUnavailable, excerpt_bytes, make_excerpt
from .git import MAX_CANDIDATES, InventoryError, decode_git, git_identity, repository_root, run_git
from .inventory import excluded_path
from .product_models import (
    DEFAULT_EVIDENCE_LIMITS,
    ChangedFile,
    ChangeDocument,
    ChangedRange,
    CommitMetadata,
    EvidenceDiagnostic,
    EvidenceLimits,
    HistoryDocument,
    TextHunk,
)
from .python_parser import ParserLimitError, parse_python_source
from .source_models import DEFAULT_LIMITS, Declaration

HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def resolve_commit(root: Path, ref: str) -> str:
    if not ref or len(ref.encode("utf-8")) > 1024 or "\0" in ref:
        raise InventoryError("Revision must be a nonempty bounded local revision expression.")
    raw, code = run_git(
        root,
        ["rev-parse", "--verify", "--quiet", "--end-of-options", f"{ref}^{{commit}}"],
        (0, 1, 128),
    )
    oid = raw.decode("ascii", errors="replace").strip()
    if code or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", oid):
        raise InventoryError(
            "Revision does not resolve to an existing local commit; no fetch is attempted."
        )
    return oid


def commit_log(root: Path, revision: str, limit: int) -> tuple[list[CommitMetadata], bool]:
    raw, _ = run_git(
        root,
        ["log", f"--max-count={limit + 1}", "--format=%H%x00%P%x00%an%x00%s", "-z", revision, "--"],
    )
    if not raw:
        return [], False
    fields = raw.removesuffix(b"\0").split(b"\0")
    if len(fields) % 4:
        raise InventoryError("Git returned malformed bounded commit metadata.")
    records = []
    for index in range(0, len(fields), 4):
        oid, parents, author, subject = fields[index : index + 4]
        records.append(
            CommitMetadata(
                oid=decode_git(oid),
                parents=decode_git(parents).split(),
                author=author[:256].decode("utf-8", errors="replace"),
                subject=subject[:512].decode("utf-8", errors="replace"),
                metadata_truncated=len(author) > 256 or len(subject) > 512,
            )
        )
    return records[:limit], len(records) > limit


def recent_history(
    supplied: str | Path,
    ref: str = "HEAD",
    *,
    limit: int = 20,
    limits: EvidenceLimits | None = None,
) -> HistoryDocument:
    effective = limits or DEFAULT_EVIDENCE_LIMITS
    if not 1 <= limit <= effective.max_history_entries:
        raise InventoryError(
            f"History limit must be between 1 and {effective.max_history_entries}."
        )
    root = repository_root(supplied)
    identity = git_identity(root)
    if identity.unborn and ref == "HEAD":
        return HistoryDocument(
            project_name=root.name,
            git=identity,
            requested_ref=ref,
            resolved_oid=None,
            commits=[],
            limit=limit,
            has_more=False,
            complete=True,
        )
    oid = resolve_commit(root, ref)
    commits, more = commit_log(root, oid, limit)
    return HistoryDocument(
        project_name=root.name,
        git=identity,
        requested_ref=ref,
        resolved_oid=oid,
        commits=commits,
        limit=limit,
        has_more=more,
        complete=not more and not any(commit.metadata_truncated for commit in commits),
    )


def read_blob(root: Path, oid: str, budget: ReadBudget) -> bytes:
    raw_size, code = run_git(root, ["cat-file", "-s", oid], (0, 128))
    if code:
        raise EvidenceUnavailable("missing_object", "Historical blob is not available locally.")
    try:
        size = int(raw_size.strip())
    except ValueError as exc:
        raise InventoryError("Git returned an invalid blob size.") from exc
    if size > DEFAULT_LIMITS.max_file_bytes:
        raise EvidenceUnavailable(
            "file_too_large", "Historical source exceeds the per-file byte limit."
        )
    if size > budget.limit_bytes - budget.bytes_read:
        raise InventoryError("Historical source reads exceed the aggregate byte limit.")
    raw, code = run_git(root, ["cat-file", "blob", oid], (0, 128))
    budget.bytes_read += len(raw)
    if code or len(raw) != size:
        raise EvidenceUnavailable("missing_object", "Historical blob could not be read completely.")
    return raw


def changed_ranges(numbers: list[int]) -> list[ChangedRange]:
    ranges: list[ChangedRange] = []
    for number in numbers:
        if ranges and number == ranges[-1].end_line + 1:
            ranges[-1] = ChangedRange(start_line=ranges[-1].start_line, end_line=number)
        else:
            ranges.append(ChangedRange(start_line=number, end_line=number))
    return ranges


def split_hunks(
    patch: str,
) -> list[tuple[tuple[int, int, int, int], str, list[ChangedRange], list[ChangedRange]]]:
    blocks: list[list[str]] = []
    for line in patch.split("\n"):
        if HUNK_HEADER.match(line):
            blocks.append([line])
        elif blocks and (line or blocks[-1]):
            blocks[-1].append(line)
    result = []
    for block in blocks:
        match = HUNK_HEADER.match(block[0])
        assert match is not None
        old_start, old_count, new_start, new_count = (
            int(match[1]),
            int(match[2] or 1),
            int(match[3]),
            int(match[4] or 1),
        )
        old_cursor, new_cursor = old_start, new_start
        old_changed, new_changed = [], []
        for line in block[1:]:
            if line.startswith("-"):
                old_changed.append(old_cursor)
                old_cursor += 1
            elif line.startswith("+"):
                new_changed.append(new_cursor)
                new_cursor += 1
            elif line.startswith(" "):
                old_cursor += 1
                new_cursor += 1
        if old_cursor != old_start + old_count or new_cursor != new_start + new_count:
            raise InventoryError("Git hunk ranges do not match its returned patch lines.")
        result.append(
            (
                (old_start, old_count, new_start, new_count),
                "\n".join(block).rstrip("\n") + "\n",
                changed_ranges(old_changed),
                changed_ranges(new_changed),
            )
        )
    return result


def overlapping_symbols(
    raw: bytes | None, path: str, ranges: list[ChangedRange]
) -> tuple[list[Declaration], list[EvidenceDiagnostic]]:
    if raw is None or PurePosixPath(path).suffix.lower() != ".py":
        return [], []
    try:
        source, _count = decode_source(raw)
        symbols, _imports = parse_python_source(source, path, DEFAULT_LIMITS.max_ast_nodes)
    except SyntaxError, UnicodeError, LookupError, RecursionError, ParserLimitError:
        return [], [
            EvidenceDiagnostic(
                code="python_unparsed",
                message="Python declaration overlap is unknown for this historical side.",
                path=path,
            )
        ]
    return [
        symbol
        for symbol in symbols
        if any(
            symbol.start_line <= span.end_line and symbol.end_line >= span.start_line
            for span in ranges
        )
    ], []


def compare_file(
    root: Path,
    path: str,
    status: str,
    old_blob: str | None,
    new_blob: str | None,
    base_oid: str,
    head_oid: str,
    budget: ReadBudget,
    limits: EvidenceLimits,
) -> ChangedFile:
    fields: dict[str, Any] = {
        "status": status,
        "old_path": path if old_blob else None,
        "new_path": path if new_blob else None,
        "old_blob": old_blob,
        "new_blob": new_blob,
    }
    try:
        old_raw = read_blob(root, old_blob, budget) if old_blob else None
        new_raw = read_blob(root, new_blob, budget) if new_blob else None
    except EvidenceUnavailable as exc:
        return ChangedFile(
            **fields,
            complete=False,
            diagnostics=[EvidenceDiagnostic(code=exc.code, message=str(exc), path=path)],
        )
    fields["old_sha256"] = hashlib.sha256(old_raw).hexdigest() if old_raw is not None else None
    fields["new_sha256"] = hashlib.sha256(new_raw).hexdigest() if new_raw is not None else None
    if any(raw is not None and b"\0" in raw for raw in (old_raw, new_raw)):
        return ChangedFile(
            **fields,
            binary=True,
            complete=False,
            diagnostics=[
                EvidenceDiagnostic(
                    code="binary",
                    message="Binary contents are not returned as text evidence.",
                    path=path,
                )
            ],
        )
    try:
        old_source = (
            (
                decode_source(old_raw)[0]
                if path.lower().endswith(".py")
                else old_raw.decode("utf-8-sig")
            )
            if old_raw is not None
            else ""
        )
        new_source = (
            (
                decode_source(new_raw)[0]
                if path.lower().endswith(".py")
                else new_raw.decode("utf-8-sig")
            )
            if new_raw is not None
            else ""
        )
        if any(raw is not None and re.search(rb"\r(?!\n)", raw) for raw in (old_raw, new_raw)):
            raise UnicodeError("Git/Python newline coordinate mismatch")
        patch_raw, _ = run_git(
            root,
            [
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--no-renames",
                "--no-color",
                "--unified=3",
                base_oid,
                head_oid,
                "--",
                f":(literal){path}",
            ],
        )
        if len(patch_raw) > limits.max_patch_context_bytes:
            raise InventoryError("Comparison exceeds the returned patch/context byte limit.")
        patch = patch_raw.decode("utf-8")
    except UnicodeError, LookupError, SyntaxError:
        return ChangedFile(
            **fields,
            complete=False,
            diagnostics=[
                EvidenceDiagnostic(
                    code="unsupported_text",
                    message="Patch/source encoding or newline coordinates are unsupported; overlap is unknown.",
                    path=path,
                )
            ],
        )
    if "Binary files " in patch:
        return ChangedFile(
            **fields,
            binary=True,
            complete=False,
            diagnostics=[
                EvidenceDiagnostic(
                    code="binary",
                    message="Git classified this file as binary; no text hunks are available.",
                    path=path,
                )
            ],
        )
    hunks = []
    diagnostics: list[EvidenceDiagnostic] = []
    old_ranges: list[ChangedRange] = []
    new_ranges: list[ChangedRange] = []
    for (
        old_start,
        old_count,
        new_start,
        new_count,
    ), hunk_patch, old_changed, new_changed in split_hunks(patch):
        old_excerpt = (
            make_excerpt(
                path,
                old_raw,
                old_source,
                old_start,
                old_start + old_count - 1,
                limits.max_context_bytes,
                commit_oid=base_oid,
                blob_oid=old_blob,
            )
            if old_raw is not None and old_count
            else None
        )
        new_excerpt = (
            make_excerpt(
                path,
                new_raw,
                new_source,
                new_start,
                new_start + new_count - 1,
                limits.max_context_bytes,
                commit_oid=head_oid,
                blob_oid=new_blob,
            )
            if new_raw is not None and new_count
            else None
        )
        if (old_count and (old_excerpt is None or not old_excerpt.complete)) or (
            new_count and (new_excerpt is None or not new_excerpt.complete)
        ):
            diagnostics.append(
                EvidenceDiagnostic(
                    code="context_limit",
                    message="Historical hunk context was explicitly excerpted.",
                    path=path,
                )
            )
        hunks.append(
            TextHunk(
                old_start=old_start,
                old_count=old_count,
                new_start=new_start,
                new_count=new_count,
                patch=hunk_patch,
                old_changed_ranges=old_changed,
                new_changed_ranges=new_changed,
                old_excerpt=old_excerpt,
                new_excerpt=new_excerpt,
            )
        )
        old_ranges.extend(old_changed)
        new_ranges.extend(new_changed)
    old_symbols, old_diagnostics = overlapping_symbols(old_raw, path, old_ranges)
    new_symbols, new_diagnostics = overlapping_symbols(new_raw, path, new_ranges)
    diagnostics.extend(old_diagnostics + new_diagnostics)
    return ChangedFile(
        **fields,
        complete=not diagnostics,
        hunks=hunks,
        old_symbols=old_symbols,
        new_symbols=new_symbols,
        diagnostics=diagnostics,
    )


def compare_revisions(
    supplied: str | Path, base_ref: str, head_ref: str, *, limits: EvidenceLimits | None = None
) -> ChangeDocument:
    effective = limits or DEFAULT_EVIDENCE_LIMITS
    root = repository_root(supplied)
    base_oid, head_oid = resolve_commit(root, base_ref), resolve_commit(root, head_ref)
    raw, _ = run_git(
        root,
        [
            "diff-tree",
            "--no-commit-id",
            "-r",
            "--raw",
            "--no-abbrev",
            "-z",
            "--no-renames",
            "--no-ext-diff",
            "--no-textconv",
            base_oid,
            head_oid,
            "--",
        ],
    )
    parts = raw.removesuffix(b"\0").split(b"\0") if raw else []
    if len(parts) % 2 or len(parts) // 2 > MAX_CANDIDATES:
        raise InventoryError("Comparison has malformed or excessive Git candidate records.")
    candidates = []
    excluded = 0
    for index in range(0, len(parts), 2):
        metadata, raw_path = parts[index : index + 2]
        values = metadata.removeprefix(b":").split()
        if len(values) != 5:
            raise InventoryError("Git returned malformed endpoint changes.")
        old_mode, new_mode, old_oid, new_oid, raw_status = values
        path = decode_git(raw_path)
        if (
            excluded_path(path)
            or old_mode not in (b"000000", b"100644", b"100755")
            or new_mode not in (b"000000", b"100644", b"100755")
        ):
            excluded += 1
            continue
        status = raw_status.decode("ascii")
        if status not in ("A", "D", "M", "T"):
            raise InventoryError("Unsupported endpoint file status under the no-rename policy.")
        candidates.append(
            (
                path,
                status,
                old_oid.decode() if old_mode != b"000000" else None,
                new_oid.decode() if new_mode != b"000000" else None,
            )
        )
    if len(candidates) > effective.max_changed_files:
        raise InventoryError("Comparison exceeds the eligible changed-file limit.")
    budget = ReadBudget(DEFAULT_LIMITS.max_total_bytes)
    files = []
    returned = 0
    for path, status, old_blob, new_blob in sorted(candidates):
        file = compare_file(
            root, path, status, old_blob, new_blob, base_oid, head_oid, budget, effective
        )
        returned += sum(
            len(hunk.patch.encode("utf-8"))
            + excerpt_bytes(hunk.old_excerpt)
            + excerpt_bytes(hunk.new_excerpt)
            for hunk in file.hunks
        )
        if returned > effective.max_patch_context_bytes:
            raise InventoryError(
                "Comparison exceeds the returned patch/context byte limit; no partial document returned."
            )
        files.append(file)
    commits, more = commit_log(root, f"{base_oid}..{head_oid}", effective.max_history_entries)
    return ChangeDocument(
        project_name=root.name,
        base_ref=base_ref,
        head_ref=head_ref,
        base_oid=base_oid,
        head_oid=head_oid,
        complete=not more
        and all(file.complete for file in files)
        and not any(commit.metadata_truncated for commit in commits),
        files=files,
        status_counts={
            status: sum(file.status == status for file in files)
            for status in sorted({file.status for file in files})
        },
        excluded_changes=excluded,
        log_range=f"{base_oid}..{head_oid}",
        commits=commits,
        log_has_more=more,
        returned_patch_context_bytes=returned,
        limits=effective,
    )
