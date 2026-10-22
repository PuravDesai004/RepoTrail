"""Versioned, keyless evidence packets for the model already running an agent."""

from typing import Any, Literal

from pydantic import Field

from .product_models import EvidenceDiagnostic, SourceExcerpt
from .source_models import SourceModel


class AgentEvidence(SourceModel):
    document_type: Literal["agent_evidence"] = "agent_evidence"
    schema_version: Literal["1"] = "1"
    mode: Literal["commit", "whole", "source"]
    project_name: str
    target_oid: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    metadata: dict[str, Any]
    sources: list[SourceExcerpt] = Field(default_factory=list, max_length=200)
    complete: bool
    omissions: list[str] = Field(default_factory=list, max_length=100)
    diagnostics: list[EvidenceDiagnostic] = Field(default_factory=list, max_length=100)
    next_cursor: str | None = Field(default=None, max_length=2048)
    source_text_bytes: int = Field(ge=0, le=32768)
    metadata_bytes: int = Field(ge=0, le=32768)
