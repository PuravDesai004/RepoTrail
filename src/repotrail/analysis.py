"""Bounded working-tree reads, encoding/hash evidence, and Python map aggregation."""

import hashlib
import io
import os
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .git import InventoryError, repository_root
from .inventory import file_signature, inspect_repository
from .models import FileEntry
from .python_parser import ParserLimitError, parse_python_source
from .source_models import (
    DEFAULT_LIMITS,
    AnalysisLimits,
    Diagnostic,
    PythonFile,
    PythonSourceMap,
    SourceCounts,
)

READ_CHUNK_BYTES = 64 * 1024


@dataclass
class ReadBudget:
    limit_bytes: int
    bytes_read: int = 0


def require_unchanged(root: Path, path: str, expected: tuple[int, ...]) -> None:
    """Recheck inventory eligibility and metadata; changes invalidate the whole map."""
    try:
        current = file_signature(root, path)
    except OSError as exc:
        raise InventoryError(
            "Cannot recheck source eligibility; retry when the repository is stable."
        ) from exc
    if current != expected:
        raise InventoryError(
            "Source eligibility or metadata changed during analysis; retry when it is stable."
        )


def descriptor_signature(stream) -> tuple[int, ...]:
    """Compare identity/mode/size/mtime; Windows lstat/fstat ctime semantics can differ."""
    info = os.fstat(stream.fileno())
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns)


def read_source(root: Path, path: str, expected: tuple[int, ...], budget: ReadBudget) -> bytes:
    """Read at most the verified size, unbuffered; charge every returned chunk, including on failure."""
    size = expected[-3]
    if size > budget.limit_bytes - budget.bytes_read:
        raise InventoryError(
            "Analysis would exceed the aggregate source-byte limit; no partial map returned."
        )
    require_unchanged(root, path, expected)
    try:
        with (root / path).open("rb", buffering=0) as stream:
            if descriptor_signature(stream) != expected[-6:-1]:
                raise InventoryError(
                    "Opened source differs from checked metadata; retry when it is stable."
                )
            require_unchanged(root, path, expected)
            raw = bytearray()
            while len(raw) < size:
                chunk = stream.read(min(READ_CHUNK_BYTES, size - len(raw)))
                if not chunk:
                    raise OSError("Incomplete source read")
                budget.bytes_read += len(chunk)
                raw.extend(chunk)
            if descriptor_signature(stream) != expected[-6:-1]:
                raise InventoryError("Source changed during its read; retry when it is stable.")
    finally:
        require_unchanged(root, path, expected)
    return bytes(raw)


def decode_source(raw: bytes) -> tuple[str, int]:
    """Detect Python encoding from the same bytes; count universal-newline physical lines."""
    encoding, _prefix = tokenize.detect_encoding(io.BytesIO(raw).readline)
    source = raw.decode(encoding).replace("\r\n", "\n").replace("\r", "\n")
    line_count = source.count("\n") + int(bool(source) and not source.endswith("\n"))
    return source, line_count


def analyze_file(
    root: Path,
    entry: FileEntry,
    expected: tuple[int, ...],
    limits: AnalysisLimits,
    budget: ReadBudget,
) -> PythonFile:
    """Convert ordinary read/decode/parser failures to one typed diagnostic; propagate fatal changes."""
    require_unchanged(root, entry.path, expected)
    fields: dict[str, Any] = {
        "path": entry.path,
        "tracked": entry.tracked,
        "size_bytes": expected[-3],
    }
    if expected[-3] > limits.max_file_bytes:
        return PythonFile(
            **fields,
            status="error",
            diagnostics=[
                Diagnostic(code="file_too_large", message="Source exceeds the per-file byte limit.")
            ],
        )
    try:
        raw = read_source(root, entry.path, expected, budget)
    except OSError:
        return PythonFile(
            **fields,
            status="error",
            diagnostics=[
                Diagnostic(code="read_error", message="Unable to read complete source bytes.")
            ],
        )
    fields["sha256"] = hashlib.sha256(raw).hexdigest()
    try:
        source, line_count = decode_source(raw)
    except SyntaxError, UnicodeError, LookupError:
        return PythonFile(
            **fields,
            status="error",
            diagnostics=[
                Diagnostic(
                    code="encoding_error",
                    message="Python source encoding is invalid or unsupported.",
                )
            ],
        )
    fields["line_count"] = line_count
    try:
        symbols, imports = parse_python_source(source, entry.path, limits.max_ast_nodes)
    except SyntaxError as exc:
        line = exc.lineno if exc.lineno is not None and 1 <= exc.lineno <= line_count else None
        return PythonFile(
            **fields,
            status="error",
            diagnostics=[
                Diagnostic(code="syntax_error", message="Invalid Python syntax.", source_line=line)
            ],
        )
    except ParserLimitError, RecursionError:
        return PythonFile(
            **fields,
            status="error",
            diagnostics=[
                Diagnostic(
                    code="parser_limit",
                    message="Source exceeds the AST node limit or parser recursion capacity.",
                )
            ],
        )
    return PythonFile(**fields, status="analyzed", symbols=symbols, imports=imports)


def analyze_python_repository(
    supplied: str | Path, *, limits: AnalysisLimits | None = None
) -> PythonSourceMap:
    """Analyze eligible .py working files with shared discovery and stability checks."""
    effective = limits if limits is not None else DEFAULT_LIMITS
    root = repository_root(supplied)
    inventory = inspect_repository(root)
    candidates = [entry for entry in inventory.files if entry.extension == ".py"]
    if len(candidates) > effective.max_python_files:
        raise InventoryError(
            "Analysis exceeds the Python-file count limit; no partial map returned."
        )
    signatures: dict[str, tuple[int, ...]] = {}
    for entry in candidates:
        signature = file_signature(root, entry.path)
        if signature is None:
            raise InventoryError(
                "Source eligibility changed before analysis; retry when it is stable."
            )
        signatures[entry.path] = signature
    budget = ReadBudget(effective.max_total_bytes)
    files = [
        analyze_file(root, entry, signatures[entry.path], effective, budget) for entry in candidates
    ]
    if inspect_repository(root) != inventory:
        raise InventoryError(
            "Repository inventory or identity changed during analysis; retry when it is stable."
        )
    for path, signature in signatures.items():
        require_unchanged(root, path, signature)
    counts = SourceCounts.from_files(files)
    return PythonSourceMap(
        project_name=inventory.project_name,
        git=inventory.git,
        complete=counts.failed_files == 0,
        files=files,
        counts=counts,
        limits=effective,
    )
