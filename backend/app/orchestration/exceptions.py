"""Exceptions for the AgentForge central orchestration engine."""


class OrchestrationError(Exception):
    """Base exception for all workflow orchestration errors."""


class InvalidWorkflowStateTransitionError(OrchestrationError):
    """Raised when an illegal workflow state transition is attempted."""


class InvalidStageStateTransitionError(OrchestrationError):
    """Raised when an illegal stage state transition is attempted."""


class WorkflowNotFoundError(OrchestrationError):
    """Raised when a requested workflow run cannot be found."""


class WorkflowExecutionError(OrchestrationError):
    """Raised when workflow execution encounters an unrecoverable failure."""


class StageExecutionError(OrchestrationError):
    """Raised when an individual stage execution fails."""


class WorkflowValidationError(OrchestrationError):
    """Raised when workflow input or context fails validation."""


class WorkflowCancelledError(OrchestrationError):
    """Raised when execution is halted due to user or policy cancellation."""


class WorkflowPausedError(OrchestrationError):
    """Raised when execution cannot proceed because workflow is paused."""


class WorkflowBlockedError(OrchestrationError):
    """Raised when workflow execution is blocked by policies or review findings."""


class ApprovalRequiredError(OrchestrationError):
    """Raised when release execution is attempted without required approval."""


class ReleaseNotAuthorizedError(OrchestrationError):
    """Raised when release authorization check fails."""


class ConcurrencyConflictError(OrchestrationError):
    """Raised when concurrent operations conflict on the same workflow run."""


class WorkflowArtifactIntegrityError(OrchestrationError):
    """Raised when artifact integrity verification fails during recovery or resume."""


class IdempotencyConflictError(ConcurrencyConflictError):
    """Raised when duplicate workflow execution is attempted with an existing key."""
