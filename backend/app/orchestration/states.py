"""Workflow and stage lifecycle state enums for central orchestration."""

from enum import StrEnum


class WorkflowStatus(StrEnum):
    """Lifecycle state of an end-to-end engineering workflow."""

    CREATED = "created"
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    PAUSED = "paused"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"


class StageStatus(StrEnum):
    """Lifecycle state of an individual workflow execution stage."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    WAITING = "waiting"
    CANCELLED = "cancelled"


class StageType(StrEnum):
    """Enumeration of standard stages in the AgentForge engineering pipeline."""

    REPOSITORY_ANALYSIS = "repository_analysis"
    ISSUE_ANALYSIS = "issue_analysis"
    IMPLEMENTATION_PLANNING = "implementation_planning"
    CODE_GENERATION = "code_generation"
    EXECUTION_VERIFICATION = "execution_verification"
    AUTOMATIC_REPAIR = "automatic_repair"
    CODE_REVIEW = "code_review"
    HUMAN_APPROVAL = "human_approval"
    CONTROLLED_RELEASE = "controlled_release"


TERMINAL_WORKFLOW_STATES: frozenset[WorkflowStatus] = frozenset(
    {
        WorkflowStatus.COMPLETED,
        WorkflowStatus.FAILED,
        WorkflowStatus.CANCELLED,
        WorkflowStatus.BLOCKED,
    }
)

TERMINAL_STAGE_STATES: frozenset[StageStatus] = frozenset(
    {
        StageStatus.COMPLETED,
        StageStatus.FAILED,
        StageStatus.SKIPPED,
        StageStatus.CANCELLED,
    }
)
