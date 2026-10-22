"""Versioned v1 evidence contracts, separate from the compatible M01/M02 documents."""

import hashlib
import json
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from .models import FileEntry, GitIdentity, Inventory
from .source_models import Declaration, PythonSourceMap, SourceModel


def stable_digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()
    ).hexdigest()


class EvidenceLimits(SourceModel):
    max_history_entries: int = Field(default=100, ge=1, le=100)
    max_changed_files: int = Field(default=100, ge=1, le=100)
    max_patch_context_bytes: int = Field(default=1_048_576, ge=1, le=1_048_576)
    max_document_bytes: int = Field(default=65_536, ge=1, le=1_048_576)
    max_context_bytes: int = Field(default=16_384, ge=1, le=1_048_576)


DEFAULT_EVIDENCE_LIMITS = EvidenceLimits()


class EvidenceDiagnostic(SourceModel):
    code: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=300)
    path: str | None = None


class SourceReference(SourceModel):
    id: str
    source_kind: Literal["working_tree", "git_blob"]
    path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    commit_oid: str | None = Field(default=None, pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    blob_oid: str | None = Field(default=None, pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")

    @field_validator("path")
    @classmethod
    def relative_path(cls, value: str) -> str:
        return FileEntry.relative_posix_path(value)

    @model_validator(mode="after")
    def coherent_reference(self) -> Self:
        if self.start_line > self.end_line:
            raise ValueError("reference range is reversed")
        historical = self.source_kind == "git_blob"
        if historical != (self.commit_oid is not None and self.blob_oid is not None):
            raise ValueError("historical references require immutable commit and blob IDs")
        if not historical and (self.commit_oid is not None or self.blob_oid is not None):
            raise ValueError("working-tree references cannot claim a historical revision")
        if self.id != "src:" + stable_digest(self.model_dump(exclude={"id"})):
            raise ValueError("citation ID does not match its typed source reference")
        return self


class NumberedLine(SourceModel):
    number: int = Field(ge=1)
    text: str


class SourceExcerpt(SourceModel):
    reference: SourceReference
    source_line_count: int = Field(ge=1)
    requested_start_line: int = Field(ge=1)
    requested_end_line: int = Field(ge=1)
    lines: list[NumberedLine] = Field(min_length=1)
    complete: bool

    @model_validator(mode="after")
    def coherent_excerpt(self) -> Self:
        ref = self.reference
        if (
            not 1
            <= self.requested_start_line
            <= ref.start_line
            <= ref.end_line
            <= self.requested_end_line
            <= self.source_line_count
        ):
            raise ValueError("citation is outside its retained/actual source range")
        if [line.number for line in self.lines] != list(range(ref.start_line, ref.end_line + 1)):
            raise ValueError("numbered source does not match its citation range")
        if any("\n" in line.text or "\r" in line.text for line in self.lines):
            raise ValueError("numbered source must contain physical lines")
        if self.complete != (
            ref.start_line == self.requested_start_line and ref.end_line == self.requested_end_line
        ):
            raise ValueError("excerpt completeness must describe the requested range")
        return self


class SymbolContext(SourceModel):
    document_type: Literal["symbol_context"] = "symbol_context"
    schema_version: Literal["1"] = "1"
    project_name: str
    git: GitIdentity
    path: str
    qualified_name: str
    status: Literal["found", "ambiguous", "not_found", "unavailable"]
    complete: bool
    definition_lines: list[int] = Field(default_factory=list)
    excerpt: SourceExcerpt | None = None
    diagnostics: list[EvidenceDiagnostic] = Field(default_factory=list)


class CommitMetadata(SourceModel):
    oid: str
    parents: list[str]
    author: str
    subject: str
    metadata_truncated: bool = False


class HistoryDocument(SourceModel):
    document_type: Literal["local_history"] = "local_history"
    schema_version: Literal["1"] = "1"
    project_name: str
    git: GitIdentity
    requested_ref: str
    resolved_oid: str | None
    commits: list[CommitMetadata]
    limit: int
    has_more: bool
    complete: bool


class PackagingMetadata(SourceModel):
    format: Literal["pyproject.toml", "absent_or_unsupported"]
    name: str | None = None
    requires_python: str | None = None
    dependencies: list[str] = Field(default_factory=list)
    optional_dependencies: dict[str, list[str]] = Field(default_factory=dict)
    scripts: dict[str, str] = Field(default_factory=dict)
    gui_scripts: dict[str, str] = Field(default_factory=dict)
    evidence: SourceExcerpt | None = None


class EntryPoint(SourceModel):
    kind: Literal["project_script", "gui_script", "module_main_file"]
    name: str
    target: str
    reference: SourceReference


class ProjectOverview(SourceModel):
    document_type: Literal["project_overview"] = "project_overview"
    schema_version: Literal["1"] = "1"
    project_name: str
    git: GitIdentity
    complete: bool
    inventory: Inventory
    source_map: PythonSourceMap
    packaging: PackagingMetadata
    documentation: list[SourceExcerpt]
    entry_points: list[EntryPoint]
    recent_history: HistoryDocument
    diagnostics: list[EvidenceDiagnostic]


class ChangedRange(SourceModel):
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)


class TextHunk(SourceModel):
    old_start: int = Field(ge=0)
    old_count: int = Field(ge=0)
    new_start: int = Field(ge=0)
    new_count: int = Field(ge=0)
    patch: str
    old_changed_ranges: list[ChangedRange]
    new_changed_ranges: list[ChangedRange]
    old_excerpt: SourceExcerpt | None
    new_excerpt: SourceExcerpt | None


class ChangedFile(SourceModel):
    status: Literal["A", "D", "M", "T"]
    old_path: str | None
    new_path: str | None
    old_blob: str | None
    new_blob: str | None
    old_sha256: str | None = None
    new_sha256: str | None = None
    binary: bool = False
    complete: bool
    hunks: list[TextHunk] = Field(default_factory=list)
    old_symbols: list[Declaration] = Field(default_factory=list)
    new_symbols: list[Declaration] = Field(default_factory=list)
    diagnostics: list[EvidenceDiagnostic] = Field(default_factory=list)


class ChangeDocument(SourceModel):
    document_type: Literal["committed_changes"] = "committed_changes"
    schema_version: Literal["1"] = "1"
    project_name: str
    base_ref: str
    head_ref: str
    base_oid: str
    head_oid: str
    comparison_kind: Literal["endpoint_trees"] = "endpoint_trees"
    rename_policy: Literal["no_renames_delete_add"] = "no_renames_delete_add"
    complete: bool
    files: list[ChangedFile]
    status_counts: dict[str, int]
    excluded_changes: int
    log_range: str
    commits: list[CommitMetadata]
    log_has_more: bool
    returned_patch_context_bytes: int
    limits: EvidenceLimits

    @model_validator(mode="after")
    def coherent_changes(self) -> Self:
        raw_paths = [file.new_path or file.old_path for file in self.files]
        if any(path is None for path in raw_paths):
            raise ValueError("changed files require a path")
        paths = [path for path in raw_paths if path is not None]
        if paths != sorted(set(paths)):
            raise ValueError("changed paths must be sorted and unique")
        if self.status_counts != {
            status: sum(file.status == status for file in self.files)
            for status in sorted({file.status for file in self.files})
        }:
            raise ValueError("changed-file counts do not reconcile")
        if (
            len(self.files) > self.limits.max_changed_files
            or self.returned_patch_context_bytes > self.limits.max_patch_context_bytes
        ):
            raise ValueError("comparison exceeds its effective bounds")
        if self.complete and (self.log_has_more or any(not file.complete for file in self.files)):
            raise ValueError("incomplete file/log evidence cannot be labeled complete")
        return self
