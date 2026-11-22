"""Authorization policies and permission enforcement engine."""

from pydantic import BaseModel, Field

from backend.app.approval.exceptions import (
    ApprovalPolicyViolationError,
    ConflictingDecisionError,
    IdentityVerificationError,
    RepositoryPermissionError,
    SeparationOfDutiesError,
    UnauthorizedApproverError,
)
from backend.app.approval.identity import (
    IdentityProvider,
    LocalTestIdentityProvider,
    RepositoryPermission,
    UserIdentity,
)
from backend.app.approval.models import (
    ApprovalRequest,
    ApprovalRole,
    ApprovalStatus,
    DecisionOutcome,
)


class AuthorizationDecision(BaseModel):
    """Result of an authorization evaluation."""

    allowed: bool = Field(description="True if action is permitted")
    reason: str = Field(description="Explanation of authorization decision")
    violations: list[str] = Field(
        default_factory=list, description="Specific violation details if denied"
    )


class AuthorizationEngine:
    """Enforces role-based, repository-level, and separation-of-duties authorization."""

    def __init__(self, identity_provider: IdentityProvider | None = None) -> None:
        self.identity_provider = identity_provider or LocalTestIdentityProvider()

    def check_can_request_approval(
        self, actor: UserIdentity, repository_id: str
    ) -> AuthorizationDecision:
        """Verify actor has authority to create an approval request for a repo."""
        if not actor.is_authenticated:
            return AuthorizationDecision(
                allowed=False,
                reason="Actor identity is not authenticated",
                violations=["Unauthenticated actor"],
            )

        has_repo_write = actor.has_repo_permission(
            repository_id, RepositoryPermission.WRITE
        )
        has_req_role = (
            actor.has_role(ApprovalRole.REQUESTER)
            or actor.has_role(ApprovalRole.APPROVER)
            or actor.has_role(ApprovalRole.ADMIN)
        )

        if not (has_repo_write or has_req_role):
            msg = (
                f"Actor '{actor.actor_id}' lacks write permission or requester role "
                f"for repository '{repository_id}'."
            )
            return AuthorizationDecision(allowed=False, reason=msg, violations=[msg])

        return AuthorizationDecision(
            allowed=True, reason="Actor authorized to request approval"
        )

    def check_can_decide(
        self,
        actor: UserIdentity,
        request: ApprovalRequest,
        outcome: DecisionOutcome,
    ) -> AuthorizationDecision:
        """Verify actor is permitted to submit an approval decision."""
        violations: list[str] = []

        if not actor.is_authenticated:
            return AuthorizationDecision(
                allowed=False,
                reason="Actor identity is not authenticated",
                violations=["Unauthenticated actor"],
            )

        policy = request.approval_policy

        # 1. Separation of Duties check
        if (
            policy.separation_of_duties
            and not policy.allow_self_approval
            and actor.actor_id == request.requested_by
        ):
            msg = (
                f"Separation of duties violation: requester '{actor.actor_id}' "
                "cannot decide or approve their own change request."
            )
            return AuthorizationDecision(allowed=False, reason=msg, violations=[msg])

        # 2. Role authorization check
        if outcome == DecisionOutcome.APPROVED:
            has_role = any(actor.has_role(r) for r in policy.required_roles)
            if not has_role and not actor.has_role(ApprovalRole.ADMIN):
                msg = (
                    f"Actor '{actor.actor_id}' lacks required role among "
                    f"{[r.value for r in policy.required_roles]} to approve."
                )
                violations.append(msg)

        # 3. Repository-level permission check
        required_perm = (
            RepositoryPermission.APPROVE
            if outcome == DecisionOutcome.APPROVED
            else RepositoryPermission.TRIAGE
        )
        if not actor.has_repo_permission(request.repository_id, required_perm):
            msg = (
                f"Actor '{actor.actor_id}' lacks '{required_perm.value}' "
                f"permission on repository '{request.repository_id}'."
            )
            violations.append(msg)

        # 4. Duplicate decision check
        for d in request.decisions:
            if d.actor_id == actor.actor_id and d.outcome == outcome:
                msg = (
                    f"Actor '{actor.actor_id}' has already submitted an identical "
                    f"decision with outcome '{outcome.value}'."
                )
                violations.append(msg)
                break

        if violations:
            return AuthorizationDecision(
                allowed=False,
                reason="Authorization denied: " + "; ".join(violations),
                violations=violations,
            )

        return AuthorizationDecision(
            allowed=True,
            reason=(
                f"Actor '{actor.actor_id}' authorized to submit outcome "
                f"'{outcome.value}'"
            ),
        )

    def check_can_revoke(
        self, actor: UserIdentity, request: ApprovalRequest
    ) -> AuthorizationDecision:
        """Verify actor has authority to revoke an approved request."""
        if not actor.is_authenticated:
            return AuthorizationDecision(
                allowed=False,
                reason="Actor identity is not authenticated",
                violations=["Unauthenticated actor"],
            )

        # Admins or repo admins can always revoke
        if actor.has_role(ApprovalRole.ADMIN) or actor.has_repo_permission(
            request.repository_id, RepositoryPermission.ADMIN
        ):
            return AuthorizationDecision(
                allowed=True, reason="Administrator authorized to revoke approval"
            )

        # An approver who previously approved can revoke their approval
        prior_approver = any(
            d.actor_id == actor.actor_id and d.outcome == DecisionOutcome.APPROVED
            for d in request.decisions
        )
        if prior_approver:
            return AuthorizationDecision(
                allowed=True,
                reason="Prior approver authorized to revoke approval",
            )

        msg = (
            f"Actor '{actor.actor_id}' is not an administrator or a prior "
            "approver of this request and cannot revoke it."
        )
        return AuthorizationDecision(allowed=False, reason=msg, violations=[msg])

    def check_can_release(
        self, actor: UserIdentity, request: ApprovalRequest
    ) -> AuthorizationDecision:
        """Verify actor has authority to release an approved candidate."""
        violations: list[str] = []

        if not actor.is_authenticated:
            return AuthorizationDecision(
                allowed=False,
                reason="Actor identity is not authenticated",
                violations=["Unauthenticated actor"],
            )

        # 1. State must be APPROVED
        if request.status != ApprovalStatus.APPROVED:
            msg = (
                f"Release candidate is in status '{request.status.value}', "
                "not 'approved'. Release cannot proceed."
            )
            violations.append(msg)

        # 2. Release operator or admin role required
        if not (
            actor.has_role(ApprovalRole.RELEASE_OPERATOR)
            or actor.has_role(ApprovalRole.ADMIN)
        ):
            msg = f"Actor '{actor.actor_id}' lacks release operator or admin role."
            violations.append(msg)

        # 3. Repository release permission
        if not actor.has_repo_permission(
            request.repository_id, RepositoryPermission.RELEASE
        ):
            msg = (
                f"Actor '{actor.actor_id}' lacks release permission on "
                f"repository '{request.repository_id}'."
            )
            violations.append(msg)

        # 4. Separation of duties: requester cannot be the release operator
        policy = request.approval_policy
        if (
            policy.separation_of_duties
            and not policy.allow_self_approval
            and actor.actor_id == request.requested_by
        ):
            msg = (
                f"Separation of duties violation: requester '{actor.actor_id}' "
                "cannot authorize release of their own change."
            )
            violations.append(msg)

        if violations:
            return AuthorizationDecision(
                allowed=False,
                reason="Release authorization denied: " + "; ".join(violations),
                violations=violations,
            )

        return AuthorizationDecision(
            allowed=True,
            reason=f"Actor '{actor.actor_id}' authorized to execute release",
        )

    def enforce_can_request_approval(
        self, actor: UserIdentity, repository_id: str
    ) -> None:
        """Raise error if actor cannot request approval."""
        decision = self.check_can_request_approval(actor, repository_id)
        if not decision.allowed:
            if not actor.is_authenticated:
                raise IdentityVerificationError(decision.reason)
            raise RepositoryPermissionError(decision.reason)

    def enforce_can_decide(
        self,
        actor: UserIdentity,
        request: ApprovalRequest,
        outcome: DecisionOutcome,
    ) -> None:
        """Raise error if actor cannot submit decision."""
        decision = self.check_can_decide(actor, request, outcome)
        if not decision.allowed:
            if not actor.is_authenticated:
                raise IdentityVerificationError(decision.reason)
            for v in decision.violations:
                if "Separation of duties" in v:
                    raise SeparationOfDutiesError(v)
                if "identical decision" in v:
                    raise ConflictingDecisionError(v)
                if "lacks required role" in v:
                    raise UnauthorizedApproverError(v)
                if "lacks" in v and "permission on repository" in v:
                    raise RepositoryPermissionError(v)
            raise UnauthorizedApproverError(decision.reason)

    def enforce_can_revoke(self, actor: UserIdentity, request: ApprovalRequest) -> None:
        """Raise error if actor cannot revoke approval."""
        decision = self.check_can_revoke(actor, request)
        if not decision.allowed:
            if not actor.is_authenticated:
                raise IdentityVerificationError(decision.reason)
            raise UnauthorizedApproverError(decision.reason)

    def enforce_can_release(
        self, actor: UserIdentity, request: ApprovalRequest
    ) -> None:
        """Raise error if actor cannot authorize release."""
        decision = self.check_can_release(actor, request)
        if not decision.allowed:
            if not actor.is_authenticated:
                raise IdentityVerificationError(decision.reason)
            for v in decision.violations:
                if "not 'approved'" in v:
                    raise ApprovalPolicyViolationError(v)
                if "Separation of duties" in v:
                    raise SeparationOfDutiesError(v)
                if "lacks release permission" in v:
                    raise RepositoryPermissionError(v)
            raise UnauthorizedApproverError(decision.reason)
