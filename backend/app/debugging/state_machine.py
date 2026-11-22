"""State machine governing bounded transitions in the automated repair loop."""

from datetime import UTC, datetime

from backend.app.debugging.models import RepairState, StateTransition


class InvalidStateTransitionError(ValueError):
    """Raised when an invalid state transition is requested."""

    def __init__(
        self, from_state: RepairState, to_state: RepairState, reason: str = ""
    ) -> None:
        self.from_state = from_state
        self.to_state = to_state
        self.reason = reason
        message = (
            f"Invalid state transition from '{from_state.value}' to '{to_state.value}'."
        )
        if reason:
            message += f" Reason: {reason}"
        super().__init__(message)


VALID_TRANSITIONS: dict[RepairState, set[RepairState]] = {
    RepairState.PENDING: {
        RepairState.ANALYZING,
        RepairState.CANCELLED,
    },
    RepairState.ANALYZING: {
        RepairState.DIAGNOSING,
        RepairState.FAILED,
        RepairState.NEEDS_HUMAN_REVIEW,
        RepairState.CANCELLED,
    },
    RepairState.DIAGNOSING: {
        RepairState.AWAITING_APPROVAL,
        RepairState.GENERATING_REPAIR,
        RepairState.NEEDS_HUMAN_REVIEW,
        RepairState.FAILED,
        RepairState.CANCELLED,
    },
    RepairState.AWAITING_APPROVAL: {
        RepairState.GENERATING_REPAIR,
        RepairState.NEEDS_HUMAN_REVIEW,
        RepairState.CANCELLED,
    },
    RepairState.GENERATING_REPAIR: {
        RepairState.VALIDATING_REPAIR,
        RepairState.FAILED,
        RepairState.NEEDS_HUMAN_REVIEW,
        RepairState.CANCELLED,
    },
    RepairState.VALIDATING_REPAIR: {
        RepairState.EXECUTING_REPAIR,
        RepairState.FAILED,
        RepairState.NEEDS_HUMAN_REVIEW,
        RepairState.CANCELLED,
    },
    RepairState.EXECUTING_REPAIR: {
        RepairState.EVALUATING,
        RepairState.FAILED,
        RepairState.CANCELLED,
    },
    RepairState.EVALUATING: {
        RepairState.SUCCEEDED,
        RepairState.PARTIALLY_SUCCEEDED,
        RepairState.ANALYZING,
        RepairState.MAX_ATTEMPTS_REACHED,
        RepairState.NEEDS_HUMAN_REVIEW,
        RepairState.FAILED,
        RepairState.CANCELLED,
    },
    RepairState.SUCCEEDED: set(),
    RepairState.PARTIALLY_SUCCEEDED: set(),
    RepairState.FAILED: set(),
    RepairState.NEEDS_HUMAN_REVIEW: set(),
    RepairState.CANCELLED: set(),
    RepairState.MAX_ATTEMPTS_REACHED: set(),
}

TERMINAL_STATES: set[RepairState] = {
    RepairState.SUCCEEDED,
    RepairState.PARTIALLY_SUCCEEDED,
    RepairState.FAILED,
    RepairState.NEEDS_HUMAN_REVIEW,
    RepairState.CANCELLED,
    RepairState.MAX_ATTEMPTS_REACHED,
}


class RepairStateMachine:
    """Manages explicit, validated state transitions for automated repair runs."""

    def __init__(self, initial_state: RepairState = RepairState.PENDING) -> None:
        self._current_state: RepairState = initial_state
        self._history: list[StateTransition] = []

    @property
    def current_state(self) -> RepairState:
        """Get the current state."""
        return self._current_state

    @property
    def history(self) -> list[StateTransition]:
        """Get chronological record of state transitions."""
        return list(self._history)

    def is_terminal(self) -> bool:
        """Return True if current state is a terminal state."""
        return self._current_state in TERMINAL_STATES

    def can_transition_to(self, target_state: RepairState) -> bool:
        """Check whether transitioning to target_state is permitted."""
        allowed = VALID_TRANSITIONS.get(self._current_state, set())
        return target_state in allowed

    def transition_to(
        self, target_state: RepairState, reason: str = ""
    ) -> StateTransition:
        """Transition machine to target_state if permitted, recording history."""
        if not self.can_transition_to(target_state):
            raise InvalidStateTransitionError(
                from_state=self._current_state,
                to_state=target_state,
                reason=reason,
            )

        transition = StateTransition(
            from_state=self._current_state,
            to_state=target_state,
            timestamp=datetime.now(UTC).isoformat(),
            reason=reason,
        )
        self._current_state = target_state
        self._history.append(transition)
        return transition
