"""Policy definitions and evaluation engine for human approvals."""

from typing import Any

from pydantic import BaseModel, Field

from backend.app.approval.exceptions import (
    ApprovalPolicyViolationError,
    SeparationOfDutiesError,
    UnauthorizedApproverError,
)
from backend.app.approval.models import (
    ApprovalDecision,
    ApprovalPolicy,
    ApprovalRequest,
    ApprovalRiskLevel,
    ApprovalRole,
    ApprovalStatus,
    DecisionOutcome,
)


class PolicyEvaluationResult(BaseModel):
    """Result of evaluating an approval request against configured policy."""

    is_approved: bool = Field(
        default=False, description="True if policy requirements for release are met"
    )
    is_rejected: bool = Field(
        default=False, description="True if any decision explicitly rejected release"
    )
    is_changes_requested: bool = Field(
        default=False, description="True if changes were requested"
    )
    is_blocked: bool = Field(
        default=False, description="True if unresolved findings block release"
    )
    approved_count: int = Field(
        default=0, ge=0, description="Count of distinct eligible approvals"
    )
    required_count: int = Field(
        default=1, ge=1, description="Minimum distinct approvals required"
    )
    participating_actors: list[str] = Field(
        default_factory=list, description="List of actors who submitted decisions"
    )
    violations: list[str] = Field(
        default_factory=list, description="Policy violations detected"
    )
    recommended_status: ApprovalStatus = Field(
        default=ApprovalStatus.PENDING,
        description="Computed status recommendation based on policy evaluation",
    )


def get_default_policy() -> ApprovalPolicy:
    """Return default approval policy for standard releases."""
    return ApprovalPolicy(
        policy_id="default",
        name="Default Approval Policy",
        minimum_approvers=1,
        required_roles=[ApprovalRole.APPROVER, ApprovalRole.ADMIN],
        separation_of_duties=True,
        block_on_critical_findings=True,
        block_on_unresolved_repairs=True,
        allow_self_approval=False,
        reapproval_on_material_change=True,
        timeout_hours=24.0,
    )


def get_low_risk_policy() -> ApprovalPolicy:
    """Return policy for low-risk non-critical changes."""
    return ApprovalPolicy(
        policy_id="low_risk",
        name="Low Risk Policy",
        minimum_approvers=1,
        required_roles=[
            ApprovalRole.REVIEWER,
            ApprovalRole.APPROVER,
            ApprovalRole.ADMIN,
        ],
        separation_of_duties=True,
        block_on_critical_findings=True,
        block_on_unresolved_repairs=True,
        allow_self_approval=False,
        reapproval_on_material_change=True,
        timeout_hours=48.0,
    )


def get_high_risk_policy() -> ApprovalPolicy:
    """Return elevated governance policy for high-risk changes."""
    return ApprovalPolicy(
        policy_id="high_risk",
        name="High Risk Policy",
        minimum_approvers=2,
        required_roles=[ApprovalRole.APPROVER, ApprovalRole.ADMIN],
        separation_of_duties=True,
        block_on_critical_findings=True,
        block_on_unresolved_repairs=True,
        allow_self_approval=False,
        reapproval_on_material_change=True,
        timeout_hours=12.0,
    )


def get_critical_policy() -> ApprovalPolicy:
    """Return strict governance policy requiring admin sign-off for critical changes."""
    return ApprovalPolicy(
        policy_id="critical",
        name="Critical Risk Policy",
        minimum_approvers=2,
        required_roles=[ApprovalRole.ADMIN],
        separation_of_duties=True,
        block_on_critical_findings=True,
        block_on_unresolved_repairs=True,
        allow_self_approval=False,
        reapproval_on_material_change=True,
        timeout_hours=6.0,
    )


def policy_for_risk_level(risk_level: ApprovalRiskLevel) -> ApprovalPolicy:
    """Retrieve standard policy preset matching the given risk tier."""
    match risk_level:
        case ApprovalRiskLevel.LOW:
            return get_low_risk_policy()
        case ApprovalRiskLevel.MEDIUM:
            return get_default_policy()
        case ApprovalRiskLevel.HIGH:
            return get_high_risk_policy()
        case ApprovalRiskLevel.CRITICAL:
            return get_critical_policy()


def evaluate_risk_level_from_review(
    review_report: Any = None,
    execution_report: Any = None,
) -> tuple[ApprovalRiskLevel, list[str]]:
    """Determine risk level and blocking reasons from upstream review and execution."""
    blockers: list[str] = []

    if review_report is None:
        return ApprovalRiskLevel.CRITICAL, ["Missing mandatory code review report"]

    # Extract review findings metrics if available
    summary = getattr(review_report, "summary", None)
    critical_count = getattr(summary, "critical_count", 0) if summary else 0
    high_count = getattr(summary, "high_count", 0) if summary else 0
    medium_count = getattr(summary, "medium_count", 0) if summary else 0
    review_blockers = getattr(review_report, "blockers", []) or []

    if critical_count > 0:
        blockers.append(f"Review contains {critical_count} critical finding(s)")
    if review_blockers:
        blockers.extend(review_blockers)

    # Check execution report if provided
    if execution_report is not None:
        exec_status = str(getattr(execution_report, "status", "")).lower()
        if exec_status in ("failed", "timed_out", "rejected"):
            blockers.append(f"Execution verification did not succeed: {exec_status}")
        test_summary = getattr(execution_report, "test_summary", None)
        if test_summary and getattr(test_summary, "failed", 0) > 0:
            blockers.append(
                f"Execution tests failed: {getattr(test_summary, 'failed', 0)} failures"
            )

    if critical_count > 0 or review_blockers or blockers:
        return ApprovalRiskLevel.CRITICAL, blockers
    if high_count > 0:
        return ApprovalRiskLevel.HIGH, blockers
    if medium_count > 0:
        return ApprovalRiskLevel.MEDIUM, blockers

    return ApprovalRiskLevel.LOW, blockers


class ApprovalPolicyEvaluator:
    """Evaluates human decisions and release requests against governance policies."""

    def __init__(self, policy: ApprovalPolicy | None = None) -> None:
        self.default_policy = policy or get_default_policy()

    def evaluate_decision_permission(
        self,
        request: ApprovalRequest,
        decision: ApprovalDecision,
    ) -> None:
        """Validate whether an individual actor is eligible to submit a decision."""
        policy = request.approval_policy

        # Check separation of duties
        if (
            policy.separation_of_duties
            and not policy.allow_self_approval
            and decision.actor_id == request.requested_by
        ):
            raise SeparationOfDutiesError(
                f"Actor '{decision.actor_id}' is the requester and cannot approve "
                "or decide this request under separation of duties policy."
            )

        # Check role authorization
        if (
            decision.actor_role not in policy.required_roles
            and decision.actor_role != ApprovalRole.ADMIN
        ):
            raise UnauthorizedApproverError(
                f"Actor role '{decision.actor_role.value}' is not among required "
                f"roles {[r.value for r in policy.required_roles]} "
                f"for policy '{policy.policy_id}'."
            )

    def evaluate(
        self,
        request: ApprovalRequest,
        candidate_decision: ApprovalDecision | None = None,
    ) -> PolicyEvaluationResult:
        """Evaluate complete request state against policy."""
        policy = request.approval_policy
        violations: list[str] = []

        all_decisions = list(request.decisions)
        if candidate_decision is not None:
            self.evaluate_decision_permission(request, candidate_decision)
            all_decisions.append(candidate_decision)

        # Track latest decision per actor
        latest_by_actor: dict[str, ApprovalDecision] = {}
        for d in all_decisions:
            latest_by_actor[d.actor_id] = d

        participating = list(latest_by_actor.keys())

        # Check for active blockers
        is_blocked = False
        if request.blocking_reasons and policy.block_on_critical_findings:
            is_blocked = True
            violations.extend(request.blocking_reasons)

        # Check for explicit rejections
        is_rejected = any(
            d.outcome == DecisionOutcome.REJECTED for d in latest_by_actor.values()
        )
        is_changes_requested = any(
            d.outcome == DecisionOutcome.CHANGES_REQUESTED
            for d in latest_by_actor.values()
        )

        # Calculate eligible approvals
        valid_approvals: list[ApprovalDecision] = []
        for actor_id, d in latest_by_actor.items():
            if d.outcome != DecisionOutcome.APPROVED:
                continue

            # Separation of duties check
            if (
                policy.separation_of_duties
                and not policy.allow_self_approval
                and actor_id == request.requested_by
            ):
                violations.append(f"Self-approval by requester '{actor_id}' discarded.")
                continue

            # Role check
            if (
                d.actor_role not in policy.required_roles
                and d.actor_role != ApprovalRole.ADMIN
            ):
                violations.append(
                    f"Approval by '{actor_id}' discarded due to role "
                    f"'{d.actor_role.value}'."
                )
                continue

            valid_approvals.append(d)

        approved_count = len(valid_approvals)
        required_count = policy.minimum_approvers
        is_approved = (
            approved_count >= required_count
            and not is_blocked
            and not is_rejected
            and not is_changes_requested
        )

        # Determine recommended status
        if is_blocked:
            recommended_status = ApprovalStatus.BLOCKED
        elif is_rejected:
            recommended_status = ApprovalStatus.REJECTED
        elif is_changes_requested:
            recommended_status = ApprovalStatus.CHANGES_REQUESTED
        elif is_approved:
            recommended_status = ApprovalStatus.APPROVED
        else:
            recommended_status = ApprovalStatus.PENDING

        return PolicyEvaluationResult(
            is_approved=is_approved,
            is_rejected=is_rejected,
            is_changes_requested=is_changes_requested,
            is_blocked=is_blocked,
            approved_count=approved_count,
            required_count=required_count,
            participating_actors=participating,
            violations=violations,
            recommended_status=recommended_status,
        )

    def enforce_approval_readiness(self, request: ApprovalRequest) -> None:
        """Raise ApprovalPolicyViolationError if request does not meet policy."""
        result = self.evaluate(request)
        if not result.is_approved:
            reasons = result.violations or [
                f"Insufficient approvers: "
                f"{result.approved_count}/{result.required_count}"
            ]
            raise ApprovalPolicyViolationError(
                f"Approval policy requirements not satisfied: {'; '.join(reasons)}"
            )
