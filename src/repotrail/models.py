"""Validated, versioned output contract for the inventory."""

from pathlib import PurePosixPath
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class GitIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    head_commit: str | None = Field(default=None, pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    branch: str | None
    detached: bool
    unborn: bool

    @model_validator(mode="after")
    def consistent_history(self) -> Self:
        if self.unborn != (self.head_commit is None):
            raise ValueError("unborn must agree with the absence of a commit")
        if self.detached != (self.branch is None) or (self.unborn and self.detached):
            raise ValueError("HEAD must be attached, detached, or attached and unborn")
        if self.branch == "":
            raise ValueError("branch must be nonempty when present")
        return self


class FileEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    path: str
    extension: str
    tracked: bool

    @field_validator("path")
    @classmethod
    def relative_posix_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if not value or path.is_absolute() or ".." in path.parts or str(path) != value:
            raise ValueError("path must be a normalized repository-relative POSIX path")
        if "\0" in value or value == ".":
            raise ValueError("path must name a file")
        return value

    @model_validator(mode="after")
    def matching_extension(self) -> Self:
        if self.extension != PurePosixPath(self.path).suffix.lower():
            raise ValueError("extension must be the lowercase final suffix")
        return self


class FileCounts(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    total_files: int = Field(ge=0)
    tracked_files: int = Field(ge=0)
    untracked_files: int = Field(ge=0)


class Inventory(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: Literal["1"] = "1"
    project_name: str = Field(min_length=1)
    git: GitIdentity
    files: list[FileEntry]
    counts: FileCounts

    @model_validator(mode="after")
    def reconciled_inventory(self) -> Self:
        paths = [entry.path for entry in self.files]
        if paths != sorted(set(paths)):
            raise ValueError("file paths must be sorted and unique")
        tracked = sum(entry.tracked for entry in self.files)
        if (self.counts.total_files, self.counts.tracked_files, self.counts.untracked_files) != (
            len(self.files),
            tracked,
            len(self.files) - tracked,
        ):
            raise ValueError("counts must reconcile with the returned files")
        return self
