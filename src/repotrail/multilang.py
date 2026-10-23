"""Shared multi-language repository APIs used by the CLI, skill evidence and MCP."""

import hashlib
from pathlib import Path
from typing import Any

from .analysis import ReadBudget, read_source, require_unchanged
from .git import InventoryError, repository_root
from .inventory import file_signature, inspect_repository
from .language_models import (
    RepositoryCounts,
    RepositoryFile,
    RepositoryLimits,
    RepositoryOverview,
    RepositorySourceMap,
    language_for,
)
from .language_parser import decode_language, parse_source
from .models import FileEntry
from .python_parser import ParserLimitError
from .source_models import Diagnostic


def analyze_repository_file(
    root: Path,
    entry: FileEntry,
    expected: tuple[int, ...],
    limits: RepositoryLimits,
    budget: ReadBudget,
) -> RepositoryFile:
    require_unchanged(root, entry.path, expected)
    fields: dict[str, Any] = {
        "path": entry.path,
        "language": language_for(entry.path),
        "tracked": entry.tracked,
        "size_bytes": expected[-3],
    }

    def error(code, message):
        return RepositoryFile(
            **fields, status="error", diagnostics=[Diagnostic(code=code, message=message)]
        )

    if expected[-3] > limits.max_file_bytes:
        return error("file_too_large", "Source exceeds the per-file byte limit.")
    try:
        raw = read_source(root, entry.path, expected, budget)
    except OSError:
        return error("read_error", "Unable to read complete source bytes.")
    fields["sha256"] = hashlib.sha256(raw).hexdigest()
    try:
        source, count = decode_language(raw, entry.path)
    except SyntaxError, UnicodeError, LookupError:
        return error("encoding_error", "Source encoding is invalid or unsupported.")
    fields["line_count"] = count
    try:
        symbols, imports = parse_source(source, entry.path, limits.max_ast_nodes)
    except SyntaxError:
        return error(
            "syntax_error", "Source syntax is invalid or unsupported by the static grammar."
        )
    except ParserLimitError, RecursionError:
        return error("parser_limit", "Source exceeds the static parser node/time/recursion limit.")
    return RepositoryFile(**fields, status="analyzed", symbols=symbols, imports=imports)


def analyze_repository(
    supplied: str | Path, *, limits: RepositoryLimits | None = None
) -> RepositorySourceMap:
    effective = limits or RepositoryLimits()
    root = repository_root(supplied)
    inventory = inspect_repository(root)
    candidates = [entry for entry in inventory.files if language_for(entry.path)]
    if len(candidates) > effective.max_source_files:
        raise InventoryError("Analysis exceeds the combined source-file count limit.")
    signatures = {}
    for entry in candidates:
        signature = file_signature(root, entry.path)
        if signature is None:
            raise InventoryError("Source eligibility changed before analysis; retry.")
        signatures[entry.path] = signature
    budget = ReadBudget(effective.max_total_bytes)
    files = [
        analyze_repository_file(root, entry, signatures[entry.path], effective, budget)
        for entry in candidates
    ]
    if inspect_repository(root) != inventory:
        raise InventoryError("Repository inventory changed during analysis; retry.")
    for path, signature in signatures.items():
        require_unchanged(root, path, signature)
    counts = RepositoryCounts.from_files(files)
    return RepositorySourceMap(
        project_name=inventory.project_name,
        git=inventory.git,
        complete=counts.failed_files == 0,
        files=files,
        counts=counts,
        limits=effective,
    )


def get_repository_overview(supplied: str | Path, *, limits=None) -> RepositoryOverview:
    from .evidence import _project_overview

    return _project_overview(
        supplied, limits=limits, mapping_api=analyze_repository, document_model=RepositoryOverview
    )
