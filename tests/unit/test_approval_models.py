"""Unit tests for approval data models, policies, and state machine."""

from datetime import UTC, datetime, timedelta

import pytest

from backend.app.approval.exceptions import (
    ApprovalExpiredError,
    ApprovalPolicyViolationError,
    ApprovalRevokedError,
    ArtifactMismatchError,
    InvalidStateTransitionError,
    SeparationOfDutiesError,
    UnauthorizedApproverError,
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
    evaluate_risk_level_from_review,
    get_critical_policy,
    get_default_policy,
    get_high_risk_policy,
    get_low_risk_policy,
    policy_for_risk_level,
)
from backend.app.approval.state_machine import ApprovalStateMachine

VALID_HASH_A = "a" * 64
VALID_HASH_B = "b" * 64
VALID_HASH_C = "c" * 64
VALID_COMMIT = "1234567890abcdef"


def make_artifact_binding(
    patch_hash: str = VALID_HASH_A,
    review_hash: str | None = VALID_HASH_B,
    plan_hash: str | None = VALID_HASH_C,
) -> ArtifactBinding:
    """Helper to generate a valid ArtifactBinding fixture."""
    return ArtifactBinding(
        patch_hash=patch_hash,
        plan_id="plan_001",
        plan_hash=plan_hash,
        verification_id="exec_001",
        verification_hash=VALID_HASH_C,
        review_id="rev_001",
        review_hash=review_hash,
        base_commit=VALID_COMMIT,
        target_branch="main",
    )


def make_approval_request(
    approval_id: str = "appr_001",
    requested_by: str = "agent_coder",
    status: ApprovalStatus = ApprovalStatus.PENDING,
    policy: ApprovalPolicy | None = None,
    binding: ArtifactBinding | None = None,
    risk_level: ApprovalRiskLevel = ApprovalRiskLevel.LOW,
    blocking_reasons: list[str] | None = None,
    expires_at: str | None = None,
) -> ApprovalRequest:
    """Helper to generate a valid ApprovalRequest fixture."""
    return ApprovalRequest(
        approval_id=approval_id,
        repository_id="repo_main",
        release_candidate_id="rc_001",
        requested_by=requested_by,
        approval_policy=policy or get_default_policy(),
        artifact_binding=binding or make_artifact_binding(),
        status=status,
        risk_level=risk_level,
        blocking_reasons=blocking_reasons or [],
        expires_at=expires_at,
    )


# ==============================================================================
# Model & Validation Tests
# ==============================================================================


def test_artifact_binding_valid() -> None:
    """Test valid artifact binding creation and normalization."""
    binding = ArtifactBinding(
        patch_hash=VALID_HASH_A.upper(),
        plan_hash=VALID_HASH_B.upper(),
        review_hash=VALID_HASH_C.upper(),
        base_commit="AbCdEf12345",
    )
    assert binding.patch_hash == VALID_HASH_A.lower()
    assert binding.plan_hash == VALID_HASH_B.lower()
    assert binding.review_hash == VALID_HASH_C.lower()
    assert binding.base_commit == "abcdef12345"


def test_artifact_binding_invalid_patch_hash() -> None:
    """Test rejection of non-SHA256 patch hash."""
    with pytest.raises(ValueError, match="64-character hexadecimal"):
        ArtifactBinding(patch_hash="invalid_hash_string")


def test_artifact_binding_invalid_optional_hash() -> None:
    """Test rejection of invalid optional artifact hash."""
    with pytest.raises(ValueError, match="64-character hexadecimal"):
        ArtifactBinding(patch_hash=VALID_HASH_A, review_hash="short")


def test_artifact_binding_invalid_base_commit() -> None:
    """Test rejection of invalid git commit format."""
    with pytest.raises(ValueError, match="7 to 64 character hex"):
        ArtifactBinding(patch_hash=VALID_HASH_A, base_commit="xyz!")


def test_approval_policy_presets() -> None:
    """Test standard policy presets configuration."""
    default_pol = get_default_policy()
    assert default_pol.minimum_approvers == 1
    assert default_pol.separation_of_duties is True
    assert ApprovalRole.APPROVER in default_pol.required_roles

    low_pol = get_low_risk_policy()
    assert low_pol.minimum_approvers == 1
    assert ApprovalRole.REVIEWER in low_pol.required_roles

    high_pol = get_high_risk_policy()
    assert high_pol.minimum_approvers == 2
    assert high_pol.timeout_hours == 12.0

    crit_pol = get_critical_policy()
    assert crit_pol.minimum_approvers == 2
    assert crit_pol.required_roles == [ApprovalRole.ADMIN]

    assert policy_for_risk_level(ApprovalRiskLevel.LOW).policy_id == "low_risk"
    assert policy_for_risk_level(ApprovalRiskLevel.MEDIUM).policy_id == "default"
    assert policy_for_risk_level(ApprovalRiskLevel.HIGH).policy_id == "high_risk"
    assert policy_for_risk_level(ApprovalRiskLevel.CRITICAL).policy_id == "critical"


def test_approval_decision_valid() -> None:
    """Test valid decision creation with reasons for disruptive outcomes."""
    decision = ApprovalDecision(
        decision_id="dec_01",
        approval_id="appr_01",
        actor_id="human_approver_1",
        actor_role=ApprovalRole.APPROVER,
        outcome=DecisionOutcome.APPROVED,
    )
    assert decision.outcome == DecisionOutcome.APPROVED
    assert decision.reason is None


def test_approval_decision_requires_reason_for_negative_outcomes() -> None:
    """Test that rejection, changes_requested, and revocation mandate a reason."""
    for outcome in (
        DecisionOutcome.REJECTED,
        DecisionOutcome.CHANGES_REQUESTED,
        DecisionOutcome.REVOKED,
    ):
        with pytest.raises(ValueError, match="non-empty reason is required"):
            ApprovalDecision(
                decision_id="dec_02",
                approval_id="appr_01",
                actor_id="human_approver_1",
                actor_role=ApprovalRole.APPROVER,
                outcome=outcome,
                reason=None,
            )

        with pytest.raises(ValueError, match="non-empty reason is required"):
            ApprovalDecision(
                decision_id="dec_02",
                approval_id="appr_01",
                actor_id="human_approver_1",
                actor_role=ApprovalRole.APPROVER,
                outcome=outcome,
                reason="   ",
            )


def test_approval_decision_rejects_empty_identifiers() -> None:
    """Test validation rejecting empty string IDs."""
    with pytest.raises(ValueError, match="must not be empty"):
        ApprovalDecision(
            decision_id="",
            approval_id="appr_01",
            actor_id="human_1",
            actor_role=ApprovalRole.APPROVER,
            outcome=DecisionOutcome.APPROVED,
        )


def test_approval_request_computation_and_defaults() -> None:
    """Test automatic computation of expires_at and required_approvers."""
    pol = ApprovalPolicy(minimum_approvers=3, timeout_hours=48.0)
    req = make_approval_request(policy=pol)
    assert req.required_approvers == 3
    assert req.expires_at is not None
    created = datetime.fromisoformat(req.created_at)
    expires = datetime.fromisoformat(req.expires_at)
    assert expires > created
    delta = expires - created
    assert abs(delta.total_seconds() - 48 * 3600) < 5


def test_approval_audit_event_logging() -> None:
    """Test audit event fields and defaults."""
    evt = ApprovalAuditEvent(
        event_id="evt_01",
        approval_id="appr_01",
        event_type="created",
        actor_id="user_admin",
        details={"key": "value"},
    )
    assert evt.event_id == "evt_01"
    assert evt.details["key"] == "value"


# ==============================================================================
# Policy Evaluator Tests
# ==============================================================================


def test_policy_evaluator_single_approval_satisfaction() -> None:
    """Test single approver policy passing when valid decision is applied."""
    req = make_approval_request(requested_by="bot_agent")
    evaluator = ApprovalPolicyEvaluator()

    decision = ApprovalDecision(
        decision_id="dec_01",
        approval_id=req.approval_id,
        actor_id="lead_eng",
        actor_role=ApprovalRole.APPROVER,
        outcome=DecisionOutcome.APPROVED,
    )
    res = evaluator.evaluate(req, candidate_decision=decision)
    assert res.is_approved is True
    assert res.approved_count == 1
    assert res.recommended_status == ApprovalStatus.APPROVED


def test_policy_evaluator_multi_approver_requirement() -> None:
    """Test policy requiring 2 approvers remains pending with only 1 approval."""
    pol = get_high_risk_policy()  # requires 2 approvers
    req = make_approval_request(policy=pol, requested_by="bot_agent")
    evaluator = ApprovalPolicyEvaluator()

    d1 = ApprovalDecision(
        decision_id="dec_01",
        approval_id=req.approval_id,
        actor_id="eng_1",
        actor_role=ApprovalRole.APPROVER,
        outcome=DecisionOutcome.APPROVED,
    )
    req.decisions.append(d1)
    res = evaluator.evaluate(req)
    assert res.is_approved is False
    assert res.approved_count == 1
    assert res.required_count == 2
    assert res.recommended_status == ApprovalStatus.PENDING

    d2 = ApprovalDecision(
        decision_id="dec_02",
        approval_id=req.approval_id,
        actor_id="eng_2",
        actor_role=ApprovalRole.ADMIN,
        outcome=DecisionOutcome.APPROVED,
    )
    res2 = evaluator.evaluate(req, candidate_decision=d2)
    assert res2.is_approved is True
    assert res2.approved_count == 2
    assert res2.recommended_status == ApprovalStatus.APPROVED


def test_policy_evaluator_separation_of_duties_enforcement() -> None:
    """Test that requester cannot approve their own change."""
    req = make_approval_request(requested_by="engineer_alice")
    evaluator = ApprovalPolicyEvaluator()

    self_decision = ApprovalDecision(
        decision_id="dec_self",
        approval_id=req.approval_id,
        actor_id="engineer_alice",
        actor_role=ApprovalRole.APPROVER,
        outcome=DecisionOutcome.APPROVED,
    )
    with pytest.raises(SeparationOfDutiesError, match="separation of duties"):
        evaluator.evaluate(req, candidate_decision=self_decision)


def test_policy_evaluator_allow_self_approval_override() -> None:
    """Test self-approval succeeds when explicitly permitted by policy."""
    pol = ApprovalPolicy(
        minimum_approvers=1,
        separation_of_duties=False,
        allow_self_approval=True,
    )
    req = make_approval_request(policy=pol, requested_by="solo_dev")
    evaluator = ApprovalPolicyEvaluator()

    decision = ApprovalDecision(
        decision_id="dec_self",
        approval_id=req.approval_id,
        actor_id="solo_dev",
        actor_role=ApprovalRole.APPROVER,
        outcome=DecisionOutcome.APPROVED,
    )
    res = evaluator.evaluate(req, candidate_decision=decision)
    assert res.is_approved is True


def test_policy_evaluator_unauthorized_role_rejection() -> None:
    """Test decision rejected when actor role is not in required roles."""
    pol = get_critical_policy()  # requires ADMIN
    req = make_approval_request(policy=pol, requested_by="bot")
    evaluator = ApprovalPolicyEvaluator()

    reviewer_decision = ApprovalDecision(
        decision_id="dec_rev",
        approval_id=req.approval_id,
        actor_id="reviewer_bob",
        actor_role=ApprovalRole.REVIEWER,
        outcome=DecisionOutcome.APPROVED,
    )
    with pytest.raises(UnauthorizedApproverError, match="not among required"):
        evaluator.evaluate(req, candidate_decision=reviewer_decision)


def test_policy_evaluator_rejection_and_changes_priority() -> None:
    """Test that rejection and changes requested supersede positive approvals."""
    req = make_approval_request(requested_by="bot")
    evaluator = ApprovalPolicyEvaluator()

    d1 = ApprovalDecision(
        decision_id="dec_01",
        approval_id=req.approval_id,
        actor_id="eng_1",
        actor_role=ApprovalRole.APPROVER,
        outcome=DecisionOutcome.APPROVED,
    )
    d2 = ApprovalDecision(
        decision_id="dec_02",
        approval_id=req.approval_id,
        actor_id="sec_lead",
        actor_role=ApprovalRole.ADMIN,
        outcome=DecisionOutcome.REJECTED,
        reason="Detected security risk in patch",
    )
    req.decisions.extend([d1, d2])

    res = evaluator.evaluate(req)
    assert res.is_approved is False
    assert res.is_rejected is True
    assert res.recommended_status == ApprovalStatus.REJECTED


def test_policy_evaluator_blocks_on_critical_findings() -> None:
    """Test policy blocks approval when request has active blockers."""
    req = make_approval_request(
        requested_by="bot",
        blocking_reasons=["Hardcoded secret detected in auth module"],
    )
    evaluator = ApprovalPolicyEvaluator()

    d1 = ApprovalDecision(
        decision_id="dec_01",
        approval_id=req.approval_id,
        actor_id="eng_1",
        actor_role=ApprovalRole.APPROVER,
        outcome=DecisionOutcome.APPROVED,
    )
    req.decisions.append(d1)

    res = evaluator.evaluate(req)
    assert res.is_approved is False
    assert res.is_blocked is True
    assert res.recommended_status == ApprovalStatus.BLOCKED

    with pytest.raises(ApprovalPolicyViolationError, match="Hardcoded secret"):
        evaluator.enforce_approval_readiness(req)


def test_evaluate_risk_level_from_review_heuristics() -> None:
    """Test risk level derivation from review and execution structures."""
    # 1. Missing review -> critical
    risk, blockers = evaluate_risk_level_from_review(None)
    assert risk == ApprovalRiskLevel.CRITICAL
    assert len(blockers) > 0

    # 2. Mock review with critical finding
    class MockSummary:
        critical_count = 1
        high_count = 0
        medium_count = 0

    class MockReview:
        summary = MockSummary()
        blockers = ["Critical security issue"]

    risk, blockers = evaluate_risk_level_from_review(MockReview())
    assert risk == ApprovalRiskLevel.CRITICAL
    assert len(blockers) >= 1

    # 3. Mock review with high finding
    class MockHighSummary:
        critical_count = 0
        high_count = 2
        medium_count = 1

    class MockHighReview:
        summary = MockHighSummary()
        blockers: list[str] = []

    risk, _ = evaluate_risk_level_from_review(MockHighReview())
    assert risk == ApprovalRiskLevel.HIGH

    # 4. Mock clean review
    class MockCleanSummary:
        critical_count = 0
        high_count = 0
        medium_count = 0

    class MockCleanReview:
        summary = MockCleanSummary()
        blockers: list[str] = []

    risk, blockers = evaluate_risk_level_from_review(MockCleanReview())
    assert risk == ApprovalRiskLevel.LOW
    assert len(blockers) == 0


# ==============================================================================
# State Machine Tests
# ==============================================================================


def test_state_machine_valid_transitions() -> None:
    """Test valid lifecycle transitions."""
    sm = ApprovalStateMachine()
    req = make_approval_request()

    assert sm.can_transition(ApprovalStatus.PENDING, ApprovalStatus.APPROVED)
    assert sm.can_transition(ApprovalStatus.PENDING, ApprovalStatus.REJECTED)
    assert sm.can_transition(ApprovalStatus.PENDING, ApprovalStatus.CHANGES_REQUESTED)
    assert sm.can_transition(ApprovalStatus.APPROVED, ApprovalStatus.REVOKED)
    assert sm.can_transition(ApprovalStatus.APPROVED, ApprovalStatus.EXPIRED)
    assert sm.can_transition(ApprovalStatus.CHANGES_REQUESTED, ApprovalStatus.PENDING)
    assert sm.can_transition(ApprovalStatus.BLOCKED, ApprovalStatus.PENDING)

    # Execute transition
    req = sm.transition(req, ApprovalStatus.APPROVED, reason="Passed inspection")
    assert req.status == ApprovalStatus.APPROVED
    assert len(req.audit_events) == 1
    assert req.audit_events[0].details["to_status"] == "approved"


def test_state_machine_invalid_transitions() -> None:
    """Test illegal transitions raise InvalidStateTransitionError."""
    sm = ApprovalStateMachine()
    req = make_approval_request(status=ApprovalStatus.REJECTED)

    with pytest.raises(InvalidStateTransitionError, match="Cannot transition"):
        sm.transition(req, ApprovalStatus.APPROVED)

    expired_req = make_approval_request(status=ApprovalStatus.EXPIRED)
    with pytest.raises(InvalidStateTransitionError, match="Cannot transition"):
        sm.transition(expired_req, ApprovalStatus.PENDING)


def test_state_machine_check_expiration() -> None:
    """Test expiration check transitions request past timeout."""
    past_time = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    req = make_approval_request(expires_at=past_time)
    sm = ApprovalStateMachine()

    expired = sm.check_expiration(req)
    assert expired is True
    assert req.status == ApprovalStatus.EXPIRED
    assert any("exceeded" in e.details.get("reason", "") for e in req.audit_events)

    # Non-expired request returns False
    future_time = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    req2 = make_approval_request(expires_at=future_time)
    assert sm.check_expiration(req2) is False
    assert req2.status == ApprovalStatus.PENDING


def test_state_machine_material_integrity_validation() -> None:
    """Test material integrity validation and invalidation on hash mismatch."""
    sm = ApprovalStateMachine()
    req = make_approval_request(status=ApprovalStatus.APPROVED)

    # Identical hashes pass
    assert sm.validate_material_integrity(
        request=req,
        current_patch_hash=VALID_HASH_A,
        current_review_hash=VALID_HASH_B,
        current_plan_hash=VALID_HASH_C,
    )

    # Modified patch hash triggers invalidation of approval and raises error
    different_patch = "f" * 64
    with pytest.raises(ArtifactMismatchError, match="Bound artifact hash mismatch"):
        sm.validate_material_integrity(
            request=req,
            current_patch_hash=different_patch,
            current_review_hash=VALID_HASH_B,
        )

    assert req.status == ApprovalStatus.CHANGES_REQUESTED
    assert any(
        "Material change detected" in e.details.get("reason", "")
        for e in req.audit_events
    )


def test_state_machine_apply_decision_lifecycle() -> None:
    """Test applying human decision updates state and records audit log."""
    sm = ApprovalStateMachine()
    req = make_approval_request(requested_by="bot")

    # Apply valid approval
    decision = ApprovalDecision(
        decision_id="dec_01",
        approval_id=req.approval_id,
        actor_id="eng_lead",
        actor_role=ApprovalRole.APPROVER,
        outcome=DecisionOutcome.APPROVED,
        associated_patch_hash=VALID_HASH_A,
        associated_review_hash=VALID_HASH_B,
    )
    sm.apply_decision(req, decision)
    assert req.status == ApprovalStatus.APPROVED
    assert len(req.decisions) == 1
    assert any(e.event_type == "decision_submitted" for e in req.audit_events)

    # Apply revocation
    revocation = ApprovalDecision(
        decision_id="dec_02",
        approval_id=req.approval_id,
        actor_id="admin_user",
        actor_role=ApprovalRole.ADMIN,
        outcome=DecisionOutcome.REVOKED,
        reason="Security issue reported post-approval",
    )
    updated_req = sm.apply_decision(req, revocation)
    assert updated_req.status == ApprovalStatus.REVOKED

    # Subsequent decision on revoked request raises ApprovalRevokedError
    with pytest.raises(ApprovalRevokedError, match="has been revoked"):
        sm.apply_decision(updated_req, decision)


def test_state_machine_apply_decision_hash_mismatch() -> None:
    """Test decision rejected when inspected patch hash does not match request."""
    sm = ApprovalStateMachine()
    req = make_approval_request(requested_by="bot")

    decision = ApprovalDecision(
        decision_id="dec_bad_hash",
        approval_id=req.approval_id,
        actor_id="eng_lead",
        actor_role=ApprovalRole.APPROVER,
        outcome=DecisionOutcome.APPROVED,
        associated_patch_hash="9" * 64,  # different from request binding
    )
    with pytest.raises(
        ArtifactMismatchError, match="Decision patch hash does not match"
    ):
        sm.apply_decision(req, decision)


def test_state_machine_apply_decision_on_expired_request() -> None:
    """Test decision rejected when applied to an already expired request."""
    past_time = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    req = make_approval_request(requested_by="bot", expires_at=past_time)
    sm = ApprovalStateMachine()

    decision = ApprovalDecision(
        decision_id="dec_late",
        approval_id=req.approval_id,
        actor_id="eng_lead",
        actor_role=ApprovalRole.APPROVER,
        outcome=DecisionOutcome.APPROVED,
        associated_patch_hash=VALID_HASH_A,
    )
    with pytest.raises(ApprovalExpiredError, match="has expired"):
        sm.apply_decision(req, decision)
