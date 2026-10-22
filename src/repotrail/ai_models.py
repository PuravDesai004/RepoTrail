"""Task-specific grounded outputs and secret-free generation/run contracts."""

from typing import Any, Literal, Self

from pydantic import Field, model_validator

from .product_models import EvidenceDiagnostic, SourceExcerpt, SourceReference
from .source_models import SourceModel

Task = Literal["explain_project", "explain_changes", "review_diff"]
PROMPT_VERSION = "1"
OUTPUT_VERSION = "1"
SELECTION_VERSION = "1"
SUPPORTED_MODELS = ("gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite")


class AIConfigurationError(Exception):
    """Missing optional dependencies or invalid/missing explicit AI configuration."""


class GenerationConfig(SourceModel):
    provider: Literal["gemini"] = "gemini"
    model: str = "gemini-3.8-flash"
    max_attempts: int = Field(default=2, ge=1, le=2)
    timeout_seconds: float = Field(default=30.0, gt=0, le=30)
    max_output_tokens: int = Field(default=1500, ge=128, le=1500)
    max_source_bytes: int = Field(default=32768, ge=256, le=32768)
    max_metadata_bytes: int = Field(default=32768, ge=1024, le=32768)
    temperature: float = Field(default=0.0, ge=0, le=1)
    thinking_level: Literal["low", "medium", "high"] = "low"
    prompt_version: str = PROMPT_VERSION
    output_version: str = OUTPUT_VERSION
    selection_version: str = SELECTION_VERSION

    @model_validator(mode="after")
    def supported_configuration(self) -> Self:
        if self.model not in SUPPORTED_MODELS:
            raise ValueError(
                "Use an explicitly supported stable Gemini Flash ID; latest/preview aliases are unsupported."
            )
        return self


class GroundedClaim(SourceModel):
    text: str = Field(min_length=1, max_length=1500)
    basis: Literal["observed", "inferred"]
    citations: list[str] = Field(min_length=1, max_length=8)


class ProjectExplanation(SourceModel):
    summary: list[GroundedClaim]
    structure: list[GroundedClaim]
    entry_points: list[GroundedClaim]
    selected_flows: list[GroundedClaim]
    unknowns: list[str]


class ChangeExplanation(SourceModel):
    observed_edits: list[GroundedClaim]
    inferred_consequences: list[GroundedClaim]
    unknowns: list[str]

    @model_validator(mode="after")
    def separate_facts_and_inferences(self) -> Self:
        if any(claim.basis != "observed" for claim in self.observed_edits) or any(
            claim.basis != "inferred" for claim in self.inferred_consequences
        ):
            raise ValueError("change observations and inferred consequences must be separated")
        return self


class ReviewFinding(SourceModel):
    severity: Literal["low", "medium", "high", "critical"]
    issue: str = Field(min_length=1, max_length=500)
    citations: list[str] = Field(min_length=1, max_length=8)
    reasoning: str = Field(min_length=1, max_length=1500)
    suggested_validation: str = Field(min_length=1, max_length=1000)
    basis: Literal["inferred"] = "inferred"


class DiffReview(SourceModel):
    observations: list[GroundedClaim]
    findings: list[ReviewFinding]
    unknowns: list[str]


def output_model(
    task: Task,
) -> type[ProjectExplanation] | type[ChangeExplanation] | type[DiffReview]:
    if task == "explain_project":
        return ProjectExplanation
    if task == "explain_changes":
        return ChangeExplanation
    return DiffReview


class EvidencePackage(SourceModel):
    task: Task
    repository_id: str
    manifest_fingerprint: str
    metadata: dict[str, Any]
    sources: list[SourceExcerpt]
    complete: bool
    omissions: list[str]
    diagnostics: list[EvidenceDiagnostic]


class NodeEvent(SourceModel):
    node: str
    elapsed_ms: float
    details: dict[str, Any]


class WorkflowResult(SourceModel):
    document_type: Literal["grounded_analysis"] = "grounded_analysis"
    schema_version: Literal["1"] = "1"
    run_id: str
    task: Task
    status: Literal["completed", "incomplete", "failed", "interrupted", "stale"]
    answer: dict[str, Any] | None = None
    citations: list[SourceReference] = Field(default_factory=list)
    evidence_complete: bool
    omissions: list[str]
    evidence_diagnostics: list[EvidenceDiagnostic]
    errors: list[str] = Field(default_factory=list)
    provider: str
    model: str
    backend: str
    attempts: int
    cache_status: Literal["hit", "miss", "invalid", "not_checked"]
    selected_source_bytes: int
    selected_metadata_bytes: int
    usage: list[dict[str, Any]] = Field(default_factory=list)
    node_events: list[NodeEvent] = Field(default_factory=list)
