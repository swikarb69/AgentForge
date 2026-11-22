"""Service for submitting human decisions, revoking, and invalidating approvals."""

from typing import Any
from uuid import uuid4

from backend.app.approval.audit import AuditTrailStore
from backend.app.approval.authorization import AuthorizationEngine
from backend.app.approval.exceptions import ArtifactMismatchError
from backend.app.approval.identity import UserIdentity
from backend.app.approval.models import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalRole,
    ApprovalStatus,
    DecisionOutcome,
)
from backend.app.approval.request_service import ApprovalRequestService
from backend.app.approval.state_machine import ApprovalStateMachine


class ApprovalDecisionService:
    """Coordinates submission of human decisions, revocations, and invalidations."""

    def __init__(
        self,
        request_service: ApprovalRequestService,
        auth_engine: AuthorizationEngine | None = None,
        state_machine: ApprovalStateMachine | None = None,
        audit_store: AuditTrailStore | None = None,
    ) -> None:
        self.request_service = request_service
        self.auth_engine = auth_engine or AuthorizationEngine()
        self.state_machine = state_machine or ApprovalStateMachine()
        self.audit_store = audit_store or request_service.audit_store

    def _select_actor_role(
        self, actor: UserIdentity, request: ApprovalRequest
    ) -> ApprovalRole:
        """Select eligible role for an actor submitting a decision."""
        if ApprovalRole.ADMIN in actor.roles:
            return ApprovalRole.ADMIN
        for role in request.approval_policy.required_roles:
            if role in actor.roles:
                return role
        return actor.roles[0] if actor.roles else ApprovalRole.APPROVER

    def submit_decision(
        self,
        actor: UserIdentity,
        approval_id: str,
        outcome: DecisionOutcome,
        reason: str | None = None,
        associated_patch_hash: str | None = None,
        associated_review_hash: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ApprovalRequest:
        """Submit an approval decision, evaluate policy, and log audit trail."""
        request = self.request_service.get_request(approval_id)

        # 1. Enforce authorization
        self.auth_engine.enforce_can_decide(actor, request, outcome)

        # 2. Select actor role
        effective_role = self._select_actor_role(actor, request)

        # 3. Create decision record
        decision_id = f"dec_{uuid4().hex[:12]}"
        decision = ApprovalDecision(
            decision_id=decision_id,
            approval_id=approval_id,
            actor_id=actor.actor_id,
            actor_role=effective_role,
            outcome=outcome,
            reason=reason,
            associated_patch_hash=associated_patch_hash,
            associated_review_hash=associated_review_hash,
            metadata=metadata or {},
        )

        prev_state = request.status.value

        # 4. Apply via deterministic state machine
        updated_request = self.state_machine.apply_decision(request, decision)

        # 5. Record tamper-evident audit event
        self.audit_store.append_event(
            approval_id=approval_id,
            actor_id=actor.actor_id,
            action=f"decision_{outcome.value}",
            previous_state=prev_state,
            new_state=updated_request.status.value,
            reason=reason,
            artifact_hashes={
                "patch_hash": request.artifact_binding.patch_hash,
                "review_hash": request.artifact_binding.review_hash,
                "decision_patch_hash": associated_patch_hash,
                "decision_review_hash": associated_review_hash,
            },
            correlation_id=decision_id,
            metadata={
                "actor_role": effective_role.value,
                "outcome": outcome.value,
            },
        )

        return updated_request

    def revoke_approval(
        self,
        actor: UserIdentity,
        approval_id: str,
        reason: str,
    ) -> ApprovalRequest:
        """Revoke a previously granted approval and record audit trail."""
        request = self.request_service.get_request(approval_id)

        # 1. Enforce revocation authorization
        self.auth_engine.enforce_can_revoke(actor, request)

        effective_role = (
            ApprovalRole.ADMIN
            if ApprovalRole.ADMIN in actor.roles
            else ApprovalRole.APPROVER
        )
        decision_id = f"dec_{uuid4().hex[:12]}"
        revocation_decision = ApprovalDecision(
            decision_id=decision_id,
            approval_id=approval_id,
            actor_id=actor.actor_id,
            actor_role=effective_role,
            outcome=DecisionOutcome.REVOKED,
            reason=reason,
        )

        prev_state = request.status.value
        updated_request = self.state_machine.apply_decision(
            request, revocation_decision
        )

        # Audit event
        self.audit_store.append_event(
            approval_id=approval_id,
            actor_id=actor.actor_id,
            action="approval_revoked",
            previous_state=prev_state,
            new_state=ApprovalStatus.REVOKED.value,
            reason=reason,
            artifact_hashes={
                "patch_hash": request.artifact_binding.patch_hash,
                "review_hash": request.artifact_binding.review_hash,
            },
            correlation_id=decision_id,
        )

        return updated_request

    def invalidate_on_material_change(
        self,
        approval_id: str,
        current_patch_hash: str,
        current_review_hash: str | None = None,
        current_plan_hash: str | None = None,
        reason: str | None = None,
        actor_id: str = "system",
    ) -> tuple[ApprovalRequest, bool]:
        """Check material integrity and invalidate approval if candidate has changed.

        Returns (ApprovalRequest, is_invalidated: bool).
        """
        request = self.request_service.get_request(approval_id)
        prev_state = request.status.value

        try:
            self.state_machine.validate_material_integrity(
                request=request,
                current_patch_hash=current_patch_hash,
                current_review_hash=current_review_hash,
                current_plan_hash=current_plan_hash,
            )
            return request, False
        except ArtifactMismatchError as err:
            # Audit invalidation event
            effective_reason = (
                reason or f"Material change detected during integrity check: {err}"
            )
            self.audit_store.append_event(
                approval_id=approval_id,
                actor_id=actor_id,
                action="approval_invalidated",
                previous_state=prev_state,
                new_state=request.status.value,
                reason=effective_reason,
                artifact_hashes={
                    "bound_patch_hash": request.artifact_binding.patch_hash,
                    "current_patch_hash": current_patch_hash,
                    "bound_review_hash": request.artifact_binding.review_hash,
                    "current_review_hash": current_review_hash,
                },
            )
            return request, True
