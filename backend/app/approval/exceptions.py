"""Exceptions for the Human Approval & Controlled Release subsystem."""


class ApprovalError(Exception):
    """Base exception for all approval-related errors."""


class ApprovalNotFoundError(ApprovalError):
    """Raised when an approval request cannot be found."""


class ApprovalValidationError(ApprovalError):
    """Raised when approval request data or decision fails validation."""


class InvalidStateTransitionError(ApprovalError):
    """Raised when an illegal approval status transition is attempted."""


class ApprovalExpiredError(ApprovalError):
    """Raised when an action is attempted on an expired approval request."""


class ApprovalRevokedError(ApprovalError):
    """Raised when an action is attempted on a revoked approval request."""


class SeparationOfDutiesError(ApprovalError):
    """Raised when a requester attempts to approve their own request."""


class ApprovalPolicyViolationError(ApprovalError):
    """Raised when an approval decision or release violates configured policy."""


class ArtifactMismatchError(ApprovalError):
    """Raised when bound artifact hashes (patch, review, plan) do not match."""


class UnauthorizedApproverError(ApprovalError):
    """Raised when an actor lacks the required role or permission to approve."""


class IdentityVerificationError(ApprovalError):
    """Raised when actor credentials or identity cannot be authenticated."""


class RepositoryPermissionError(ApprovalError):
    """Raised when actor lacks required repository-level permissions."""


class ConflictingDecisionError(ApprovalError):
    """Raised when an actor submits duplicate or conflicting decisions."""


class ReleaseError(ApprovalError):
    """Base exception for all controlled release errors."""


class ReleaseAuthorizationError(ReleaseError):
    """Raised when an actor lacks authorization to trigger release operations."""


class ReleaseNotReadyError(ReleaseError):
    """Raised when release candidate fails readiness criteria checks."""


class ReleaseProviderError(ReleaseError):
    """Raised when an external release provider encounters an operational failure."""
