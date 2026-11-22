"""Approval expiration management and automated sweep service."""

from datetime import UTC, datetime, timedelta

from backend.app.approval.audit import AuditTrailStore
from backend.app.approval.models import ApprovalRequest, ApprovalStatus
from backend.app.approval.state_machine import ApprovalStateMachine


class ExpirationManager:
    """Manages lifecycle expiration checks and scheduled sweep evaluations."""

    def __init__(
        self,
        state_machine: ApprovalStateMachine | None = None,
        audit_store: AuditTrailStore | None = None,
    ) -> None:
        self.state_machine = state_machine or ApprovalStateMachine()
        self.audit_store = audit_store

    def is_expired(self, request: ApprovalRequest, now: datetime | None = None) -> bool:
        """Return True if the request has passed its expiration deadline."""
        if not request.expires_at:
            return False
        current_time = now or datetime.now(UTC)
        try:
            expires_dt = datetime.fromisoformat(request.expires_at)
        except Exception:
            return False
        return current_time >= expires_dt

    def get_time_remaining(
        self, request: ApprovalRequest, now: datetime | None = None
    ) -> timedelta:
        """Calculate duration remaining before request expiry, or 0 if expired."""
        if not request.expires_at:
            return timedelta(0)
        current_time = now or datetime.now(UTC)
        try:
            expires_dt = datetime.fromisoformat(request.expires_at)
        except Exception:
            return timedelta(0)

        remaining = expires_dt - current_time
        return remaining if remaining > timedelta(0) else timedelta(0)

    def check_and_expire(
        self,
        request: ApprovalRequest,
        now: datetime | None = None,
        actor_id: str = "system",
    ) -> bool:
        """Check and transition request to EXPIRED if deadline exceeded."""
        if request.status not in (
            ApprovalStatus.PENDING,
            ApprovalStatus.APPROVED,
        ):
            return False

        if not self.is_expired(request, now=now):
            return False

        prev_status = request.status.value
        self.state_machine.transition(
            request=request,
            to_status=ApprovalStatus.EXPIRED,
            reason="Request deadline exceeded without release execution",
            actor_id=actor_id,
        )

        if self.audit_store:
            self.audit_store.append_event(
                approval_id=request.approval_id,
                actor_id=actor_id,
                action="approval_expired",
                previous_state=prev_status,
                new_state=ApprovalStatus.EXPIRED.value,
                reason="Automatic expiration sweep: deadline exceeded",
                artifact_hashes={
                    "patch_hash": request.artifact_binding.patch_hash,
                    "review_hash": request.artifact_binding.review_hash,
                },
            )

        return True

    def sweep_expired_requests(
        self,
        requests: list[ApprovalRequest],
        now: datetime | None = None,
        actor_id: str = "system",
    ) -> list[ApprovalRequest]:
        """Sweep multiple requests and expire all that passed their deadline."""
        expired_requests: list[ApprovalRequest] = []
        current_time = now or datetime.now(UTC)
        for req in requests:
            if self.check_and_expire(req, now=current_time, actor_id=actor_id):
                expired_requests.append(req)
        return expired_requests
