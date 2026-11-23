"""Data models for the AgentForge central orchestration engine."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from backend.app.orchestration.states import (
    TERMINAL_WORKFLOW_STATES,
    StageStatus,
    StageType,
    WorkflowStatus,
)


class ExecutionLimits(BaseModel):
    """Execution boundaries and limits enforced on workflow runs."""

    max_stage_retries: int = Field(
        default=2,
        ge=0,
        le=5,
        description="Maximum retry attempts per individual stage",
    )
    stage_timeout_seconds: int = Field(
        default=300,
        ge=10,
        le=3600,
        description="Maximum execution duration per stage in seconds",
    )
    workflow_timeout_seconds: int = Field(
        default=1800,
        ge=60,
        le=86400,
        description="Overall workflow timeout duration in seconds",
    )
    max_repair_attempts: int = Field(
        default=3,
        ge=1,
        le=5,
        description="Maximum repair loop iterations inherited from Sprint 6",
    )


class WorkflowConfiguration(BaseModel):
    """Runtime flags and feature toggles for workflow execution."""

    auto_repair_enabled: bool = Field(
        default=True,
        description="Whether automatic repair loop is enabled upon test failure",
    )
    review_enabled: bool = Field(
        default=True,
        description="Whether deterministic and static review checks are run",
    )
    dry_run: bool = Field(
        default=False,
        description="Simulate release without publishing actual pull requests",
    )
    target_branch: str = Field(
        default="main",
        min_length=1,
        description="Target repository branch for release candidate PRs",
    )
    custom_settings: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary stage-specific custom configuration overrides",
    )
    limits: ExecutionLimits = Field(
        default_factory=ExecutionLimits,
        description="Resource bounds and execution limits",
    )


class WorkflowRequest(BaseModel):
    """Request payload to initiate an autonomous engineering workflow run."""

    workflow_id: str = Field(
        default_factory=lambda: f"wf-{uuid4().hex[:12]}",
        description="Unique identifier for the requested workflow run",
    )
    repository_id: str = Field(
        ...,
        min_length=1,
        description="Target repository identifier or workspace path",
    )
    issue_reference: str = Field(
        ...,
        min_length=1,
        description="Issue identifier, URL, or raw problem description",
    )
    requested_by: str = Field(
        ...,
        min_length=1,
        description="Identity of the operator or agent initiating the run",
    )
    target_branch: str = Field(
        default="main",
        min_length=1,
        description="Target branch for the release candidate",
    )
    configuration: WorkflowConfiguration = Field(
        default_factory=WorkflowConfiguration,
        description="Workflow runtime configuration",
    )
    idempotency_key: str | None = Field(
        default=None,
        description="Client-provided idempotency key preventing duplicate runs",
    )
    execution_limits: ExecutionLimits = Field(
        default_factory=ExecutionLimits,
        description="Resource bounds and timeout thresholds",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional caller-provided context or tracking tags",
    )

    @field_validator("workflow_id", "repository_id", "requested_by")
    @classmethod
    def validate_non_empty(cls, v: str) -> str:
        """Ensure string fields contain non-whitespace characters."""
        if not v or not v.strip():
            raise ValueError("Field cannot be empty or whitespace only")
        return v.strip()


class StateTransitionRecord(BaseModel):
    """Immutable audit record of a workflow state transition."""

    transition_id: str = Field(
        default_factory=lambda: f"tr-{uuid4().hex[:8]}",
        description="Unique transition event identifier",
    )
    from_state: WorkflowStatus = Field(
        description="Source workflow status prior to transition"
    )
    to_state: WorkflowStatus = Field(
        description="Target workflow status after transition"
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="ISO 8601 timestamp of transition execution",
    )
    reason: str | None = Field(
        default=None,
        description="Operational rationale or error description for transition",
    )
    actor: str | None = Field(
        default=None,
        description="Actor or component that triggered the transition",
    )


class WorkflowStage(BaseModel):
    """Execution status and metadata for an individual pipeline stage."""

    stage_id: str = Field(
        default_factory=lambda: f"stg-{uuid4().hex[:8]}",
        description="Unique stage execution instance identifier",
    )
    stage_type: StageType = Field(
        description="Pipeline stage identifier",
    )
    status: StageStatus = Field(
        default=StageStatus.PENDING,
        description="Current lifecycle status of the stage",
    )
    input_artifact_ids: list[str] = Field(
        default_factory=list,
        description="IDs of upstream artifacts consumed as inputs",
    )
    output_artifact_ids: list[str] = Field(
        default_factory=list,
        description="IDs of artifacts generated as outputs by this stage",
    )
    started_at: datetime | None = Field(
        default=None,
        description="Timestamp when stage execution commenced",
    )
    completed_at: datetime | None = Field(
        default=None,
        description="Timestamp when stage execution concluded",
    )
    attempt_count: int = Field(
        default=0,
        ge=0,
        description="Number of attempts executed for this stage",
    )
    duration_seconds: float = Field(
        default=0.0,
        ge=0.0,
        description="Total execution time spent in this stage",
    )
    error: str | None = Field(
        default=None,
        description="Error details if stage failed",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Stage-specific execution metadata",
    )


class WorkflowRun(BaseModel):
    """Complete lifecycle record representing an active or completed run."""

    workflow_id: str = Field(
        description="Unique workflow run identifier",
    )
    status: WorkflowStatus = Field(
        default=WorkflowStatus.CREATED,
        description="Current lifecycle status of the workflow run",
    )
    current_stage: StageType | None = Field(
        default=None,
        description="Stage currently executing or awaiting action",
    )
    completed_stages: list[StageType] = Field(
        default_factory=list,
        description="Ordered list of successfully completed stages",
    )
    failed_stage: StageType | None = Field(
        default=None,
        description="Stage where unrecoverable failure occurred, if any",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp when workflow run was created",
    )
    started_at: datetime | None = Field(
        default=None,
        description="Timestamp when execution began",
    )
    completed_at: datetime | None = Field(
        default=None,
        description="Timestamp when workflow reached terminal state",
    )
    last_updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp of most recent state update",
    )
    requested_by: str = Field(
        description="Identity of the operator who requested the workflow",
    )
    repository_reference: str = Field(
        description="Target repository identifier or repository URL",
    )
    issue_reference: str = Field(
        description="Target issue identifier, number, or description",
    )
    target_branch: str = Field(
        default="main",
        description="Target base branch for release candidate",
    )
    cancellation_reason: str | None = Field(
        default=None,
        description="User or system rationale if workflow was cancelled",
    )
    error_summary: str | None = Field(
        default=None,
        description="High-level failure summary if workflow failed",
    )
    stages: dict[str, WorkflowStage] = Field(
        default_factory=dict,
        description="Execution state records for each stage keyed by StageType",
    )
    transition_history: list[StateTransitionRecord] = Field(
        default_factory=list,
        description="Chronological audit history of state transitions",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Workflow run metadata and telemetry tags",
    )

    @property
    def is_terminal(self) -> bool:
        """Return True if workflow is in a terminal non-resumable state."""
        return self.status in TERMINAL_WORKFLOW_STATES

    def get_stage(self, stage_type: StageType) -> WorkflowStage | None:
        """Retrieve existing stage state record if present."""
        return self.stages.get(stage_type.value)

    def get_or_create_stage(self, stage_type: StageType) -> WorkflowStage:
        """Retrieve or initialize stage state record for the given stage type."""
        key = stage_type.value
        if key not in self.stages:
            self.stages[key] = WorkflowStage(stage_type=stage_type)
        return self.stages[key]

    def add_completed_stage(self, stage_type: StageType) -> None:
        """Record stage completion in completed_stages list idempotently."""
        if stage_type not in self.completed_stages:
            self.completed_stages.append(stage_type)
