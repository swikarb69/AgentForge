"""Stage interfaces, input/output contracts, and retry policies."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from backend.app.orchestration.context import WorkflowContext
from backend.app.orchestration.models import (
    WorkflowConfiguration,
    WorkflowStage,
)
from backend.app.orchestration.states import StageStatus, StageType


class RetryPolicy(BaseModel):
    """Configuration for stage retry attempts and exponential backoff."""

    max_attempts: int = Field(
        default=2,
        ge=0,
        le=5,
        description="Maximum number of retry attempts for this stage",
    )
    initial_delay_seconds: float = Field(
        default=1.0,
        ge=0.0,
        description="Initial delay before first retry in seconds",
    )
    backoff_multiplier: float = Field(
        default=2.0,
        ge=1.0,
        description="Backoff factor multiplied on each subsequent retry",
    )
    retryable_error_types: list[str] = Field(
        default_factory=list,
        description="List of exception or error names eligible for retry",
    )


class StageDefinition(BaseModel):
    """Specification and execution metadata defining a workflow stage."""

    stage_type: StageType = Field(
        description="Pipeline stage identifier",
    )
    name: str = Field(
        description="Human-readable stage title",
    )
    description: str = Field(
        description="Brief summary of stage responsibility",
    )
    required: bool = Field(
        default=True,
        description="Whether stage is mandatory or can be conditionally skipped",
    )
    prerequisites: list[StageType] = Field(
        default_factory=list,
        description="Stages that must complete before this stage can execute",
    )
    timeout_seconds: int = Field(
        default=300,
        ge=10,
        le=3600,
        description="Maximum execution timeout for this stage in seconds",
    )
    retry_policy: RetryPolicy = Field(
        default_factory=RetryPolicy,
        description="Retry policy governing execution retries on failure",
    )


class StageInput(BaseModel):
    """Execution payload and context delivered to a stage adapter."""

    workflow_id: str = Field(
        description="Identifier of the overarching workflow run",
    )
    repository_path: Path = Field(
        description="Absolute filesystem path to the target repository",
    )
    issue_reference: str = Field(
        description="Issue identifier, URL, or problem statement",
    )
    target_branch: str = Field(
        default="main",
        description="Target branch for the release candidate",
    )
    context: WorkflowContext = Field(
        description="Shared workflow execution context and artifact references",
    )
    stage: WorkflowStage = Field(
        description="Active stage execution tracking model",
    )
    configuration: WorkflowConfiguration = Field(
        description="Runtime flags and policy toggles",
    )
    extra_inputs: dict[str, Any] = Field(
        default_factory=dict,
        description="Stage-specific parameters or overrides",
    )


class StageOutput(BaseModel):
    """Result artifact bundle returned by a stage adapter execution."""

    stage_type: StageType = Field(
        description="Type of stage that produced this output",
    )
    status: StageStatus = Field(
        description="Outcome status of the stage execution",
    )
    produced_artifact_ids: list[str] = Field(
        default_factory=list,
        description="Identifiers of artifacts produced during execution",
    )
    summary: dict[str, Any] = Field(
        default_factory=dict,
        description="Lightweight structured output summary",
    )
    error: str | None = Field(
        default=None,
        description="Error message if stage failed",
    )
    should_pause_for_human: bool = Field(
        default=False,
        description="True if stage requires human intervention/approval to proceed",
    )
    should_skip: bool = Field(
        default=False,
        description="True if stage was conditionally skipped",
    )
    skip_reason: str | None = Field(
        default=None,
        description="Rationale for skipping stage execution",
    )
    requires_repair: bool = Field(
        default=False,
        description="True if execution verification detected failures needing repair",
    )


class WorkflowStageAdapter(ABC):
    """Abstract interface that all stage execution adapters must implement."""

    @abstractmethod
    async def execute(self, stage_input: StageInput) -> StageOutput:
        """Execute stage logic and return structured StageOutput."""
