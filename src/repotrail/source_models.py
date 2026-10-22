"""Versioned Python source-map contract, independent of the inventory schema."""

from pathlib import PurePosixPath
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import FileEntry, GitIdentity


class SourceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class AnalysisLimits(SourceModel):
    max_python_files: int = Field(default=500, ge=1, le=500)
    max_file_bytes: int = Field(default=1_048_576, ge=1, le=1_048_576)
    max_total_bytes: int = Field(default=10_485_760, ge=1, le=10_485_760)
    max_ast_nodes: int = Field(default=50_000, ge=1, le=50_000)


DEFAULT_LIMITS = AnalysisLimits()


class Declaration(SourceModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    qualified_name: str = Field(min_length=1)
    kind: Literal["class", "function", "method"]
    parent_qualified_name: str | None
    is_async: bool
    definition_line: int = Field(ge=1)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)

    @model_validator(mode="after")
    def coherent_declaration(self) -> Self:
        expected = (
            f"{self.parent_qualified_name}.{self.name}" if self.parent_qualified_name else self.name
        )
        if (
            self.qualified_name != expected
            or not self.start_line <= self.definition_line <= self.end_line
        ):
            raise ValueError("declaration name or source range is inconsistent")
        if (self.kind == "class" and self.is_async) or (
            self.kind == "method" and self.parent_qualified_name is None
        ):
            raise ValueError("declaration kind/async state is inconsistent")
        return self


class ImportName(SourceModel):
    name: str = Field(min_length=1)
    asname: str | None = Field(default=None, min_length=1)


class StaticImport(SourceModel):
    kind: Literal["import", "from"]
    module: str | None = Field(default=None, min_length=1)
    level: int = Field(ge=0)
    names: list[ImportName] = Field(min_length=1)
    scope: str | None = Field(default=None, min_length=1)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)

    @model_validator(mode="after")
    def coherent_import(self) -> Self:
        if self.start_line > self.end_line:
            raise ValueError("import source range is reversed")
        if self.kind == "import" and (self.module is not None or self.level != 0):
            raise ValueError("plain import must have null module and level zero")
        if self.kind == "from" and self.module is None and self.level == 0:
            raise ValueError("absolute from-import must name a module")
        return self


class Diagnostic(SourceModel):
    code: Literal["syntax_error", "encoding_error", "read_error", "file_too_large", "parser_limit"]
    message: str = Field(min_length=1, max_length=200)
    source_line: int | None = Field(default=None, ge=1)


def declaration_key(symbol: Declaration) -> tuple:
    return (
        symbol.start_line,
        symbol.definition_line,
        symbol.end_line,
        symbol.qualified_name,
        symbol.kind,
        symbol.id,
    )


def import_key(statement: StaticImport) -> tuple:
    return (
        statement.start_line,
        statement.end_line,
        statement.scope or "",
        statement.kind,
        statement.module or "",
        statement.level,
        tuple((name.name, name.asname or "") for name in statement.names),
    )


class PythonFile(SourceModel):
    path: str
    tracked: bool
    status: Literal["analyzed", "error"]
    size_bytes: int = Field(ge=0)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    line_count: int | None = Field(default=None, ge=0)
    symbols: list[Declaration] = Field(default_factory=list)
    imports: list[StaticImport] = Field(default_factory=list)
    diagnostics: list[Diagnostic] = Field(default_factory=list)

    @field_validator("path")
    @classmethod
    def python_path(cls, value: str) -> str:
        value = FileEntry.relative_posix_path(value)
        if PurePosixPath(value).suffix.lower() != ".py":
            raise ValueError("source-map files must have a .py suffix")
        return value

    @model_validator(mode="after")
    def coherent_file(self) -> Self:
        if self.status == "analyzed":
            if self.diagnostics or self.sha256 is None or self.line_count is None:
                raise ValueError("analyzed files require hash/line count and no diagnostics")
        else:
            if len(self.diagnostics) != 1 or self.symbols or self.imports:
                raise ValueError(
                    "error files require one diagnostic and empty declarations/imports"
                )
            code = self.diagnostics[0].code
            read_succeeded = code not in ("read_error", "file_too_large")
            decoded = code in ("syntax_error", "parser_limit")
            if (self.sha256 is not None) != read_succeeded or (
                self.line_count is not None
            ) != decoded:
                raise ValueError("error hash/line count must agree with the failed stage")
        if self.symbols != sorted(self.symbols, key=declaration_key) or self.imports != sorted(
            self.imports, key=import_key
        ):
            raise ValueError("declarations and imports must be sorted by source location")
        ids: set[str] = set()
        parents: list[Declaration] = []
        for symbol in self.symbols:
            expected_id = f"{self.path}::{symbol.qualified_name}:{symbol.definition_line}"
            if symbol.id != expected_id or symbol.id in ids:
                raise ValueError(
                    "declaration IDs must be unique and match path/name/definition line"
                )
            ids.add(symbol.id)
            if self.line_count is None or symbol.end_line > self.line_count:
                raise ValueError("declaration range exceeds source line count")
            while parents and symbol.definition_line > parents[-1].end_line:
                parents.pop()
            parent = parents[-1] if parents else None
            if symbol.parent_qualified_name != (parent.qualified_name if parent else None):
                raise ValueError("declaration parent must agree with lexical source containment")
            expected_kind = "method" if parent and parent.kind == "class" else "function"
            if symbol.kind != "class" and symbol.kind != expected_kind:
                raise ValueError("method/function kind must agree with its lexical parent")
            if parent and (
                symbol.start_line <= parent.definition_line or symbol.end_line > parent.end_line
            ):
                raise ValueError("nested declaration range must fit its parent")
            parents.append(symbol)
        for statement in self.imports:
            if self.line_count is None or statement.end_line > self.line_count:
                raise ValueError("import range exceeds source line count")
        for diagnostic in self.diagnostics:
            if diagnostic.source_line is not None and (
                self.line_count is None or diagnostic.source_line > self.line_count
            ):
                raise ValueError("diagnostic line exceeds decoded source line count")
        return self


class SourceCounts(SourceModel):
    total_python_files: int = Field(ge=0)
    analyzed_files: int = Field(ge=0)
    failed_files: int = Field(ge=0)
    declarations: int = Field(ge=0)
    import_statements: int = Field(ge=0)

    @classmethod
    def from_files(cls, files: list[PythonFile]) -> Self:
        analyzed = sum(file.status == "analyzed" for file in files)
        return cls(
            total_python_files=len(files),
            analyzed_files=analyzed,
            failed_files=len(files) - analyzed,
            declarations=sum(len(file.symbols) for file in files),
            import_statements=sum(len(file.imports) for file in files),
        )


class PythonSourceMap(SourceModel):
    document_type: Literal["python_source_map"] = "python_source_map"
    schema_version: Literal["1"] = "1"
    project_name: str = Field(min_length=1)
    git: GitIdentity
    complete: bool
    files: list[PythonFile]
    counts: SourceCounts
    limits: AnalysisLimits

    @model_validator(mode="after")
    def coherent_map(self) -> Self:
        paths = [file.path for file in self.files]
        if paths != sorted(set(paths)):
            raise ValueError("source-map file paths must be sorted and unique")
        expected = SourceCounts.from_files(self.files)
        if self.counts != expected or self.complete != (expected.failed_files == 0):
            raise ValueError("source-map counts/completeness must reconcile with its files")
        if len(self.files) > self.limits.max_python_files:
            raise ValueError("source map exceeds the Python-file limit")
        ids = [symbol.id for file in self.files for symbol in file.symbols]
        if len(ids) != len(set(ids)):
            raise ValueError("declaration IDs must be unique within the document")
        for file in self.files:
            oversized = bool(file.diagnostics and file.diagnostics[0].code == "file_too_large")
            if oversized != (file.size_bytes > self.limits.max_file_bytes):
                raise ValueError("file size and file-too-large status must agree with limits")
        return self
