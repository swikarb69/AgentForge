"""Unit tests for RepairStateMachine transitions and invariants."""

import pytest

from backend.app.debugging.models import RepairState
from backend.app.debugging.state_machine import (
    InvalidStateTransitionError,
    RepairStateMachine,
)


def test_initial_state_and_history() -> None:
    """Verify initial state is PENDING and history starts empty."""
    sm = RepairStateMachine()
    assert sm.current_state == RepairState.PENDING
    assert sm.history == []
    assert not sm.is_terminal()


def test_happy_path_transitions() -> None:
    """Verify full linear happy-path transition sequence to SUCCEEDED."""
    sm = RepairStateMachine()

    t1 = sm.transition_to(RepairState.ANALYZING, reason="Start log analysis")
    assert t1.from_state == RepairState.PENDING
    assert t1.to_state == RepairState.ANALYZING
    assert sm.current_state == RepairState.ANALYZING

    sm.transition_to(RepairState.DIAGNOSING, reason="Evidence gathered")
    sm.transition_to(RepairState.GENERATING_REPAIR, reason="Strategy selected")
    sm.transition_to(RepairState.VALIDATING_REPAIR, reason="Proposal generated")
    sm.transition_to(RepairState.EXECUTING_REPAIR, reason="Patch validated")
    sm.transition_to(RepairState.EVALUATING, reason="Sandbox run completed")
    t_end = sm.transition_to(RepairState.SUCCEEDED, reason="All tests passed")

    assert t_end.to_state == RepairState.SUCCEEDED
    assert sm.is_terminal()
    assert len(sm.history) == 7


def test_approval_branching() -> None:
    """Verify branching through AWAITING_APPROVAL."""
    sm = RepairStateMachine()
    sm.transition_to(RepairState.ANALYZING)
    sm.transition_to(RepairState.DIAGNOSING)
    sm.transition_to(
        RepairState.AWAITING_APPROVAL, reason="High-risk strategy requires approval"
    )
    assert sm.current_state == RepairState.AWAITING_APPROVAL

    sm.transition_to(RepairState.GENERATING_REPAIR, reason="Human approved")
    assert sm.current_state.value == RepairState.GENERATING_REPAIR.value


def test_multi_attempt_retry_loop() -> None:
    """Verify retry loop: EVALUATING -> ANALYZING -> ..."""
    sm = RepairStateMachine()
    sm.transition_to(RepairState.ANALYZING)
    sm.transition_to(RepairState.DIAGNOSING)
    sm.transition_to(RepairState.GENERATING_REPAIR)
    sm.transition_to(RepairState.VALIDATING_REPAIR)
    sm.transition_to(RepairState.EXECUTING_REPAIR)
    sm.transition_to(RepairState.EVALUATING)

    # First attempt failed, retry attempt 2
    sm.transition_to(RepairState.ANALYZING, reason="Retry attempt 2")
    assert sm.current_state.value == RepairState.ANALYZING.value
    assert not sm.is_terminal()

    # Second attempt completes and reaches max attempts
    sm.transition_to(RepairState.DIAGNOSING)
    sm.transition_to(RepairState.GENERATING_REPAIR)
    sm.transition_to(RepairState.VALIDATING_REPAIR)
    sm.transition_to(RepairState.EXECUTING_REPAIR)
    sm.transition_to(RepairState.EVALUATING)
    sm.transition_to(RepairState.MAX_ATTEMPTS_REACHED, reason="Attempt limit reached")
    assert sm.current_state.value == RepairState.MAX_ATTEMPTS_REACHED.value
    assert sm.is_terminal()


def test_human_review_branch() -> None:
    """Verify transition to NEEDS_HUMAN_REVIEW from diagnostic or evaluation."""
    sm = RepairStateMachine()
    sm.transition_to(RepairState.ANALYZING)
    sm.transition_to(RepairState.DIAGNOSING)
    sm.transition_to(
        RepairState.NEEDS_HUMAN_REVIEW,
        reason="Root cause requires architectural design",
    )
    assert sm.current_state == RepairState.NEEDS_HUMAN_REVIEW
    assert sm.is_terminal()


def test_cancellation() -> None:
    """Verify early cancellation from PENDING or ANALYZING."""
    sm = RepairStateMachine()
    sm.transition_to(RepairState.CANCELLED, reason="User cancelled session")
    assert sm.current_state == RepairState.CANCELLED
    assert sm.is_terminal()


def test_invalid_transitions_raise() -> None:
    """Verify invalid state transitions raise InvalidStateTransitionError."""
    sm = RepairStateMachine()

    # Cannot skip directly from PENDING to EXECUTING_REPAIR
    assert not sm.can_transition_to(RepairState.EXECUTING_REPAIR)
    with pytest.raises(InvalidStateTransitionError) as exc_info:
        sm.transition_to(RepairState.EXECUTING_REPAIR)
    assert "Invalid state transition" in str(exc_info.value)
    assert "pending" in str(exc_info.value)
    assert "executing_repair" in str(exc_info.value)

    # Move to analyzing
    sm.transition_to(RepairState.ANALYZING)

    # Cannot jump to SUCCEEDED from ANALYZING
    assert not sm.can_transition_to(RepairState.SUCCEEDED)
    with pytest.raises(InvalidStateTransitionError):
        sm.transition_to(RepairState.SUCCEEDED)


def test_terminal_states_cannot_transition_out() -> None:
    """Verify no transitions are permitted once a terminal state is reached."""
    terminal_states = [
        RepairState.SUCCEEDED,
        RepairState.PARTIALLY_SUCCEEDED,
        RepairState.FAILED,
        RepairState.NEEDS_HUMAN_REVIEW,
        RepairState.CANCELLED,
        RepairState.MAX_ATTEMPTS_REACHED,
    ]
    for term in terminal_states:
        sm = RepairStateMachine(initial_state=term)
        assert sm.is_terminal()
        assert not sm.can_transition_to(RepairState.ANALYZING)
        with pytest.raises(InvalidStateTransitionError):
            sm.transition_to(RepairState.ANALYZING)
