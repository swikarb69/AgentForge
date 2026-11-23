"""Validated execution context and artifact reference tracking for workflows."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from backend.app.orchestration.models import WorkflowConfiguration
from backend.app.orchestration.states import StageType


class WorkflowArtifactReferences(BaseModel):
    """Structured references to artifacts created across workflow stages."""

    repository_analysis_id: str | None = Field(
        default=None,
        description="Repository intelligence analysis identifier",
    )
    repository_analysis_hash: str | None = Field(
        default=None,
        description="SHA-256 fingerprint of repository context",
    )
    issue_analysis_id: str | None = Field(
        default=None,
        description="Issue intelligence analysis identifier",
    )
    issue_analysis_hash: str | None = Field(
        default=None,
        description="SHA-256 fingerprint of analyzed issue requirements",
    )
    implementation_plan_id: str | None = Field(
        default=None,
        description="Implementation planning task plan identifier",
    )
    implementation_plan_hash: str | None = Field(
        default=None,
        description="SHA-256 fingerprint of task plan",
    )
    generation_id: str | None = Field(
        default=None,
        description="Code generation output identifier",
    )
    generation_patch_hash: str | None = Field(
        default=None,
        description="SHA-256 fingerprint of initial generated patch",
    )
    execution_id: str | None = Field(
        default=None,
        description="Execution sandbox verification report identifier",
    )
    execution_verification_hash: str | None = Field(
        default=None,
        description="SHA-256 fingerprint of verification report",
    )
    repair_id: str | None = Field(
        default=None,
        description="Automatic repair session identifier",
    )
    repair_patch_hash: str | None = Field(
        default=None,
        description="SHA-256 fingerprint of repaired patch",
    )
    review_id: str | None = Field(
        default=None,
        description="Code review report identifier",
    )
    review_hash: str | None = Field(
        default=None,
        description="SHA-256 fingerprint of review report",
    )
    approval_id: str | None = Field(
        default=None,
        description="Human approval request identifier",
    )
    approval_hash: str | None = Field(
        default=None,
        description="SHA-256 fingerprint of approval record",
    )
    release_id: str | None = Field(
        default=None,
        description="Controlled release execution identifier",
    )
    release_pr_number: int | None = Field(
        default=None,
        description="Pull request number created during release",
    )
    release_pr_url: str | None = Field(
        default=None,
        description="Pull request HTML URL",
    )


class WorkflowContext(BaseModel):
    """Validated in-flight context maintaining artifact links and history."""

    workflow_id: str = Field(
        description="Unique workflow run identifier",
    )
    repository_id: str = Field(
        description="Target repository reference or path",
    )
    repository_path: Path | None = Field(
        default=None,
        description="Optional local filesystem path to repository",
    )
    issue_reference: str = Field(
        description="Target issue identifier, URL, or problem statement",
    )
    target_branch: str = Field(
        default="main",
        description="Target base branch for release candidate pull request",
    )
    configuration: WorkflowConfiguration = Field(
        default_factory=WorkflowConfiguration,
        description="Workflow configuration options and limits",
    )
    artifacts: WorkflowArtifactReferences = Field(
        default_factory=WorkflowArtifactReferences,
        description="References to produced stage artifacts",
    )
    current_stage: StageType | None = Field(
        default=None,
        description="Currently executing stage type",
    )
    stage_outputs: dict[str, Any] = Field(
        default_factory=dict,
        description="Lightweight output summaries keyed by stage name",
    )
    execution_history: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Chronological log of stage execution events",
    )
    shared_data: dict[str, Any] = Field(
        default_factory=dict,
        description="Ephemeral metadata shared between stage adapters",
    )

    def record_stage_output(self, stage: StageType, output: dict[str, Any]) -> None:
        """Store lightweight output metadata for a stage."""
        self.stage_outputs[stage.value] = output
        self.execution_history.append(
            {
                "stage": stage.value,
                "timestamp": datetime.now(UTC).isoformat(),
                "output_keys": list(output.keys()),
            }
        )

    def get_stage_output(self, stage: StageType) -> dict[str, Any] | None:
        """Retrieve output metadata for a previously completed stage."""
        return self.stage_outputs.get(stage.value)

    def get_effective_patch_hash(self) -> str | None:
        """Return repair patch hash if repaired, else generation patch hash."""
        return self.artifacts.repair_patch_hash or self.artifacts.generation_patch_hash

    def has_artifact(self, artifact_name: str) -> bool:
        """Check if an artifact reference field is populated."""
        return getattr(self.artifacts, artifact_name, None) is not None
