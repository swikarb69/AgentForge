"""Approval subsystem for AgentForge human approval and controlled release."""

from backend.app.approval.audit import (
    GENESIS_HASH,
    AuditEventRecord,
    AuditTrailStore,
    compute_event_hash,
)
from backend.app.approval.authorization import (
    AuthorizationDecision,
    AuthorizationEngine,
)
from backend.app.approval.decision_service import ApprovalDecisionService
from backend.app.approval.exceptions import (
    ApprovalError,
    ApprovalExpiredError,
    ApprovalNotFoundError,
    ApprovalPolicyViolationError,
    ApprovalRevokedError,
    ApprovalValidationError,
    ArtifactMismatchError,
    ConflictingDecisionError,
    IdentityVerificationError,
    InvalidStateTransitionError,
    ReleaseAuthorizationError,
    ReleaseError,
    ReleaseNotReadyError,
    ReleaseProviderError,
    RepositoryPermissionError,
    SeparationOfDutiesError,
    UnauthorizedApproverError,
)
from backend.app.approval.expiration import ExpirationManager
from backend.app.approval.identity import (
    PERMISSION_HIERARCHY,
    IdentityProvider,
    LocalTestIdentityProvider,
    ProductionIdentityProvider,
    RepositoryPermission,
    UserIdentity,
)
from backend.app.approval.models import (
    ApprovalAuditEvent,
    ApprovalDecision,
    ApprovalPolicy,
    ApprovalRequest,
    ApprovalRiskLevel,
    ApprovalRole,
    ApprovalStatus,
    ArtifactBinding,
    DecisionOutcome,
)
from backend.app.approval.policies import (
    ApprovalPolicyEvaluator,
    PolicyEvaluationResult,
    evaluate_risk_level_from_review,
    get_critical_policy,
    get_default_policy,
    get_high_risk_policy,
    get_low_risk_policy,
    policy_for_risk_level,
)
from backend.app.approval.readiness import (
    ReadinessCheck,
    ReadinessStatus,
    ReleaseReadinessEngine,
    ReleaseReadinessReport,
)
from backend.app.approval.release_models import (
    PullRequestMetadata,
    ReleaseExecutionRecord,
    ReleaseStatus,
)
from backend.app.approval.release_provider import (
    GitHubReleaseProvider,
    MockReleaseProvider,
    ReleaseProvider,
)
from backend.app.approval.release_service import ControlledReleaseService
from backend.app.approval.request_service import ApprovalRequestService
from backend.app.approval.service import ApprovalService
from backend.app.approval.state_machine import ApprovalStateMachine

__all__ = [
    "ApprovalAuditEvent",
    "ApprovalDecision",
    "ApprovalDecisionService",
    "ApprovalError",
    "ApprovalExpiredError",
    "ApprovalNotFoundError",
    "ApprovalPolicy",
    "ApprovalPolicyEvaluator",
    "ApprovalPolicyViolationError",
    "ApprovalRequest",
    "ApprovalRequestService",
    "ApprovalRevokedError",
    "ApprovalRiskLevel",
    "ApprovalRole",
    "ApprovalService",
    "ApprovalStateMachine",
    "ApprovalStatus",
    "ApprovalValidationError",
    "ArtifactBinding",
    "ArtifactMismatchError",
    "AuditEventRecord",
    "AuditTrailStore",
    "AuthorizationDecision",
    "AuthorizationEngine",
    "ConflictingDecisionError",
    "ControlledReleaseService",
    "DecisionOutcome",
    "ExpirationManager",
    "GENESIS_HASH",
    "GitHubReleaseProvider",
    "IdentityProvider",
    "IdentityVerificationError",
    "InvalidStateTransitionError",
    "LocalTestIdentityProvider",
    "MockReleaseProvider",
    "PERMISSION_HIERARCHY",
    "PolicyEvaluationResult",
    "ProductionIdentityProvider",
    "PullRequestMetadata",
    "ReadinessCheck",
    "ReadinessStatus",
    "ReleaseAuthorizationError",
    "ReleaseError",
    "ReleaseExecutionRecord",
    "ReleaseNotReadyError",
    "ReleaseProvider",
    "ReleaseProviderError",
    "ReleaseReadinessEngine",
    "ReleaseReadinessReport",
    "ReleaseStatus",
    "RepositoryPermission",
    "RepositoryPermissionError",
    "SeparationOfDutiesError",
    "UnauthorizedApproverError",
    "UserIdentity",
    "compute_event_hash",
    "evaluate_risk_level_from_review",
    "get_critical_policy",
    "get_default_policy",
    "get_high_risk_policy",
    "get_low_risk_policy",
    "policy_for_risk_level",
]
