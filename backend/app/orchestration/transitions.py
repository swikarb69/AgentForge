"""Deterministic state machine and transition rules for workflows and stages."""

from datetime import UTC, datetime

from backend.app.orchestration.exceptions import (
    InvalidStageStateTransitionError,
    InvalidWorkflowStateTransitionError,
)
from backend.app.orchestration.models import (
    StateTransitionRecord,
    WorkflowRun,
    WorkflowStage,
)
from backend.app.orchestration.states import (
    TERMINAL_STAGE_STATES,
    TERMINAL_WORKFLOW_STATES,
    StageStatus,
    WorkflowStatus,
)

VALID_WORKFLOW_TRANSITIONS: dict[WorkflowStatus, set[WorkflowStatus]] = {
    WorkflowStatus.CREATED: {
        WorkflowStatus.QUEUED,
        WorkflowStatus.CANCELLING,
        WorkflowStatus.CANCELLED,
    },
    WorkflowStatus.QUEUED: {
        WorkflowStatus.RUNNING,
        WorkflowStatus.PAUSED,
        WorkflowStatus.CANCELLING,
        WorkflowStatus.CANCELLED,
    },
    WorkflowStatus.RUNNING: {
        WorkflowStatus.WAITING_FOR_APPROVAL,
        WorkflowStatus.PAUSED,
        WorkflowStatus.RETRYING,
        WorkflowStatus.COMPLETED,
        WorkflowStatus.FAILED,
        WorkflowStatus.CANCELLING,
        WorkflowStatus.BLOCKED,
    },
    WorkflowStatus.WAITING_FOR_APPROVAL: {
        WorkflowStatus.RUNNING,
        WorkflowStatus.PAUSED,
        WorkflowStatus.FAILED,
        WorkflowStatus.BLOCKED,
        WorkflowStatus.CANCELLING,
    },
    WorkflowStatus.PAUSED: {
        WorkflowStatus.RUNNING,
        WorkflowStatus.CANCELLING,
        WorkflowStatus.CANCELLED,
    },
    WorkflowStatus.RETRYING: {
        WorkflowStatus.RUNNING,
        WorkflowStatus.FAILED,
        WorkflowStatus.CANCELLING,
    },
    WorkflowStatus.CANCELLING: {
        WorkflowStatus.CANCELLED,
        WorkflowStatus.FAILED,
    },
    WorkflowStatus.COMPLETED: set(),  # Terminal
    WorkflowStatus.FAILED: set(),  # Terminal
    WorkflowStatus.CANCELLED: set(),  # Terminal
    WorkflowStatus.BLOCKED: set(),  # Terminal
}

VALID_STAGE_TRANSITIONS: dict[StageStatus, set[StageStatus]] = {
    StageStatus.PENDING: {
        StageStatus.RUNNING,
        StageStatus.SKIPPED,
        StageStatus.CANCELLED,
    },
    StageStatus.RUNNING: {
        StageStatus.COMPLETED,
        StageStatus.FAILED,
        StageStatus.WAITING,
        StageStatus.CANCELLED,
    },
    StageStatus.WAITING: {
        StageStatus.RUNNING,
        StageStatus.COMPLETED,
        StageStatus.FAILED,
        StageStatus.CANCELLED,
    },
    StageStatus.COMPLETED: set(),  # Terminal
    StageStatus.FAILED: set(),  # Terminal
    StageStatus.SKIPPED: set(),  # Terminal
    StageStatus.CANCELLED: set(),  # Terminal
}


class WorkflowStateMachine:
    """Enforces deterministic lifecycle transitions and audit history."""

    @staticmethod
    def can_transition(from_status: WorkflowStatus, to_status: WorkflowStatus) -> bool:
        """Check if a transition between two workflow statuses is permitted."""
        if from_status == to_status:
            return True
        return to_status in VALID_WORKFLOW_TRANSITIONS.get(from_status, set())

    @classmethod
    def transition(
        cls,
        run: WorkflowRun,
        target_status: WorkflowStatus,
        reason: str | None = None,
        actor: str | None = None,
    ) -> WorkflowRun:
        """Execute a validated state transition on a workflow run."""
        current_status = run.status

        if current_status == target_status:
            return run

        if current_status in TERMINAL_WORKFLOW_STATES:
            raise InvalidWorkflowStateTransitionError(
                f"Cannot transition terminal workflow '{run.workflow_id}' "
                f"({current_status.value}) to {target_status.value}."
            )

        if not cls.can_transition(current_status, target_status):
            raise InvalidWorkflowStateTransitionError(
                f"Illegal workflow transition from {current_status.value} "
                f"to {target_status.value} for run '{run.workflow_id}'."
            )

        # Enforce business invariants
        if target_status == WorkflowStatus.COMPLETED:
            if run.failed_stage is not None:
                raise InvalidWorkflowStateTransitionError(
                    f"Cannot mark workflow '{run.workflow_id}' completed when "
                    f"stage '{run.failed_stage.value}' failed."
                )

        now = datetime.now(UTC)

        # Update lifecycle timestamps
        if target_status == WorkflowStatus.RUNNING and run.started_at is None:
            run.started_at = now
        elif target_status in TERMINAL_WORKFLOW_STATES:
            run.completed_at = now

        if target_status == WorkflowStatus.CANCELLED and reason:
            run.cancellation_reason = reason
        elif target_status == WorkflowStatus.FAILED and reason:
            run.error_summary = reason

        run.last_updated_at = now

        # Append immutable audit transition record
        transition_record = StateTransitionRecord(
            from_state=current_status,
            to_state=target_status,
            timestamp=now,
            reason=reason,
            actor=actor,
        )
        run.transition_history.append(transition_record)
        run.status = target_status

        return run


class StageStateMachine:
    """Enforces deterministic lifecycle transitions for individual stages."""

    @staticmethod
    def can_transition(from_status: StageStatus, to_status: StageStatus) -> bool:
        """Check if a transition between two stage statuses is permitted."""
        if from_status == to_status:
            return True
        return to_status in VALID_STAGE_TRANSITIONS.get(from_status, set())

    @classmethod
    def transition(
        cls,
        stage: WorkflowStage,
        target_status: StageStatus,
        error: str | None = None,
    ) -> WorkflowStage:
        """Execute a validated state transition on an individual stage."""
        current_status = stage.status

        if current_status == target_status:
            return stage

        if current_status in TERMINAL_STAGE_STATES:
            raise InvalidStageStateTransitionError(
                f"Cannot transition terminal stage '{stage.stage_id}' "
                f"({current_status.value}) to {target_status.value}."
            )

        if not cls.can_transition(current_status, target_status):
            raise InvalidStageStateTransitionError(
                f"Illegal stage transition from {current_status.value} "
                f"to {target_status.value} for stage '{stage.stage_id}'."
            )

        now = datetime.now(UTC)

        if target_status == StageStatus.RUNNING:
            if stage.started_at is None:
                stage.started_at = now
            stage.attempt_count += 1
        elif target_status in TERMINAL_STAGE_STATES:
            stage.completed_at = now
            if stage.started_at is not None:
                duration = (stage.completed_at - stage.started_at).total_seconds()
                stage.duration_seconds = max(0.0, duration)
            if error:
                stage.error = error

        stage.status = target_status
        return stage
