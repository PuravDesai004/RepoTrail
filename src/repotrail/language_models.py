"""Repository source-map v2; the published Python source-map v1 stays unchanged."""

from pathlib import PurePosixPath
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from .models import FileEntry, GitIdentity, Inventory
from .product_models import (
    ChangedFile,
    ChangeDocument,
    EntryPoint,
    EvidenceDiagnostic,
    HistoryDocument,
    PackagingMetadata,
    SourceExcerpt,
)
from .source_models import Declaration, Diagnostic, SourceModel

LanguageName = Literal["python", "javascript", "java"]
ManifestFormat = Literal["package.json", "pom.xml", "build.gradle", "build.gradle.kts"]
EXTENSIONS: dict[str, LanguageName] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".java": "java",
}


def language_for(path: str) -> LanguageName | None:
    return EXTENSIONS.get(PurePosixPath(path).suffix.lower())


class RepositoryLimits(SourceModel):
    max_source_files: int = Field(default=500, ge=1, le=500)
    max_file_bytes: int = Field(default=1_048_576, ge=1, le=1_048_576)
    max_total_bytes: int = Field(default=10_485_760, ge=1, le=10_485_760)
    max_ast_nodes: int = Field(default=50_000, ge=1, le=50_000)


class LanguageDeclaration(Declaration):
    language: LanguageName
    syntax_kind: str = Field(min_length=1, max_length=80)
    definition_column: int = Field(default=1, ge=1)


class LanguageImport(SourceModel):
    syntax_kind: str = Field(min_length=1, max_length=80)
    module: str
    names: list[str] = Field(default_factory=list)
    scope: str | None = None
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.start_line > self.end_line:
            raise ValueError("import range is reversed")
        return self


def symbol_key(symbol: LanguageDeclaration) -> tuple:
    return (
        symbol.start_line,
        symbol.definition_line,
        symbol.definition_column,
        symbol.end_line,
        symbol.qualified_name,
        symbol.id,
    )


class RepositoryFile(SourceModel):
    path: str
    language: LanguageName
    tracked: bool
    status: Literal["analyzed", "error"]
    size_bytes: int = Field(ge=0)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    line_count: int | None = Field(default=None, ge=0)
    symbols: list[LanguageDeclaration] = Field(default_factory=list)
    imports: list[LanguageImport] = Field(default_factory=list)
    diagnostics: list[Diagnostic] = Field(default_factory=list)

    @field_validator("path")
    @classmethod
    def relative_path(cls, value: str) -> str:
        return FileEntry.relative_posix_path(value)

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if language_for(self.path) != self.language:
            raise ValueError("language must match the supported file extension")
        if self.status == "analyzed":
            if self.diagnostics or self.sha256 is None or self.line_count is None:
                raise ValueError("analyzed source requires hash and line count, no errors")
        elif len(self.diagnostics) != 1 or self.symbols or self.imports:
            raise ValueError("failed source requires one diagnostic and no parsed facts")
        else:
            code = self.diagnostics[0].code
            if (self.sha256 is not None) != (code not in {"read_error", "file_too_large"}):
                raise ValueError("hash must reflect a successful read")
            if (self.line_count is not None) != (code in {"syntax_error", "parser_limit"}):
                raise ValueError("line count must reflect successful decoding")
        if self.symbols != sorted(self.symbols, key=symbol_key):
            raise ValueError("symbols must be in deterministic source order")
        ids = set()
        for symbol in self.symbols:
            expected = (
                f"{self.path}::{symbol.qualified_name}:"
                f"{symbol.definition_line}:{symbol.definition_column}"
            )
            if symbol.id != expected or symbol.id in ids or symbol.language != self.language:
                raise ValueError("symbol identity/language is inconsistent")
            ids.add(symbol.id)
            if self.line_count is None or symbol.end_line > self.line_count:
                raise ValueError("symbol range exceeds physical source")
        for statement in self.imports:
            if self.line_count is None or statement.end_line > self.line_count:
                raise ValueError("import range exceeds physical source")
        return self


class RepositoryCounts(SourceModel):
    total_source_files: int = Field(ge=0)
    analyzed_files: int = Field(ge=0)
    failed_files: int = Field(ge=0)
    declarations: int = Field(ge=0)
    import_statements: int = Field(ge=0)
    by_language: dict[str, int]

    @classmethod
    def from_files(cls, files: list[RepositoryFile]) -> Self:
        analyzed = sum(file.status == "analyzed" for file in files)
        return cls(
            total_source_files=len(files),
            analyzed_files=analyzed,
            failed_files=len(files) - analyzed,
            declarations=sum(len(file.symbols) for file in files),
            import_statements=sum(len(file.imports) for file in files),
            by_language={
                name: sum(file.language == name for file in files)
                for name in ("python", "javascript", "java")
            },
        )


class RepositorySourceMap(SourceModel):
    document_type: Literal["repository_source_map"] = "repository_source_map"
    schema_version: Literal["2"] = "2"
    project_name: str
    git: GitIdentity
    complete: bool
    files: list[RepositoryFile]
    counts: RepositoryCounts
    limits: RepositoryLimits

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if [file.path for file in self.files] != sorted({file.path for file in self.files}):
            raise ValueError("source paths must be sorted and unique")
        counts = RepositoryCounts.from_files(self.files)
        if counts != self.counts or self.complete != (counts.failed_files == 0):
            raise ValueError("source counts/completeness do not reconcile")
        if len(self.files) > self.limits.max_source_files:
            raise ValueError("source-file bound exceeded")
        for file in self.files:
            oversized = bool(file.diagnostics and file.diagnostics[0].code == "file_too_large")
            if oversized != (file.size_bytes > self.limits.max_file_bytes):
                raise ValueError("per-file byte bound and diagnostic disagree")
        return self


class ManifestEvidence(SourceModel):
    path: str
    format: ManifestFormat
    evidence: SourceExcerpt
    # Observed text only: scripts, XML entities, plugins and build expressions never run.
    interpretation: Literal["source_only"] = "source_only"


class RepositoryOverview(SourceModel):
    document_type: Literal["repository_overview"] = "repository_overview"
    schema_version: Literal["2"] = "2"
    project_name: str
    git: GitIdentity
    complete: bool
    inventory: Inventory
    source_map: RepositorySourceMap
    packaging: PackagingMetadata
    manifests: list[ManifestEvidence]
    documentation: list[SourceExcerpt]
    entry_points: list[EntryPoint]
    recent_history: HistoryDocument
    diagnostics: list[EvidenceDiagnostic]


class RepositoryChangedFile(ChangedFile):
    # v2 adds language/column identities; the old model and its JSON schema are untouched.
    old_symbols: list[LanguageDeclaration] = Field(default_factory=list)  # type: ignore[assignment]
    new_symbols: list[LanguageDeclaration] = Field(default_factory=list)  # type: ignore[assignment]


class RepositoryChanges(ChangeDocument):
    document_type: Literal["repository_changes"] = "repository_changes"  # type: ignore[assignment]
    schema_version: Literal["2"] = "2"  # type: ignore[assignment]
    files: list[RepositoryChangedFile]  # type: ignore[assignment]
