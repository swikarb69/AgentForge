"""Unit tests for release readiness validation engine."""

from backend.app.approval.models import (
    ApprovalDecision,
    ApprovalPolicy,
    ApprovalRequest,
    ApprovalRiskLevel,
    ApprovalRole,
    ApprovalStatus,
    ArtifactBinding,
    DecisionOutcome,
)
from backend.app.approval.readiness import (
    ReadinessStatus,
    ReleaseReadinessEngine,
)

VALID_HASH_A = "a" * 64
VALID_HASH_B = "b" * 64
VALID_HASH_C = "c" * 64


def make_approved_candidate() -> ApprovalRequest:
    """Helper creating a fully approved, valid candidate request."""
    policy = ApprovalPolicy(minimum_approvers=1, required_roles=[ApprovalRole.APPROVER])
    binding = ArtifactBinding(
        patch_hash=VALID_HASH_A,
        plan_id="plan_01",
        plan_hash=VALID_HASH_C,
        review_id="rev_01",
        review_hash=VALID_HASH_B,
        target_branch="main",
    )
    decision = ApprovalDecision(
        decision_id="dec_01",
        approval_id="appr_ready_01",
        actor_id="lead_eng",
        actor_role=ApprovalRole.APPROVER,
        outcome=DecisionOutcome.APPROVED,
        associated_patch_hash=VALID_HASH_A,
        associated_review_hash=VALID_HASH_B,
    )
    return ApprovalRequest(
        approval_id="appr_ready_01",
        repository_id="repo_corp",
        release_candidate_id="rc_100",
        requested_by="bot_coder",
        approval_policy=policy,
        artifact_binding=binding,
        status=ApprovalStatus.APPROVED,
        risk_level=ApprovalRiskLevel.LOW,
        decisions=[decision],
    )


class MockReviewSummary:
    critical_count = 0
    high_count = 0


class MockReviewReport:
    summary = MockReviewSummary()
    blockers: list[str] = []


class MockVerificationReport:
    verification_passed = True
    failures: list[str] = []


class MockPlan:
    plan_id = "plan_01"


# ==============================================================================
# Release Readiness Engine Tests
# ==============================================================================


def test_readiness_all_checks_passed() -> None:
    """Test fully compliant candidate produces READY verdict."""
    engine = ReleaseReadinessEngine()
    req = make_approved_candidate()

    report = engine.evaluate(
        request=req,
        plan=MockPlan(),
        verification_report=MockVerificationReport(),
        review_report=MockReviewReport(),
    )
    assert report.is_release_ready is True
    assert report.status == ReadinessStatus.READY
    assert len(report.failed_checks) == 0
    assert len(report.blocking_reasons) == 0
    assert report.obtained_approvals == 1
    assert "repository_reference" in report.passed_checks
    assert "critical_findings_check" in report.passed_checks


def test_readiness_fails_on_missing_review_report() -> None:
    """Test candidate blocked when mandatory review report is missing."""
    engine = ReleaseReadinessEngine()
    req = make_approved_candidate()
    req.artifact_binding.review_id = None

    report = engine.evaluate(
        request=req,
        plan=MockPlan(),
        verification_report=MockVerificationReport(),
        review_report=None,
    )
    assert report.is_release_ready is False
    assert report.status in (ReadinessStatus.BLOCKED, ReadinessStatus.REQUIRES_REVIEW)
    assert "review_report_presence" in report.failed_checks


def test_readiness_fails_on_critical_review_findings() -> None:
    """Test candidate blocked when review contains critical findings."""
    engine = ReleaseReadinessEngine()
    req = make_approved_candidate()

    class BadReviewSummary:
        critical_count = 2

    class BadReviewReport:
        summary = BadReviewSummary()
        blockers = ["Critical security alert: command injection"]

    report = engine.evaluate(
        request=req,
        plan=MockPlan(),
        verification_report=MockVerificationReport(),
        review_report=BadReviewReport(),
    )
    assert report.is_release_ready is False
    assert report.status == ReadinessStatus.BLOCKED
    assert "critical_findings_check" in report.failed_checks


def test_readiness_fails_on_unapproved_status() -> None:
    """Test candidate not ready when status is PENDING or REJECTED."""
    engine = ReleaseReadinessEngine()
    req = make_approved_candidate()
    req.status = ApprovalStatus.PENDING

    report = engine.evaluate(
        request=req,
        plan=MockPlan(),
        verification_report=MockVerificationReport(),
        review_report=MockReviewReport(),
    )
    assert report.is_release_ready is False
    assert report.status == ReadinessStatus.NOT_READY
    assert "approval_status_valid" in report.failed_checks


def test_readiness_fails_on_execution_verification_failure() -> None:
    """Test candidate fails when sandbox execution did not pass."""
    engine = ReleaseReadinessEngine()
    req = make_approved_candidate()

    class FailingVerification:
        verification_passed = False
        failures = ["test_auth_flow failed with AssertionError"]

    report = engine.evaluate(
        request=req,
        plan=MockPlan(),
        verification_report=FailingVerification(),
        review_report=MockReviewReport(),
    )
    assert report.is_release_ready is False
    assert "execution_verification" in report.failed_checks
    assert any("Execution verification failed" in b for b in report.blocking_reasons)


def test_readiness_fails_on_unresolved_repair_session() -> None:
    """Test candidate fails when an automatic repair session was left unresolved."""
    engine = ReleaseReadinessEngine()
    req = make_approved_candidate()

    class FailedRepair:
        status = "failed"

    report = engine.evaluate(
        request=req,
        plan=MockPlan(),
        verification_report=MockVerificationReport(),
        review_report=MockReviewReport(),
        repair_session=FailedRepair(),
    )
    assert report.is_release_ready is False
    assert "repair_status" in report.failed_checks


def test_readiness_fails_on_artifact_hash_mismatch() -> None:
    """Test candidate fails when current patch hash differs from bound hash."""
    engine = ReleaseReadinessEngine()
    req = make_approved_candidate()

    report = engine.evaluate(
        request=req,
        plan=MockPlan(),
        verification_report=MockVerificationReport(),
        review_report=MockReviewReport(),
        current_patch_hash="9" * 64,  # altered
    )
    assert report.is_release_ready is False
    assert "artifact_hash_match" in report.failed_checks


def test_readiness_fails_on_disallowed_target_branch() -> None:
    """Test candidate fails when target branch violates policy."""
    engine = ReleaseReadinessEngine()
    req = make_approved_candidate()
    req.artifact_binding.target_branch = "unauthorized_feature_branch"

    report = engine.evaluate(
        request=req,
        plan=MockPlan(),
        verification_report=MockVerificationReport(),
        review_report=MockReviewReport(),
    )
    assert report.is_release_ready is False
    assert "target_branch_valid" in report.failed_checks


def test_readiness_fails_on_plan_id_mismatch() -> None:
    """Test candidate fails when upstream plan ID does not match bound plan ID."""
    engine = ReleaseReadinessEngine()
    req = make_approved_candidate()

    class MismatchedPlan:
        plan_id = "completely_different_plan"

    report = engine.evaluate(
        request=req,
        plan=MismatchedPlan(),
        verification_report=MockVerificationReport(),
        review_report=MockReviewReport(),
    )
    assert report.is_release_ready is False
    assert "plan_verification" in report.failed_checks
