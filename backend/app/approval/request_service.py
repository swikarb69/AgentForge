"""Service for creating, querying, and managing approval requests."""

from typing import Any
from uuid import uuid4

from backend.app.approval.audit import AuditTrailStore
from backend.app.approval.authorization import AuthorizationEngine
from backend.app.approval.exceptions import ApprovalNotFoundError
from backend.app.approval.expiration import ExpirationManager
from backend.app.approval.identity import UserIdentity
from backend.app.approval.models import (
    ApprovalPolicy,
    ApprovalRequest,
    ApprovalRiskLevel,
    ApprovalStatus,
    ArtifactBinding,
)
from backend.app.approval.policies import policy_for_risk_level
from backend.app.approval.state_machine import ApprovalStateMachine


class ApprovalRequestService:
    """Manages the creation, retrieval, and lifecycle queries of approval requests."""

    def __init__(
        self,
        auth_engine: AuthorizationEngine | None = None,
        audit_store: AuditTrailStore | None = None,
        state_machine: ApprovalStateMachine | None = None,
        expiration_manager: ExpirationManager | None = None,
    ) -> None:
        self.auth_engine = auth_engine or AuthorizationEngine()
        self.audit_store = audit_store or AuditTrailStore()
        self.state_machine = state_machine or ApprovalStateMachine()
        self.expiration_manager = expiration_manager or ExpirationManager(
            state_machine=self.state_machine, audit_store=self.audit_store
        )
        self._requests: dict[str, ApprovalRequest] = {}

    def create_request(
        self,
        actor: UserIdentity,
        repository_id: str,
        release_candidate_id: str,
        artifact_binding: ArtifactBinding,
        policy: ApprovalPolicy | None = None,
        risk_level: ApprovalRiskLevel | None = None,
        issue_id: str | None = None,
        plan_id: str | None = None,
        generation_id: str | None = None,
        execution_id: str | None = None,
        repair_id: str | None = None,
        review_id: str | None = None,
        blocking_reasons: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ApprovalRequest:
        """Create and persist an approval request following authorization validation."""
        # 1. Enforce authorization to request approval
        self.auth_engine.enforce_can_request_approval(actor, repository_id)

        # 2. Derive risk tier and policy
        active_blockers = list(blocking_reasons or [])
        effective_risk = risk_level or (
            ApprovalRiskLevel.CRITICAL if active_blockers else ApprovalRiskLevel.LOW
        )
        effective_policy = policy or policy_for_risk_level(effective_risk)

        # 3. Determine initial status
        initial_status = ApprovalStatus.PENDING
        if active_blockers and effective_policy.block_on_critical_findings:
            initial_status = ApprovalStatus.BLOCKED

        approval_id = f"appr_{uuid4().hex[:12]}"
        request = ApprovalRequest(
            approval_id=approval_id,
            repository_id=repository_id,
            release_candidate_id=release_candidate_id,
            issue_id=issue_id,
            plan_id=plan_id,
            generation_id=generation_id,
            execution_id=execution_id,
            repair_id=repair_id,
            review_id=review_id,
            requested_by=actor.actor_id,
            required_approvers=effective_policy.minimum_approvers,
            approval_policy=effective_policy,
            risk_level=effective_risk,
            status=initial_status,
            artifact_binding=artifact_binding,
            blocking_reasons=active_blockers,
            metadata=metadata or {},
        )

        # 4. Record genesis audit event
        self.audit_store.append_event(
            approval_id=approval_id,
            actor_id=actor.actor_id,
            action="approval_requested",
            previous_state=None,
            new_state=initial_status.value,
            reason=f"Approval request created at risk tier '{effective_risk.value}'",
            artifact_hashes={
                "patch_hash": artifact_binding.patch_hash,
                "review_hash": artifact_binding.review_hash,
                "plan_hash": artifact_binding.plan_hash,
                "verification_hash": artifact_binding.verification_hash,
            },
            correlation_id=release_candidate_id,
            metadata={
                "repository_id": repository_id,
                "policy_id": effective_policy.policy_id,
                "required_approvers": effective_policy.minimum_approvers,
            },
        )

        # 5. Persist
        self._requests[approval_id] = request
        return request

    def get_request(self, approval_id: str) -> ApprovalRequest:
        """Fetch request by ID, performing on-read expiration check."""
        request = self._requests.get(approval_id)
        if not request:
            raise ApprovalNotFoundError(
                f"Approval request '{approval_id}' was not found."
            )

        # Check and apply expiration if deadline has passed
        self.expiration_manager.check_and_expire(request)
        return request

    def list_requests(
        self,
        repository_id: str | None = None,
        status: ApprovalStatus | None = None,
        requested_by: str | None = None,
    ) -> list[ApprovalRequest]:
        """List requests matching optional filter criteria."""
        results: list[ApprovalRequest] = []
        for req in self._requests.values():
            self.expiration_manager.check_and_expire(req)
            if repository_id and req.repository_id != repository_id:
                continue
            if status and req.status != status:
                continue
            if requested_by and req.requested_by != requested_by:
                continue
            results.append(req)
        return results
