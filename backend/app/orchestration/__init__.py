"""Central orchestration engine coordinating AgentForge engineering workflows."""

from backend.app.orchestration.adapters import (
    AutomaticRepairAdapter,
    CodeGenerationAdapter,
    CodeReviewAdapter,
    ControlledReleaseAdapter,
    ExecutionVerificationAdapter,
    HumanApprovalAdapter,
    ImplementationPlanningAdapter,
    IssueAnalysisAdapter,
    RepositoryAnalysisAdapter,
)
from backend.app.orchestration.context import (
    WorkflowArtifactReferences,
    WorkflowContext,
)
from backend.app.orchestration.exceptions import (
    ApprovalRequiredError,
    ConcurrencyConflictError,
    IdempotencyConflictError,
    InvalidStageStateTransitionError,
    InvalidWorkflowStateTransitionError,
    OrchestrationError,
    ReleaseNotAuthorizedError,
    StageExecutionError,
    WorkflowArtifactIntegrityError,
    WorkflowBlockedError,
    WorkflowCancelledError,
    WorkflowExecutionError,
    WorkflowNotFoundError,
    WorkflowPausedError,
    WorkflowValidationError,
)
from backend.app.orchestration.executor import WorkflowExecutor
from backend.app.orchestration.models import (
    ExecutionLimits,
    StateTransitionRecord,
    WorkflowConfiguration,
    WorkflowRequest,
    WorkflowRun,
    WorkflowStage,
)
from backend.app.orchestration.recovery import (
    WorkflowRecoveryManager,
    verify_artifact_integrity,
)
from backend.app.orchestration.stage_registry import (
    StageRegistry,
    create_default_stage_registry,
)
from backend.app.orchestration.stages import (
    RetryPolicy,
    StageDefinition,
    StageInput,
    StageOutput,
    WorkflowStageAdapter,
)
from backend.app.orchestration.states import (
    TERMINAL_STAGE_STATES,
    TERMINAL_WORKFLOW_STATES,
    StageStatus,
    StageType,
    WorkflowStatus,
)
from backend.app.orchestration.storage import (
    InMemoryWorkflowStore,
    WorkflowStore,
)
from backend.app.orchestration.transitions import (
    VALID_STAGE_TRANSITIONS,
    VALID_WORKFLOW_TRANSITIONS,
    StageStateMachine,
    WorkflowStateMachine,
)

__all__ = [
    "ApprovalRequiredError",
    "AutomaticRepairAdapter",
    "CodeGenerationAdapter",
    "CodeReviewAdapter",
    "ConcurrencyConflictError",
    "ControlledReleaseAdapter",
    "ExecutionLimits",
    "ExecutionVerificationAdapter",
    "HumanApprovalAdapter",
    "IdempotencyConflictError",
    "ImplementationPlanningAdapter",
    "InMemoryWorkflowStore",
    "InvalidStageStateTransitionError",
    "InvalidWorkflowStateTransitionError",
    "IssueAnalysisAdapter",
    "OrchestrationError",
    "ReleaseNotAuthorizedError",
    "RepositoryAnalysisAdapter",
    "RetryPolicy",
    "StageDefinition",
    "StageExecutionError",
    "StageInput",
    "StageOutput",
    "StageRegistry",
    "StageStateMachine",
    "StageStatus",
    "StageType",
    "StateTransitionRecord",
    "TERMINAL_STAGE_STATES",
    "TERMINAL_WORKFLOW_STATES",
    "VALID_STAGE_TRANSITIONS",
    "VALID_WORKFLOW_TRANSITIONS",
    "WorkflowArtifactIntegrityError",
    "WorkflowArtifactReferences",
    "WorkflowBlockedError",
    "WorkflowCancelledError",
    "WorkflowConfiguration",
    "WorkflowContext",
    "WorkflowExecutionError",
    "WorkflowExecutor",
    "WorkflowNotFoundError",
    "WorkflowPausedError",
    "WorkflowRecoveryManager",
    "WorkflowRequest",
    "WorkflowRun",
    "WorkflowStage",
    "WorkflowStageAdapter",
    "WorkflowStateMachine",
    "WorkflowStatus",
    "WorkflowStore",
    "WorkflowValidationError",
    "create_default_stage_registry",
    "verify_artifact_integrity",
]
