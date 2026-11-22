"""State machine and lifecycle management for approval requests."""

from datetime import UTC, datetime
from uuid import uuid4

from backend.app.approval.exceptions import (
    ApprovalExpiredError,
    ApprovalRevokedError,
    ArtifactMismatchError,
    InvalidStateTransitionError,
)
from backend.app.approval.models import (
    ApprovalAuditEvent,
    ApprovalDecision,
    ApprovalRequest,
    ApprovalStatus,
    DecisionOutcome,
)
from backend.app.approval.policies import ApprovalPolicyEvaluator

# Explicit map of permissible lifecycle transitions
VALID_TRANSITIONS: dict[ApprovalStatus, set[ApprovalStatus]] = {
    ApprovalStatus.PENDING: {
        ApprovalStatus.APPROVED,
        ApprovalStatus.REJECTED,
        ApprovalStatus.CHANGES_REQUESTED,
        ApprovalStatus.EXPIRED,
        ApprovalStatus.BLOCKED,
    },
    ApprovalStatus.APPROVED: {
        ApprovalStatus.REVOKED,
        ApprovalStatus.EXPIRED,
        ApprovalStatus.CHANGES_REQUESTED,
        ApprovalStatus.BLOCKED,
    },
    ApprovalStatus.CHANGES_REQUESTED: {
        ApprovalStatus.PENDING,
        ApprovalStatus.EXPIRED,
    },
    ApprovalStatus.BLOCKED: {
        ApprovalStatus.PENDING,
        ApprovalStatus.REJECTED,
        ApprovalStatus.EXPIRED,
    },
    ApprovalStatus.REJECTED: set(),  # Terminal
    ApprovalStatus.EXPIRED: set(),  # Terminal
    ApprovalStatus.REVOKED: set(),  # Terminal
}


class ApprovalStateMachine:
    """Enforces deterministic lifecycle transitions and material integrity."""

    def __init__(self, evaluator: ApprovalPolicyEvaluator | None = None) -> None:
        self.evaluator = evaluator or ApprovalPolicyEvaluator()

    @staticmethod
    def can_transition(from_status: ApprovalStatus, to_status: ApprovalStatus) -> bool:
        """Check if transition from one status to another is legal."""
        if from_status == to_status:
            return True
        allowed = VALID_TRANSITIONS.get(from_status, set())
        return to_status in allowed

    def transition(
        self,
        request: ApprovalRequest,
        to_status: ApprovalStatus,
        reason: str | None = None,
        actor_id: str | None = None,
    ) -> ApprovalRequest:
        """Transition request status and record an immutable audit event."""
        if request.status == to_status:
            return request

        if not self.can_transition(request.status, to_status):
            raise InvalidStateTransitionError(
                f"Cannot transition approval request from '{request.status.value}' "
                f"to '{to_status.value}'."
            )

        old_status = request.status
        request.status = to_status
        now_iso = datetime.now(UTC).isoformat()
        request.updated_at = now_iso

        event = ApprovalAuditEvent(
            event_id=f"evt_{uuid4().hex[:12]}",
            approval_id=request.approval_id,
            event_type=f"status_changed_to_{to_status.value}",
            actor_id=actor_id or "system",
            timestamp=now_iso,
            details={
                "from_status": old_status.value,
                "to_status": to_status.value,
                "reason": reason,
            },
        )
        request.audit_events.append(event)
        return request

    def check_expiration(
        self,
        request: ApprovalRequest,
        now: datetime | None = None,
    ) -> bool:
        """Check if request has exceeded its expiry and transition to EXPIRED if so."""
        if request.status not in (
            ApprovalStatus.PENDING,
            ApprovalStatus.APPROVED,
        ):
            return False

        if not request.expires_at:
            return False

        current_time = now or datetime.now(UTC)
        try:
            expires_dt = datetime.fromisoformat(request.expires_at)
        except Exception:
            return False

        if current_time >= expires_dt:
            self.transition(
                request=request,
                to_status=ApprovalStatus.EXPIRED,
                reason="Request exceeded configured timeout without release execution",
                actor_id="system",
            )
            return True

        return False

    def validate_material_integrity(
        self,
        request: ApprovalRequest,
        current_patch_hash: str,
        current_review_hash: str | None = None,
        current_plan_hash: str | None = None,
    ) -> bool:
        """Verify candidate artifacts match the bound hashes; invalidate if altered."""
        binding = request.artifact_binding
        norm_patch = current_patch_hash.strip().lower()
        patch_matches = norm_patch == binding.patch_hash.lower()

        review_matches = True
        if current_review_hash is not None and binding.review_hash is not None:
            review_matches = (
                current_review_hash.strip().lower() == binding.review_hash.lower()
            )

        plan_matches = True
        if current_plan_hash is not None and binding.plan_hash is not None:
            plan_matches = (
                current_plan_hash.strip().lower() == binding.plan_hash.lower()
            )

        all_matched = patch_matches and review_matches and plan_matches
        if not all_matched:
            # If request was approved and policy mandates reapproval on material change
            if (
                request.status == ApprovalStatus.APPROVED
                and request.approval_policy.reapproval_on_material_change
            ):
                self.transition(
                    request=request,
                    to_status=ApprovalStatus.CHANGES_REQUESTED,
                    reason=(
                        "Material change detected: artifact hashes no longer match "
                        "approved bindings. Prior approval invalidated."
                    ),
                    actor_id="system",
                )
            raise ArtifactMismatchError(
                "Bound artifact hash mismatch detected: "
                f"patch_matches={patch_matches}, "
                f"review_matches={review_matches}, "
                f"plan_matches={plan_matches}."
            )

        return True

    def apply_decision(
        self,
        request: ApprovalRequest,
        decision: ApprovalDecision,
    ) -> ApprovalRequest:
        """Process and record a human decision, updating request status."""
        # 1. Check expiration
        if self.check_expiration(request):
            raise ApprovalExpiredError(
                f"Approval request '{request.approval_id}' has expired and "
                "cannot accept decisions."
            )

        # 2. Check terminal statuses
        if request.status == ApprovalStatus.REVOKED:
            raise ApprovalRevokedError(
                f"Approval request '{request.approval_id}' has been revoked."
            )
        if request.status in (ApprovalStatus.REJECTED, ApprovalStatus.EXPIRED):
            raise InvalidStateTransitionError(
                "Cannot submit decision on terminal approval request in status "
                f"'{request.status.value}'."
            )

        # 3. Check artifact binding matching in decision
        if decision.associated_patch_hash is not None:
            if (
                decision.associated_patch_hash.strip().lower()
                != request.artifact_binding.patch_hash.lower()
            ):
                raise ArtifactMismatchError(
                    "Decision patch hash does not match request bound patch hash."
                )

        if (
            decision.associated_review_hash is not None
            and request.artifact_binding.review_hash is not None
        ):
            if (
                decision.associated_review_hash.strip().lower()
                != request.artifact_binding.review_hash.lower()
            ):
                raise ArtifactMismatchError(
                    "Decision review hash does not match request bound review hash."
                )

        # 4. Check decision permission via policy evaluator
        self.evaluator.evaluate_decision_permission(request, decision)

        # 5. Handle revocation
        if decision.outcome == DecisionOutcome.REVOKED:
            request.decisions.append(decision)
            self.transition(
                request=request,
                to_status=ApprovalStatus.REVOKED,
                reason=decision.reason,
                actor_id=decision.actor_id,
            )
            return request

        # 6. Append decision
        request.decisions.append(decision)
        now_iso = datetime.now(UTC).isoformat()
        request.audit_events.append(
            ApprovalAuditEvent(
                event_id=f"evt_{uuid4().hex[:12]}",
                approval_id=request.approval_id,
                event_type="decision_submitted",
                actor_id=decision.actor_id,
                timestamp=now_iso,
                details={
                    "outcome": decision.outcome.value,
                    "role": decision.actor_role.value,
                    "reason": decision.reason,
                },
            )
        )

        # 7. Evaluate complete policy
        result = self.evaluator.evaluate(request)

        # 8. Apply recommended status transition
        if result.recommended_status != request.status:
            self.transition(
                request=request,
                to_status=result.recommended_status,
                reason=decision.reason
                or f"Policy evaluation: {result.recommended_status.value}",
                actor_id=decision.actor_id,
            )

        return request
