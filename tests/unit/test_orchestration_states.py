"""Unit tests for workflow and stage state machines and transition rules."""

import pytest

from backend.app.orchestration.exceptions import (
    InvalidStageStateTransitionError,
    InvalidWorkflowStateTransitionError,
)
from backend.app.orchestration.models import WorkflowRun, WorkflowStage
from backend.app.orchestration.states import (
    TERMINAL_STAGE_STATES,
    TERMINAL_WORKFLOW_STATES,
    StageStatus,
    StageType,
    WorkflowStatus,
)
from backend.app.orchestration.transitions import (
    StageStateMachine,
    WorkflowStateMachine,
)


def test_workflow_state_machine_can_transition() -> None:
    """Verify can_transition returns true for legal transitions and false otherwise."""
    # Identity transitions
    assert WorkflowStateMachine.can_transition(
        WorkflowStatus.CREATED, WorkflowStatus.CREATED
    )

    # Valid transitions
    assert WorkflowStateMachine.can_transition(
        WorkflowStatus.CREATED, WorkflowStatus.QUEUED
    )
    assert WorkflowStateMachine.can_transition(
        WorkflowStatus.QUEUED, WorkflowStatus.RUNNING
    )
    assert WorkflowStateMachine.can_transition(
        WorkflowStatus.RUNNING, WorkflowStatus.WAITING_FOR_APPROVAL
    )
    assert WorkflowStateMachine.can_transition(
        WorkflowStatus.WAITING_FOR_APPROVAL, WorkflowStatus.RUNNING
    )
    assert WorkflowStateMachine.can_transition(
        WorkflowStatus.RUNNING, WorkflowStatus.COMPLETED
    )
    assert WorkflowStateMachine.can_transition(
        WorkflowStatus.RUNNING, WorkflowStatus.FAILED
    )
    assert WorkflowStateMachine.can_transition(
        WorkflowStatus.RUNNING, WorkflowStatus.PAUSED
    )
    assert WorkflowStateMachine.can_transition(
        WorkflowStatus.PAUSED, WorkflowStatus.RUNNING
    )
    assert WorkflowStateMachine.can_transition(
        WorkflowStatus.RUNNING, WorkflowStatus.CANCELLING
    )
    assert WorkflowStateMachine.can_transition(
        WorkflowStatus.CANCELLING, WorkflowStatus.CANCELLED
    )
    assert WorkflowStateMachine.can_transition(
        WorkflowStatus.RUNNING, WorkflowStatus.BLOCKED
    )

    # Illegal transitions
    assert not WorkflowStateMachine.can_transition(
        WorkflowStatus.CREATED, WorkflowStatus.COMPLETED
    )
    assert not WorkflowStateMachine.can_transition(
        WorkflowStatus.COMPLETED, WorkflowStatus.RUNNING
    )
    assert not WorkflowStateMachine.can_transition(
        WorkflowStatus.FAILED, WorkflowStatus.QUEUED
    )
    assert not WorkflowStateMachine.can_transition(
        WorkflowStatus.CANCELLED, WorkflowStatus.RUNNING
    )


def test_workflow_state_machine_happy_path_transitions() -> None:
    """Verify a complete workflow lifecycle transition sequence."""
    run = WorkflowRun(
        workflow_id="wf-happy-path",
        requested_by="dev-1",
        repository_reference="swikarb69/AgentForge",
        issue_reference="101",
    )

    assert run.status == WorkflowStatus.CREATED
    assert run.started_at is None
    assert run.completed_at is None

    # CREATED -> QUEUED
    WorkflowStateMachine.transition(
        run, WorkflowStatus.QUEUED, reason="Enqueued by dispatcher"
    )
    assert run.status.value == WorkflowStatus.QUEUED.value
    assert len(run.transition_history) == 1
    assert run.transition_history[0].from_state == WorkflowStatus.CREATED
    assert run.transition_history[0].to_state == WorkflowStatus.QUEUED

    # QUEUED -> RUNNING
    WorkflowStateMachine.transition(
        run, WorkflowStatus.RUNNING, reason="Worker dequeued"
    )
    assert run.status.value == WorkflowStatus.RUNNING.value
    assert run.started_at is not None
    assert run.completed_at is None

    # RUNNING -> WAITING_FOR_APPROVAL
    WorkflowStateMachine.transition(
        run,
        WorkflowStatus.WAITING_FOR_APPROVAL,
        reason="Review report ready, awaiting human signoff",
    )
    assert run.status.value == WorkflowStatus.WAITING_FOR_APPROVAL.value

    # WAITING_FOR_APPROVAL -> RUNNING
    WorkflowStateMachine.transition(
        run,
        WorkflowStatus.RUNNING,
        reason="Human approved release",
        actor="lead_approver",
    )
    assert run.status.value == WorkflowStatus.RUNNING.value

    # RUNNING -> COMPLETED
    WorkflowStateMachine.transition(
        run,
        WorkflowStatus.COMPLETED,
        reason="Controlled release PR created successfully",
    )
    assert run.status.value == WorkflowStatus.COMPLETED.value
    assert run.is_terminal is True
    assert run.completed_at is not None
    assert len(run.transition_history) == 5


def test_workflow_state_machine_terminal_state_protection() -> None:
    """Verify that terminal workflows cannot transition or silently restart."""
    for terminal_status in TERMINAL_WORKFLOW_STATES:
        run = WorkflowRun(
            workflow_id=f"wf-terminal-{terminal_status.value}",
            status=terminal_status,
            requested_by="tester",
            repository_reference="repo",
            issue_reference="issue",
        )
        assert run.is_terminal is True

        # Attempt transition to RUNNING
        with pytest.raises(InvalidWorkflowStateTransitionError) as exc_info:
            WorkflowStateMachine.transition(run, WorkflowStatus.RUNNING)
        assert "Cannot transition terminal workflow" in str(exc_info.value)

        # Attempt transition to QUEUED
        with pytest.raises(InvalidWorkflowStateTransitionError):
            WorkflowStateMachine.transition(run, WorkflowStatus.QUEUED)


def test_workflow_cannot_complete_with_failed_stage() -> None:
    """Verify that workflow completion is blocked when a failed stage exists."""
    run = WorkflowRun(
        workflow_id="wf-failed-stage",
        status=WorkflowStatus.RUNNING,
        requested_by="dev",
        repository_reference="repo",
        issue_reference="issue",
        failed_stage=StageType.EXECUTION_VERIFICATION,
    )

    with pytest.raises(InvalidWorkflowStateTransitionError) as exc_info:
        WorkflowStateMachine.transition(run, WorkflowStatus.COMPLETED)
    assert "Cannot mark workflow" in str(exc_info.value)
    assert "execution_verification" in str(exc_info.value)


def test_workflow_cancellation_and_failure_recording() -> None:
    """Verify cancellation reason and error summary recording on transitions."""
    run = WorkflowRun(
        workflow_id="wf-cancel-test",
        status=WorkflowStatus.RUNNING,
        requested_by="dev",
        repository_reference="repo",
        issue_reference="issue",
    )

    WorkflowStateMachine.transition(
        run,
        WorkflowStatus.CANCELLING,
        reason="Operator invoked cancel",
        actor="operator",
    )
    assert run.status.value == WorkflowStatus.CANCELLING.value

    WorkflowStateMachine.transition(
        run,
        WorkflowStatus.CANCELLED,
        reason="Container cleanly halted",
    )
    assert run.status.value == WorkflowStatus.CANCELLED.value
    assert run.cancellation_reason == "Container cleanly halted"
    assert run.completed_at is not None

    # Test failure summary recording
    run_failed = WorkflowRun(
        workflow_id="wf-fail-test",
        status=WorkflowStatus.RUNNING,
        requested_by="dev",
        repository_reference="repo",
        issue_reference="issue",
    )
    WorkflowStateMachine.transition(
        run_failed,
        WorkflowStatus.FAILED,
        reason="Critical security review finding detected",
    )
    assert run_failed.status.value == WorkflowStatus.FAILED.value
    assert run_failed.error_summary == "Critical security review finding detected"


def test_stage_state_machine_lifecycle() -> None:
    """Verify StageStateMachine transitions, duration calculation, and errors."""
    stage = WorkflowStage(stage_type=StageType.CODE_GENERATION)
    assert stage.status.value == StageStatus.PENDING.value
    assert stage.attempt_count == 0

    # PENDING -> RUNNING
    StageStateMachine.transition(stage, StageStatus.RUNNING)
    assert stage.status.value == StageStatus.RUNNING.value
    assert stage.started_at is not None
    assert stage.attempt_count == 1

    # RUNNING -> COMPLETED
    StageStateMachine.transition(stage, StageStatus.COMPLETED)
    assert stage.status.value == StageStatus.COMPLETED.value
    assert stage.completed_at is not None
    assert stage.duration_seconds >= 0.0

    # Terminal stage cannot transition again
    with pytest.raises(InvalidStageStateTransitionError) as exc_info:
        StageStateMachine.transition(stage, StageStatus.RUNNING)
    assert "Cannot transition terminal stage" in str(exc_info.value)


def test_stage_state_machine_failure_and_skip() -> None:
    """Verify stage failure records error and stage skip transitions cleanly."""
    stage_fail = WorkflowStage(stage_type=StageType.EXECUTION_VERIFICATION)
    StageStateMachine.transition(stage_fail, StageStatus.RUNNING)
    StageStateMachine.transition(
        stage_fail, StageStatus.FAILED, error="Docker sandbox timeout"
    )
    assert stage_fail.status == StageStatus.FAILED
    assert stage_fail.error == "Docker sandbox timeout"

    stage_skip = WorkflowStage(stage_type=StageType.AUTOMATIC_REPAIR)
    StageStateMachine.transition(stage_skip, StageStatus.SKIPPED)
    assert stage_skip.status == StageStatus.SKIPPED
    assert stage_skip.status in TERMINAL_STAGE_STATES
