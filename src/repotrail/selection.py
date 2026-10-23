"""Deterministic bounded evidence selection; no embeddings or model calls."""

import json
import os
from pathlib import Path
from typing import Any

from .ai_models import EvidencePackage, GenerationConfig, Task
from .evidence import excerpt_text, get_symbol_context
from .git import repository_root
from .history import compare_repository_revisions
from .multilang import get_repository_overview
from .product_models import NumberedLine, SourceExcerpt, SourceReference, stable_digest


def repository_id(root: Path) -> str:
    # Scope matching relative paths/content to their actual canonical local repository.
    return stable_digest(
        {
            "canonical_root": str(root.resolve()).casefold()
            if os.name == "nt"
            else str(root.resolve())
        }
    )


def source_text(sources: list[SourceExcerpt]) -> str:
    return "\n\n".join(
        f"REFERENCE {source.reference.id}\n{excerpt_text(source)}" for source in sources
    )


def trim_source(source: SourceExcerpt, available: int) -> SourceExcerpt | None:
    lines: list[NumberedLine] = []
    for line in source.lines:
        candidate = [*lines, line]
        header = f"REFERENCE {'src:' + '0' * 64}\n"
        if (
            len((header + "\n".join(f"{item.number}: {item.text}" for item in candidate)).encode())
            > available
        ):
            break
        lines.append(line)
    if not lines:
        return None
    fields = source.reference.model_dump(exclude={"id"})
    fields["end_line"] = lines[-1].number
    reference = SourceReference(id="src:" + stable_digest(fields), **fields)
    return SourceExcerpt(
        reference=reference,
        source_line_count=source.source_line_count,
        requested_start_line=source.requested_start_line,
        requested_end_line=source.requested_end_line,
        lines=lines,
        complete=lines[-1].number == source.requested_end_line,
    )


def collect_evidence(
    supplied: str | Path,
    task: Task,
    config: GenerationConfig,
    base_ref: str | None = None,
    head_ref: str | None = None,
) -> EvidencePackage:
    root = repository_root(supplied)
    candidates: list[SourceExcerpt] = []
    omissions: list[str] = []
    if task == "explain_project":
        document = get_repository_overview(root)
        manifest = stable_digest(document.model_dump(mode="json"))
        candidates.extend(document.documentation)
        if document.packaging.evidence:
            candidates.append(document.packaging.evidence)
        candidates.extend(manifest.evidence for manifest in document.manifests)
        eligible = [
            file for file in document.source_map.files if file.symbols and file.status == "analyzed"
        ]
        sample = []
        for language in ("python", "javascript", "java"):
            first = next((file for file in eligible if file.language == language), None)
            if first:
                sample.append(first)
        sample.extend(file for file in eligible if file not in sample)
        for file in sample[:3]:
            symbol = file.symbols[0]
            context = get_symbol_context(
                root,
                file.path,
                symbol.qualified_name,
                symbol.definition_line,
                source_map=document.source_map,
            )
            if context.excerpt:
                candidates.append(context.excerpt)
        if len(eligible) > 3:
            omissions.append(
                f"Selected three declaration-bearing files, sampling each supported language first; {len(eligible) - 3} additional files were not supplied as source."
            )
        metadata: dict[str, Any] = {
            "project_name": document.project_name,
            "git": document.git.model_dump(),
            "inventory_counts": document.inventory.counts.model_dump(),
            "source_counts": document.source_map.counts.model_dump(),
            "packaging_format": document.packaging.format,
            "declared_dependencies": document.packaging.dependencies[:20],
            "entry_points": [entry.model_dump(mode="json") for entry in document.entry_points[:10]],
        }
        diagnostics = [*document.diagnostics]
        for file in document.source_map.files:
            if file.diagnostics:
                from .product_models import EvidenceDiagnostic

                diagnostics.extend(
                    EvidenceDiagnostic(
                        code=diagnostic.code, message=diagnostic.message, path=file.path
                    )
                    for diagnostic in file.diagnostics
                )
        complete = document.complete
    else:
        if base_ref is None or head_ref is None:
            from .git import InventoryError

            raise InventoryError("Change explanation/review requires both base_ref and head_ref.")
        comparison = compare_repository_revisions(root, base_ref, head_ref)
        manifest = stable_digest(comparison.model_dump(mode="json"))
        for changed_file in comparison.files:
            for hunk in changed_file.hunks:
                candidates.extend(
                    excerpt
                    for excerpt in (hunk.old_excerpt, hunk.new_excerpt)
                    if excerpt is not None
                )
        metadata = {
            "project_name": comparison.project_name,
            "base_ref": base_ref,
            "head_ref": head_ref,
            "base_oid": comparison.base_oid,
            "head_oid": comparison.head_oid,
            "comparison_kind": comparison.comparison_kind,
            "rename_policy": comparison.rename_policy,
            "status_counts": comparison.status_counts,
            "excluded_changes": comparison.excluded_changes,
            "files": [
                {
                    "old_path": file.old_path,
                    "new_path": file.new_path,
                    "status": file.status,
                    "old_symbols": [symbol.qualified_name for symbol in file.old_symbols],
                    "new_symbols": [symbol.qualified_name for symbol in file.new_symbols],
                }
                for file in comparison.files[:20]
            ],
            "commits": [commit.model_dump() for commit in comparison.commits[:10]],
            "log_is_not_causation": True,
        }
        diagnostics = [diagnostic for file in comparison.files for diagnostic in file.diagnostics]
        complete = comparison.complete
        if len(comparison.files) > 20:
            omissions.append("Changed-file metadata includes only the first twenty paths.")
    selected: list[SourceExcerpt] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate.reference.id in seen:
            continue
        available = (
            config.max_source_bytes - len(source_text(selected).encode()) - (2 if selected else 0)
        )
        trimmed = trim_source(candidate, max(0, available))
        if trimmed is None:
            omissions.append(
                f"Source omitted for {candidate.reference.path}: selected text budget exhausted."
            )
            continue
        seen.add(candidate.reference.id)
        if any(source.reference.id == trimmed.reference.id for source in selected):
            continue
        selected.append(trimmed)
        if not trimmed.complete:
            omissions.append(
                f"Source excerpted for {candidate.reference.path}: only registered retained lines are supplied."
            )
    # Reserve room for system instructions, schema, registry, and a bounded repair turn.
    metadata_limit = max(256, min(12000, config.max_metadata_bytes // 2))
    while len(json.dumps(metadata, ensure_ascii=True).encode()) > metadata_limit:
        removable = next(
            (
                key
                for key in ("files", "entry_points", "declared_dependencies", "commits")
                if key in metadata
            ),
            None,
        )
        if removable is None:
            from .git import InventoryError

            raise InventoryError("Selected metadata cannot fit the configured prompt budget.")
        metadata.pop(removable)
        omissions.append(f"Omitted {removable} metadata to stay within the prompt budget.")
    return EvidencePackage(
        task=task,
        repository_id=repository_id(root),
        manifest_fingerprint=manifest,
        metadata=metadata,
        sources=selected,
        complete=complete and not omissions,
        omissions=omissions,
        diagnostics=diagnostics,
    )
