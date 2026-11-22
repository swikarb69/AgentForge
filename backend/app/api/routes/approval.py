"""FastAPI REST routes for Human Approval & Controlled Release engine."""

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from backend.app.approval.audit import AuditEventRecord
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
from backend.app.approval.models import (
    ApprovalPolicy,
    ApprovalRequest,
    ApprovalRiskLevel,
    ArtifactBinding,
    DecisionOutcome,
)
from backend.app.approval.readiness import ReleaseReadinessReport
from backend.app.approval.release_models import (
    ReleaseExecutionRecord,
    ReleaseStatus,
)
from backend.app.approval.service import ApprovalService

router = APIRouter(prefix="/api/v1/approval", tags=["Approval & Release"])
approval_service = ApprovalService()


# ============================================================================
# Request / Response Schemas
# ============================================================================


class CreateApprovalRequestPayload(BaseModel):
    """Payload to initiate a human approval request."""

    repository_id: str = Field(description="Target repository identifier or path")
    release_candidate_id: str = Field(description="Unique candidate identifier")
    artifact_binding: ArtifactBinding = Field(
        description="Bound artifact cryptographic hashes"
    )
    actor_token: str = Field(
        default="mock_token_admin",
        description="Authentication token or actor ID of requester",
    )
    policy: ApprovalPolicy | None = Field(
        default=None, description="Optional custom approval policy"
    )
    risk_level: ApprovalRiskLevel | None = Field(
        default=None, description="Optional assessed risk level"
    )
    issue_id: str | None = Field(default=None, description="Linked issue ID")
    plan_id: str | None = Field(default=None, description="Linked plan ID")
    generation_id: str | None = Field(default=None, description="Linked generation ID")
    execution_id: str | None = Field(default=None, description="Linked execution ID")
    review_id: str | None = Field(default=None, description="Linked review ID")
    repair_id: str | None = Field(default=None, description="Linked repair ID")
    blocking_reasons: list[str] = Field(
        default_factory=list, description="Initial known blocker descriptions"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Supplementary request metadata"
    )


class DecisionPayload(BaseModel):
    """Payload to submit a human approval decision."""

    actor_token: str = Field(description="Authentication token or actor ID of approver")
    outcome: DecisionOutcome = Field(description="Decision outcome")
    comments: str | None = Field(
        default=None, description="Optional reviewer remarks or rationale"
    )
    current_patch_hash: str | None = Field(
        default=None, description="Observed patch hash for validation"
    )
    current_review_hash: str | None = Field(
        default=None, description="Observed review hash for validation"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Supplementary decision metadata"
    )


class RevocationPayload(BaseModel):
    """Payload to revoke a previously granted approval."""

    actor_token: str = Field(
        description="Authentication token or actor ID of revoking actor"
    )
    reason: str = Field(description="Detailed explanation for revocation")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Supplementary metadata"
    )


class ReleasePayload(BaseModel):
    """Payload to trigger a controlled release operation."""

    actor_token: str = Field(
        description="Authentication token or actor ID of release operator"
    )
    dry_run: bool = Field(
        default=False, description="Simulate release without upstream PR creation"
    )
    patch_content: str | None = Field(
        default=None, description="Optional raw patch contents to apply"
    )
    commit_message: str | None = Field(default=None, description="Git commit message")
    custom_branch_name: str | None = Field(
        default=None, description="Explicit release candidate branch name"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Supplementary release metadata"
    )


class AuditIntegrityResponse(BaseModel):
    """Response verifying cryptographic hash-chain integrity of audit log."""

    approval_id: str = Field(description="Approval request identifier")
    is_valid: bool = Field(description="True if cryptographic hash chain is intact")
    violations: list[str] = Field(
        default_factory=list, description="Integrity violation descriptions"
    )
    total_events: int = Field(description="Total recorded events in chain")


# ============================================================================
# Error Mapping Helper
# ============================================================================


def handle_approval_exception(err: Exception) -> None:
    """Map domain exceptions to corresponding FastAPI HTTP status codes."""
    if isinstance(err, ApprovalNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(err))
    if isinstance(
        err,
        (
            SeparationOfDutiesError,
            UnauthorizedApproverError,
            IdentityVerificationError,
            RepositoryPermissionError,
            ReleaseAuthorizationError,
        ),
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(err))
    if isinstance(
        err,
        (
            ApprovalExpiredError,
            ApprovalRevokedError,
            InvalidStateTransitionError,
            ArtifactMismatchError,
            ConflictingDecisionError,
            ReleaseNotReadyError,
            ApprovalPolicyViolationError,
            ApprovalValidationError,
        ),
    ):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err))
    if isinstance(err, ReleaseProviderError):
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(err))
    if isinstance(err, (ApprovalError, ReleaseError)):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err))
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(err)
    )


# ============================================================================
# API Endpoints
# ============================================================================


@router.post(
    "/requests",
    response_model=ApprovalRequest,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new human approval request",
)
async def create_approval_request(
    payload: CreateApprovalRequestPayload,
) -> ApprovalRequest:
    """Register a new release candidate for human authorization and review."""
    try:
        actor = await approval_service.authenticate_actor(payload.actor_token)
        return approval_service.create_approval_request(
            actor=actor,
            repository_id=payload.repository_id,
            release_candidate_id=payload.release_candidate_id,
            artifact_binding=payload.artifact_binding,
            policy=payload.policy,
            risk_level=payload.risk_level,
            issue_id=payload.issue_id,
            plan_id=payload.plan_id,
            generation_id=payload.generation_id,
            execution_id=payload.execution_id,
            review_id=payload.review_id,
            repair_id=payload.repair_id,
            blocking_reasons=payload.blocking_reasons,
            metadata=payload.metadata,
        )
    except Exception as err:
        handle_approval_exception(err)
        raise  # unreachable, satisfies type checker


@router.get(
    "/requests",
    response_model=list[ApprovalRequest],
    summary="List approval requests matching criteria",
)
def list_approval_requests(
    repository_id: Annotated[str | None, Query()] = None,
    candidate_id: Annotated[str | None, Query()] = None,
    request_status: Annotated[str | None, Query(alias="status")] = None,
) -> list[ApprovalRequest]:
    """List active approval requests filtered by repository, candidate, or status."""
    try:
        return approval_service.list_approval_requests(
            repository_id=repository_id,
            candidate_id=candidate_id,
            status_filter=request_status,
        )
    except Exception as err:
        handle_approval_exception(err)
        raise


@router.get(
    "/requests/{approval_id}",
    response_model=ApprovalRequest,
    summary="Get approval request details and on-read expiration status",
)
def get_approval_request(approval_id: str) -> ApprovalRequest:
    """Retrieve approval request by ID with automatic expiration evaluation."""
    try:
        return approval_service.get_approval_request(approval_id)
    except Exception as err:
        handle_approval_exception(err)
        raise


@router.post(
    "/requests/{approval_id}/decide",
    response_model=ApprovalRequest,
    summary="Submit an authorized human approval or rejection decision",
)
async def submit_decision(
    approval_id: str,
    payload: DecisionPayload,
) -> ApprovalRequest:
    """Record an immutable, cryptographically-bound decision by a human reviewer."""
    try:
        actor = await approval_service.authenticate_actor(payload.actor_token)
        return approval_service.submit_decision(
            actor=actor,
            approval_id=approval_id,
            outcome=payload.outcome,
            comments=payload.comments,
            current_patch_hash=payload.current_patch_hash,
            current_review_hash=payload.current_review_hash,
            metadata=payload.metadata,
        )
    except Exception as err:
        handle_approval_exception(err)
        raise


@router.post(
    "/requests/{approval_id}/revoke",
    response_model=ApprovalRequest,
    summary="Revoke previously granted approval",
)
async def revoke_approval(
    approval_id: str,
    payload: RevocationPayload,
) -> ApprovalRequest:
    """Revoke approval status due to new findings, test failures, or risks."""
    try:
        actor = await approval_service.authenticate_actor(payload.actor_token)
        return approval_service.revoke_approval(
            actor=actor,
            approval_id=approval_id,
            reason=payload.reason,
        )
    except Exception as err:
        handle_approval_exception(err)
        raise


@router.get(
    "/requests/{approval_id}/readiness",
    response_model=ReleaseReadinessReport,
    summary="Evaluate full release readiness criteria across all 13 checks",
)
def evaluate_readiness(approval_id: str) -> ReleaseReadinessReport:
    """Inspect release candidate readiness before triggering shipping."""
    try:
        return approval_service.evaluate_readiness(approval_id)
    except Exception as err:
        handle_approval_exception(err)
        raise


@router.post(
    "/requests/{approval_id}/release",
    response_model=ReleaseExecutionRecord,
    status_code=status.HTTP_201_CREATED,
    summary="Execute controlled release operation creating release branch and PR",
)
async def execute_release(
    approval_id: str,
    payload: ReleasePayload,
) -> ReleaseExecutionRecord:
    """Authorize and execute release operation. Never merges to default branch."""
    try:
        actor = await approval_service.authenticate_actor(payload.actor_token)
        return await approval_service.execute_release(
            actor=actor,
            approval_id=approval_id,
            dry_run=payload.dry_run,
            patch_content=payload.patch_content,
            commit_message=payload.commit_message,
            custom_branch_name=payload.custom_branch_name,
            metadata=payload.metadata,
        )
    except Exception as err:
        handle_approval_exception(err)
        raise


@router.get(
    "/requests/{approval_id}/audit",
    response_model=list[AuditEventRecord],
    summary="Retrieve chronological audit log for approval request",
)
def get_audit_trail(approval_id: str) -> list[AuditEventRecord]:
    """Retrieve tamper-evident audit history for compliance and verification."""
    try:
        # Verify request exists first
        approval_service.get_approval_request(approval_id)
        return approval_service.get_audit_trail(approval_id)
    except Exception as err:
        handle_approval_exception(err)
        raise


@router.get(
    "/requests/{approval_id}/audit/verify",
    response_model=AuditIntegrityResponse,
    summary="Verify cryptographic hash-chain integrity of audit log",
)
def verify_audit_integrity(approval_id: str) -> AuditIntegrityResponse:
    """Verify SHA-256 hash chaining to detect any tampering or deletion."""
    try:
        approval_service.get_approval_request(approval_id)
        is_valid, violations = approval_service.verify_audit_integrity(approval_id)
        events = approval_service.get_audit_trail(approval_id)
        return AuditIntegrityResponse(
            approval_id=approval_id,
            is_valid=is_valid,
            violations=violations,
            total_events=len(events),
        )
    except Exception as err:
        handle_approval_exception(err)
        raise


@router.get(
    "/releases/{release_id}",
    response_model=ReleaseExecutionRecord,
    summary="Retrieve details of a controlled release operation",
)
def get_release(release_id: str) -> ReleaseExecutionRecord:
    """Look up release execution record by ID."""
    record = approval_service.get_release(release_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Release operation '{release_id}' not found",
        )
    return record


@router.get(
    "/releases",
    response_model=list[ReleaseExecutionRecord],
    summary="List controlled release operations matching filter criteria",
)
def list_releases(
    repository_id: Annotated[str | None, Query()] = None,
    release_status: Annotated[ReleaseStatus | None, Query(alias="status")] = None,
) -> list[ReleaseExecutionRecord]:
    """List recorded release operations filtered by repository or execution status."""
    return approval_service.list_releases(
        repository_id=repository_id, status=release_status
    )
