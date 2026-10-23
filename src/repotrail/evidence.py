"""Verified working-tree overview and declaration context. No input code is executed."""

import hashlib
import tomllib
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING, Any, Literal, cast

from pydantic import ValidationError

from .analysis import (
    ReadBudget,
    analyze_python_repository,
    decode_source,
    read_source,
    require_unchanged,
)
from .git import InventoryError, repository_root
from .inventory import excluded_path, file_signature, inspect_repository
from .models import FileEntry, Inventory
from .product_models import (
    DEFAULT_EVIDENCE_LIMITS,
    EntryPoint,
    EvidenceDiagnostic,
    EvidenceLimits,
    NumberedLine,
    PackagingMetadata,
    ProjectOverview,
    SourceExcerpt,
    SourceReference,
    SymbolContext,
    stable_digest,
)
from .source_models import DEFAULT_LIMITS, PythonSourceMap

if TYPE_CHECKING:
    from .language_models import RepositorySourceMap


class EvidenceUnavailable(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def safe_relative(path: str) -> str:
    """Reject host-absolute, drive-relative, link-like separator and traversal inputs."""
    if "\\" in path or PureWindowsPath(path).drive or "\0" in path:
        raise InventoryError("Use a repository-relative POSIX path without drives or backslashes.")
    try:
        return FileEntry.relative_posix_path(path)
    except ValueError as exc:
        raise InventoryError(
            "Use a normalized repository-relative path within the bound root."
        ) from exc


def physical_lines(source: str) -> list[str]:
    text = source.replace("\r\n", "\n").replace("\r", "\n")
    return text.removesuffix("\n").split("\n") if text else []


def make_excerpt(
    path: str,
    raw: bytes,
    source: str,
    start: int,
    end: int,
    budget: int,
    *,
    commit_oid: str | None = None,
    blob_oid: str | None = None,
) -> SourceExcerpt | None:
    """Retain whole physical lines only; the citation names exactly the retained range."""
    lines = physical_lines(source)
    if not 1 <= start <= end <= len(lines):
        raise InventoryError("Source range is stale or outside the verified evidence.")
    selected: list[NumberedLine] = []
    used = 0
    for number in range(start, end + 1):
        text = lines[number - 1]
        size = len((text + "\n").encode("utf-8"))
        if used + size > budget:
            break
        selected.append(NumberedLine(number=number, text=text))
        used += size
    if not selected:
        return None
    fields: dict[str, Any] = {
        "source_kind": "git_blob" if commit_oid else "working_tree",
        "path": safe_relative(path),
        "start_line": start,
        "end_line": selected[-1].number,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "commit_oid": commit_oid,
        "blob_oid": blob_oid,
    }
    reference = SourceReference(id="src:" + stable_digest(fields), **fields)
    return SourceExcerpt(
        reference=reference,
        source_line_count=len(lines),
        requested_start_line=start,
        requested_end_line=end,
        lines=selected,
        complete=selected[-1].number == end,
    )


def excerpt_text(excerpt: SourceExcerpt) -> str:
    return "\n".join(f"{line.number}: {line.text}" for line in excerpt.lines)


def excerpt_bytes(excerpt: SourceExcerpt | None) -> int:
    return sum(len((line.text + "\n").encode("utf-8")) for line in excerpt.lines) if excerpt else 0


def read_eligible(
    root: Path,
    path: str,
    inventory: Inventory,
    max_bytes: int,
    *,
    expected_sha256: str | None = None,
) -> tuple[bytes, tuple[int, ...]]:
    path = safe_relative(path)
    if excluded_path(path) or not any(entry.path == path for entry in inventory.files):
        raise EvidenceUnavailable(
            "ineligible_path", "Path is excluded, missing, linked, or not a regular inventory file."
        )
    signature = file_signature(root, path)
    if signature is None:
        raise InventoryError("Source eligibility changed; retry when the repository is stable.")
    if signature[-3] > max_bytes:
        raise EvidenceUnavailable("file_too_large", "Evidence exceeds the per-source byte limit.")
    try:
        raw = read_source(root, path, signature, ReadBudget(max_bytes))
    except OSError as exc:
        raise EvidenceUnavailable("read_error", "Evidence could not be read completely.") from exc
    if expected_sha256 is not None and hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise InventoryError("Source hash is stale; retry with a fresh source map.")
    return raw, signature


def get_symbol_context(
    supplied: str | Path,
    path: str,
    qualified_name: str,
    definition_line: int | None = None,
    *,
    limits: EvidenceLimits | None = None,
    source_map: PythonSourceMap | RepositorySourceMap | None = None,
    definition_column: int | None = None,
) -> SymbolContext:
    effective = limits or DEFAULT_EVIDENCE_LIMITS
    root = repository_root(supplied)
    path = safe_relative(path)
    from .language_parser import decode_language
    from .multilang import analyze_repository

    mapping = source_map if source_map is not None else analyze_repository(root)
    fields: dict[str, Any] = {
        "project_name": mapping.project_name,
        "git": mapping.git,
        "path": path,
        "qualified_name": qualified_name,
    }
    if excluded_path(path):
        return SymbolContext(
            **fields,
            status="unavailable",
            complete=False,
            diagnostics=[
                EvidenceDiagnostic(
                    code="ineligible_path",
                    message="Path is excluded by repository evidence policy.",
                    path=path,
                )
            ],
        )
    file = next((file for file in mapping.files if file.path == path), None)
    if file is None:
        return SymbolContext(**fields, status="not_found", complete=False)
    if file.status == "error":
        diagnostic = file.diagnostics[0]
        return SymbolContext(
            **fields,
            status="unavailable",
            complete=False,
            diagnostics=[
                EvidenceDiagnostic(code=diagnostic.code, message=diagnostic.message, path=path)
            ],
        )
    matches = [
        symbol
        for symbol in file.symbols
        if symbol.qualified_name == qualified_name
        and (definition_line is None or symbol.definition_line == definition_line)
        and (
            definition_column is None
            or getattr(symbol, "definition_column", 1) == definition_column
        )
    ]
    if len(matches) != 1:
        return SymbolContext(
            **fields,
            status="ambiguous" if matches else "not_found",
            complete=False,
            definition_lines=[symbol.definition_line for symbol in matches],
        )
    inventory = inspect_repository(root)
    if inventory.git != mapping.git:
        raise InventoryError("Repository identity changed; retry with a fresh source map.")
    try:
        raw, signature = read_eligible(
            root, path, inventory, DEFAULT_LIMITS.max_file_bytes, expected_sha256=file.sha256
        )
        source, _count = decode_language(raw, path)
    except (EvidenceUnavailable, UnicodeError, LookupError, SyntaxError) as exc:
        code = exc.code if isinstance(exc, EvidenceUnavailable) else "encoding_error"
        return SymbolContext(
            **fields,
            status="unavailable",
            complete=False,
            diagnostics=[
                EvidenceDiagnostic(
                    code=code,
                    message="Declaration source is unavailable within the configured limits.",
                    path=path,
                )
            ],
        )
    symbol = matches[0]
    excerpt = make_excerpt(
        path, raw, source, symbol.start_line, symbol.end_line, effective.max_context_bytes
    )
    require_unchanged(root, path, signature)
    if inspect_repository(root) != inventory:
        raise InventoryError("Repository inventory changed during context retrieval; retry.")
    diagnostics = (
        []
        if excerpt and excerpt.complete
        else [
            EvidenceDiagnostic(
                code="context_limit",
                message="The declaration exceeds the context budget; only retained whole lines are cited.",
                path=path,
            )
        ]
    )
    return SymbolContext(
        **fields,
        status="found" if excerpt else "unavailable",
        complete=bool(excerpt and excerpt.complete),
        definition_lines=[symbol.definition_line],
        excerpt=excerpt,
        diagnostics=diagnostics,
    )


def get_project_overview(
    supplied: str | Path, *, limits: EvidenceLimits | None = None
) -> ProjectOverview:
    return _project_overview(
        supplied,
        limits=limits,
        mapping_api=analyze_python_repository,
        document_model=ProjectOverview,
    )


def _project_overview(supplied, *, limits, mapping_api, document_model):
    from .history import recent_history
    from .language_models import ManifestEvidence, ManifestFormat, RepositoryOverview

    effective = limits or DEFAULT_EVIDENCE_LIMITS
    root = repository_root(supplied)
    inventory = inspect_repository(root)
    signatures = {entry.path: file_signature(root, entry.path) for entry in inventory.files}
    mapping = mapping_api(root)
    diagnostics: list[EvidenceDiagnostic] = []
    docs: list[SourceExcerpt] = []
    entries: list[EntryPoint] = []
    manifests: list[ManifestEvidence] = []
    packaging = PackagingMetadata(format="absent_or_unsupported")
    paths = {entry.path for entry in inventory.files}
    readmes = [
        path
        for path in sorted(paths)
        if "/" not in path and path.lower() in ("readme", "readme.md", "readme.rst", "readme.txt")
    ]
    selected = ([readmes[0]] if readmes else []) + (
        ["pyproject.toml"] if "pyproject.toml" in paths else []
    )
    selected += [path for path in sorted(paths) if PurePosixPath(path).name == "__main__.py"][:10]
    manifest_names = ("package.json", "pom.xml", "build.gradle", "build.gradle.kts")
    if document_model is RepositoryOverview:
        manifest_paths = [
            path for path in sorted(paths) if PurePosixPath(path).name in manifest_names
        ]
        selected += manifest_paths[:20]
        if len(manifest_paths) > 20:
            diagnostics.append(
                EvidenceDiagnostic(
                    code="manifest_limit",
                    message="Only the first twenty eligible build manifests are shown.",
                )
            )
    for path in selected:
        try:
            max_bytes = (
                DEFAULT_LIMITS.max_file_bytes
                if path.endswith(".py")
                else effective.max_document_bytes
            )
            raw, _signature = read_eligible(root, path, inventory, max_bytes)
            source = (
                decode_source(raw)[0]
                if path.endswith(".py")
                else raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
            )
            lines = physical_lines(source)
            excerpt = (
                make_excerpt(
                    path,
                    raw,
                    source,
                    1,
                    len(lines),
                    effective.max_document_bytes
                    if path == "pyproject.toml"
                    else min(8192, effective.max_context_bytes),
                )
                if lines
                else None
            )
            if path == "pyproject.toml":
                project = tomllib.loads(source).get("project")
                if not isinstance(project, dict):
                    raise ValueError("unsupported packaging")
                packaging = PackagingMetadata(
                    format="pyproject.toml",
                    name=project.get("name"),
                    requires_python=project.get("requires-python"),
                    dependencies=project.get("dependencies", []),
                    optional_dependencies=project.get("optional-dependencies", {}),
                    scripts=project.get("scripts", {}),
                    gui_scripts=project.get("gui-scripts", {}),
                    evidence=excerpt,
                )
                if excerpt and excerpt.complete:
                    script_groups: list[
                        tuple[Literal["project_script", "gui_script"], dict[str, str]]
                    ] = [
                        ("project_script", packaging.scripts),
                        ("gui_script", packaging.gui_scripts),
                    ]
                    for kind, scripts in script_groups:
                        entries.extend(
                            EntryPoint(
                                kind=kind, name=name, target=target, reference=excerpt.reference
                            )
                            for name, target in sorted(scripts.items())
                        )
            elif PurePosixPath(path).name in manifest_names and excerpt:
                manifests.append(
                    ManifestEvidence(
                        path=path,
                        format=cast(ManifestFormat, PurePosixPath(path).name),
                        evidence=excerpt,
                    )
                )
            elif PurePosixPath(path).name == "__main__.py":
                if excerpt:
                    entries.append(
                        EntryPoint(
                            kind="module_main_file",
                            name=path,
                            target=path,
                            reference=excerpt.reference,
                        )
                    )
            elif excerpt:
                docs.append(excerpt)
            if excerpt and not excerpt.complete:
                diagnostics.append(
                    EvidenceDiagnostic(
                        code="excerpt_limit",
                        message="Repository documentation/context is explicitly excerpted.",
                        path=path,
                    )
                )
        except (EvidenceUnavailable, UnicodeError, ValueError, ValidationError) as exc:
            code = exc.code if isinstance(exc, EvidenceUnavailable) else "unsupported_metadata"
            diagnostics.append(
                EvidenceDiagnostic(
                    code=code,
                    message="Documentation or packaging evidence is unavailable/unsupported within bounds.",
                    path=path,
                )
            )
    if "pyproject.toml" not in paths and any(path in paths for path in ("setup.py", "setup.cfg")):
        diagnostics.append(
            EvidenceDiagnostic(
                code="unsupported_packaging",
                message="Legacy packaging was observed but is not executed or parsed.",
            )
        )
    history = recent_history(root, limit=min(10, effective.max_history_entries), limits=effective)
    if inspect_repository(root) != inventory or mapping.git != inventory.git:
        raise InventoryError("Repository inventory or identity changed during overview; retry.")
    for path, signature in signatures.items():
        if signature is None:
            raise InventoryError("Repository eligibility changed during overview; retry.")
        require_unchanged(root, path, signature)
    return document_model(
        project_name=inventory.project_name,
        git=inventory.git,
        complete=mapping.complete and not diagnostics and history.complete,
        inventory=inventory,
        source_map=mapping,
        packaging=packaging,
        documentation=docs,
        entry_points=entries,
        recent_history=history,
        diagnostics=diagnostics,
        **({"manifests": manifests} if document_model is RepositoryOverview else {}),
    )
