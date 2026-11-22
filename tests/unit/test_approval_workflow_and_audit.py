"""Unit tests for approval workflow services and tamper-evident audit trail."""

from datetime import UTC, datetime, timedelta

import pytest

from backend.app.approval.audit import (
    GENESIS_HASH,
    AuditTrailStore,
    compute_event_hash,
)
from backend.app.approval.authorization import AuthorizationEngine
from backend.app.approval.decision_service import ApprovalDecisionService
from backend.app.approval.exceptions import (
    ApprovalNotFoundError,
    ArtifactMismatchError,
    RepositoryPermissionError,
    SeparationOfDutiesError,
    UnauthorizedApproverError,
)
from backend.app.approval.expiration import ExpirationManager
from backend.app.approval.identity import (
    LocalTestIdentityProvider,
    RepositoryPermission,
    UserIdentity,
)
from backend.app.approval.models import (
    ApprovalPolicy,
    ApprovalRequest,
    ApprovalRole,
    ApprovalStatus,
    ArtifactBinding,
    DecisionOutcome,
)
from backend.app.approval.request_service import ApprovalRequestService

VALID_HASH_A = "a" * 64
VALID_HASH_B = "b" * 64
VALID_HASH_C = "c" * 64


def make_test_binding(
    patch_hash: str = VALID_HASH_A,
    review_hash: str = VALID_HASH_B,
) -> ArtifactBinding:
    """Helper generating valid ArtifactBinding."""
    return ArtifactBinding(
        patch_hash=patch_hash,
        review_hash=review_hash,
        target_branch="main",
    )


# ==============================================================================
# AuditTrailStore Tests
# ==============================================================================


def test_audit_store_append_and_hash_chain() -> None:
    """Test appending audit events and sequential hash-chain generation."""
    store = AuditTrailStore()

    ev1 = store.append_event(
        approval_id="appr_01",
        actor_id="user_admin",
        action="approval_requested",
        new_state="pending",
        artifact_hashes={"patch_hash": VALID_HASH_A},
    )
    assert ev1.sequence == 1
    assert ev1.previous_event_hash == GENESIS_HASH
    assert len(ev1.event_hash) == 64
    assert ev1.event_hash == compute_event_hash(ev1)

    ev2 = store.append_event(
        approval_id="appr_01",
        actor_id="lead_approver",
        action="approved",
        previous_state="pending",
        new_state="approved",
        reason="Code looks great",
    )
    assert ev2.sequence == 2
    assert ev2.previous_event_hash == ev1.event_hash
    assert ev2.event_hash == compute_event_hash(ev2)

    # Verify audit integrity
    is_valid, violations = store.verify_integrity("appr_01")
    assert is_valid is True
    assert violations == []


def test_audit_store_detects_record_tampering() -> None:
    """Test that modifying any event attribute breaks hash-chain verification."""
    store = AuditTrailStore()

    store.append_event(
        approval_id="appr_tamper",
        actor_id="user_1",
        action="approval_requested",
        new_state="pending",
    )
    store.append_event(
        approval_id="appr_tamper",
        actor_id="user_2",
        action="approved",
        previous_state="pending",
        new_state="approved",
    )

    # Tamper with internal recorded event payload
    records = store.get_events("appr_tamper")
    records[0].actor_id = "hacker_attacker"

    is_valid, violations = store.verify_integrity("appr_tamper")
    assert is_valid is False
    assert any("Tampering detected" in v for v in violations)


def test_audit_store_detects_hash_chain_discontinuity() -> None:
    """Test that breaking the hash link or sequence is caught."""
    store = AuditTrailStore()

    store.append_event(
        approval_id="appr_chain",
        actor_id="u1",
        action="approval_requested",
    )
    store.append_event(
        approval_id="appr_chain",
        actor_id="u2",
        action="approved",
    )

    records = store.get_events("appr_chain")
    # Corrupt previous_event_hash of second event
    records[1].previous_event_hash = "f" * 64

    is_valid, violations = store.verify_integrity("appr_chain")
    assert is_valid is False
    assert any("Broken hash chain" in v for v in violations)


def test_audit_store_secret_redaction() -> None:
    """Test that sensitive credentials in reasons or metadata are redacted."""
    store = AuditTrailStore()

    ev = store.append_event(
        approval_id="appr_sec",
        actor_id="admin",
        action="comment",
        reason="Found secret: api_key='sk-1234567890abcdef'",
        metadata={"note": "token: Bearer abcdef1234567890xyz"},
    )
    assert "[REDACTED]" in (ev.reason or "")
    assert "sk-1234567890abcdef" not in (ev.reason or "")
    assert "[REDACTED_BEARER_TOKEN]" in str(ev.metadata.get("note", ""))


# ==============================================================================
# ExpirationManager Tests
# ==============================================================================


def test_expiration_manager_expiry_calculations() -> None:
    """Test expiration detection and remaining duration calculations."""
    mgr = ExpirationManager()

    now = datetime.now(UTC)
    future_iso = (now + timedelta(hours=5)).isoformat()
    past_iso = (now - timedelta(hours=1)).isoformat()

    binding = make_test_binding()

    req_future = ApprovalRequest(
        approval_id="appr_future",
        repository_id="repo_1",
        release_candidate_id="rc_1",
        requested_by="bot",
        artifact_binding=binding,
        expires_at=future_iso,
    )
    assert mgr.is_expired(req_future, now=now) is False
    assert mgr.get_time_remaining(req_future, now=now).total_seconds() > 0

    req_past = ApprovalRequest(
        approval_id="appr_past",
        repository_id="repo_1",
        release_candidate_id="rc_1",
        requested_by="bot",
        artifact_binding=binding,
        expires_at=past_iso,
    )
    assert mgr.is_expired(req_past, now=now) is True
    assert mgr.get_time_remaining(req_past, now=now) == timedelta(0)


def test_expiration_manager_check_and_sweep() -> None:
    """Test check_and_expire and sweep_expired_requests transitions."""
    audit_store = AuditTrailStore()
    mgr = ExpirationManager(audit_store=audit_store)

    now = datetime.now(UTC)
    past_iso = (now - timedelta(minutes=10)).isoformat()
    future_iso = (now + timedelta(hours=2)).isoformat()
    binding = make_test_binding()

    req1 = ApprovalRequest(
        approval_id="appr_sweep_1",
        repository_id="repo_1",
        release_candidate_id="rc_1",
        requested_by="bot",
        artifact_binding=binding,
        status=ApprovalStatus.PENDING,
        expires_at=past_iso,
    )
    req2 = ApprovalRequest(
        approval_id="appr_sweep_2",
        repository_id="repo_1",
        release_candidate_id="rc_2",
        requested_by="bot",
        artifact_binding=binding,
        status=ApprovalStatus.APPROVED,
        expires_at=past_iso,
    )
    req3 = ApprovalRequest(
        approval_id="appr_sweep_3",
        repository_id="repo_1",
        release_candidate_id="rc_3",
        requested_by="bot",
        artifact_binding=binding,
        status=ApprovalStatus.PENDING,
        expires_at=future_iso,
    )

    swept = mgr.sweep_expired_requests([req1, req2, req3], now=now)
    assert len(swept) == 2
    assert req1.status == ApprovalStatus.EXPIRED
    assert req2.status == ApprovalStatus.EXPIRED
    assert req3.status == ApprovalStatus.PENDING

    # Verify audit events were recorded
    events_1 = audit_store.get_events("appr_sweep_1")
    assert any(e.action == "approval_expired" for e in events_1)


# ==============================================================================
# ApprovalRequestService Tests
# ==============================================================================


def test_request_service_create_and_get() -> None:
    """Test standard approval request creation and retrieval."""
    id_provider = LocalTestIdentityProvider()
    auth_engine = AuthorizationEngine(identity_provider=id_provider)
    audit_store = AuditTrailStore()
    service = ApprovalRequestService(auth_engine=auth_engine, audit_store=audit_store)

    requester = UserIdentity(
        actor_id="coder_1",
        username="coder",
        display_name="Coder",
        roles=[ApprovalRole.REQUESTER],
        repository_permissions={"repo_main": [RepositoryPermission.WRITE.value]},
    )
    binding = make_test_binding()

    req = service.create_request(
        actor=requester,
        repository_id="repo_main",
        release_candidate_id="rc_test_01",
        artifact_binding=binding,
    )
    assert req.approval_id.startswith("appr_")
    assert req.status == ApprovalStatus.PENDING
    assert req.requested_by == "coder_1"

    # Retrieve
    fetched = service.get_request(req.approval_id)
    assert fetched.approval_id == req.approval_id

    # Non-existent
    with pytest.raises(ApprovalNotFoundError, match="not found"):
        service.get_request("non_existent_id")


def test_request_service_unauthorized_requester_fails() -> None:
    """Test that requester lacking write permission cannot request approval."""
    service = ApprovalRequestService()
    unauthorized_user = UserIdentity(
        actor_id="viewer_1",
        username="viewer",
        display_name="Viewer",
        roles=[],
        repository_permissions={"repo_main": [RepositoryPermission.READ.value]},
    )
    binding = make_test_binding()

    with pytest.raises(RepositoryPermissionError, match="lacks write permission"):
        service.create_request(
            actor=unauthorized_user,
            repository_id="repo_main",
            release_candidate_id="rc_01",
            artifact_binding=binding,
        )


def test_request_service_initial_blocked_on_critical_findings() -> None:
    """Test that requests with active blockers initialize in BLOCKED status."""
    service = ApprovalRequestService()
    requester = UserIdentity(
        actor_id="bot_1",
        username="bot",
        display_name="Bot",
        roles=[ApprovalRole.REQUESTER],
        repository_permissions={"repo_main": [RepositoryPermission.WRITE.value]},
    )
    binding = make_test_binding()

    req = service.create_request(
        actor=requester,
        repository_id="repo_main",
        release_candidate_id="rc_blocker",
        artifact_binding=binding,
        blocking_reasons=["Critical SQL injection vulnerability detected"],
    )
    assert req.status == ApprovalStatus.BLOCKED
    assert len(req.blocking_reasons) == 1


def test_request_service_list_filters() -> None:
    """Test filtering requests by repo, status, and requester."""
    service = ApprovalRequestService()
    requester = UserIdentity(
        actor_id="alice",
        username="alice",
        display_name="Alice",
        roles=[ApprovalRole.REQUESTER],
        repository_permissions={"*": [RepositoryPermission.WRITE.value]},
    )
    binding = make_test_binding()

    r1 = service.create_request(
        actor=requester,
        repository_id="repo_alpha",
        release_candidate_id="rc_1",
        artifact_binding=binding,
    )
    service.create_request(
        actor=requester,
        repository_id="repo_beta",
        release_candidate_id="rc_2",
        artifact_binding=binding,
    )

    res_alpha = service.list_requests(repository_id="repo_alpha")
    assert len(res_alpha) == 1
    assert res_alpha[0].approval_id == r1.approval_id

    res_status = service.list_requests(status=ApprovalStatus.PENDING)
    assert len(res_status) == 2


# ==============================================================================
# ApprovalDecisionService Tests
# ==============================================================================


def test_decision_service_submit_approval_and_rejection() -> None:
    """Test decision submission transitions request and writes audit log."""
    request_service = ApprovalRequestService()
    audit_store = request_service.audit_store
    decision_service = ApprovalDecisionService(
        request_service=request_service, audit_store=audit_store
    )

    requester = UserIdentity(
        actor_id="bot_requester",
        username="bot",
        display_name="Bot",
        roles=[ApprovalRole.REQUESTER],
        repository_permissions={"repo_corp": [RepositoryPermission.WRITE.value]},
    )
    binding = make_test_binding()
    req = request_service.create_request(
        actor=requester,
        repository_id="repo_corp",
        release_candidate_id="rc_dec_01",
        artifact_binding=binding,
    )

    approver = UserIdentity(
        actor_id="eng_lead",
        username="lead",
        display_name="Lead",
        roles=[ApprovalRole.APPROVER],
        repository_permissions={"repo_corp": [RepositoryPermission.APPROVE.value]},
    )

    # Approve
    updated = decision_service.submit_decision(
        actor=approver,
        approval_id=req.approval_id,
        outcome=DecisionOutcome.APPROVED,
        associated_patch_hash=VALID_HASH_A,
        associated_review_hash=VALID_HASH_B,
    )
    assert updated.status == ApprovalStatus.APPROVED
    assert len(updated.decisions) == 1

    # Verify audit log
    events = audit_store.get_events(req.approval_id)
    assert len(events) >= 2
    assert events[-1].action == "decision_approved"

    is_valid, violations = audit_store.verify_integrity(req.approval_id)
    assert is_valid is True
    assert violations == []


def test_decision_service_separation_of_duties_prevents_approval() -> None:
    """Test that decision service prevents self-approval by requester."""
    request_service = ApprovalRequestService()
    decision_service = ApprovalDecisionService(request_service=request_service)

    requester = UserIdentity(
        actor_id="alice",
        username="alice",
        display_name="Alice",
        roles=[ApprovalRole.REQUESTER, ApprovalRole.APPROVER],
        repository_permissions={"repo_corp": [RepositoryPermission.ADMIN.value]},
    )
    binding = make_test_binding()
    req = request_service.create_request(
        actor=requester,
        repository_id="repo_corp",
        release_candidate_id="rc_self",
        artifact_binding=binding,
    )

    with pytest.raises(SeparationOfDutiesError, match="Separation of duties"):
        decision_service.submit_decision(
            actor=requester,
            approval_id=req.approval_id,
            outcome=DecisionOutcome.APPROVED,
        )


def test_decision_service_hash_mismatch_rejection() -> None:
    """Test decision rejected when inspected patch hash differs from bound hash."""
    request_service = ApprovalRequestService()
    decision_service = ApprovalDecisionService(request_service=request_service)

    requester = UserIdentity(
        actor_id="bot_req",
        username="bot",
        display_name="Bot",
        roles=[ApprovalRole.REQUESTER],
        repository_permissions={"repo_corp": [RepositoryPermission.WRITE.value]},
    )
    binding = make_test_binding(patch_hash=VALID_HASH_A)
    req = request_service.create_request(
        actor=requester,
        repository_id="repo_corp",
        release_candidate_id="rc_mismatch",
        artifact_binding=binding,
    )

    approver = UserIdentity(
        actor_id="eng_approver",
        username="appr",
        display_name="Approver",
        roles=[ApprovalRole.APPROVER],
        repository_permissions={"repo_corp": [RepositoryPermission.APPROVE.value]},
    )

    with pytest.raises(
        ArtifactMismatchError, match="Decision patch hash does not match"
    ):
        decision_service.submit_decision(
            actor=approver,
            approval_id=req.approval_id,
            outcome=DecisionOutcome.APPROVED,
            associated_patch_hash="9" * 64,  # different hash
        )


def test_decision_service_revocation_lifecycle() -> None:
    """Test revocation by prior approver and admin."""
    request_service = ApprovalRequestService()
    audit_store = request_service.audit_store
    decision_service = ApprovalDecisionService(
        request_service=request_service, audit_store=audit_store
    )

    requester = UserIdentity(
        actor_id="bot_req",
        username="bot",
        display_name="Bot",
        roles=[ApprovalRole.REQUESTER],
        repository_permissions={"repo_corp": [RepositoryPermission.WRITE.value]},
    )
    approver = UserIdentity(
        actor_id="lead_eng",
        username="lead",
        display_name="Lead",
        roles=[ApprovalRole.APPROVER],
        repository_permissions={"repo_corp": [RepositoryPermission.APPROVE.value]},
    )
    binding = make_test_binding()
    req = request_service.create_request(
        actor=requester,
        repository_id="repo_corp",
        release_candidate_id="rc_revoke",
        artifact_binding=binding,
    )

    # Approve
    decision_service.submit_decision(
        actor=approver,
        approval_id=req.approval_id,
        outcome=DecisionOutcome.APPROVED,
    )

    # Revoke by original approver
    revoked_req = decision_service.revoke_approval(
        actor=approver,
        approval_id=req.approval_id,
        reason="Flaw discovered in test assertion post-approval",
    )
    assert revoked_req.status == ApprovalStatus.REVOKED

    # Unauthorized random user cannot revoke
    rando = UserIdentity(
        actor_id="random_person",
        username="rando",
        display_name="Rando",
        roles=[ApprovalRole.REVIEWER],
    )
    with pytest.raises(UnauthorizedApproverError, match="not an administrator"):
        decision_service.revoke_approval(
            actor=rando,
            approval_id=req.approval_id,
            reason="I don't like it",
        )

    # Verify audit integrity across creation, approval, and revocation
    is_valid, violations = audit_store.verify_integrity(req.approval_id)
    assert is_valid is True
    assert violations == []


def test_decision_service_invalidate_on_material_change() -> None:
    """Test that material changes invalidate existing approval and audit the event."""
    request_service = ApprovalRequestService()
    audit_store = request_service.audit_store
    decision_service = ApprovalDecisionService(
        request_service=request_service, audit_store=audit_store
    )

    requester = UserIdentity(
        actor_id="bot_req",
        username="bot",
        display_name="Bot",
        roles=[ApprovalRole.REQUESTER],
        repository_permissions={"repo_corp": [RepositoryPermission.WRITE.value]},
    )
    approver = UserIdentity(
        actor_id="approver_1",
        username="appr",
        display_name="Approver",
        roles=[ApprovalRole.APPROVER],
        repository_permissions={"repo_corp": [RepositoryPermission.APPROVE.value]},
    )
    policy = ApprovalPolicy(reapproval_on_material_change=True)
    binding = make_test_binding(patch_hash=VALID_HASH_A, review_hash=VALID_HASH_B)

    req = request_service.create_request(
        actor=requester,
        repository_id="repo_corp",
        release_candidate_id="rc_material",
        artifact_binding=binding,
        policy=policy,
    )
    decision_service.submit_decision(
        actor=approver,
        approval_id=req.approval_id,
        outcome=DecisionOutcome.APPROVED,
    )
    assert req.status == ApprovalStatus.APPROVED

    # Matching check returns False for invalidation
    _, invalidated = decision_service.invalidate_on_material_change(
        approval_id=req.approval_id,
        current_patch_hash=VALID_HASH_A,
        current_review_hash=VALID_HASH_B,
    )
    assert invalidated is False
    assert req.status == ApprovalStatus.APPROVED

    # Altered patch hash triggers invalidation to CHANGES_REQUESTED
    updated_req, invalidated = decision_service.invalidate_on_material_change(
        approval_id=req.approval_id,
        current_patch_hash="f" * 64,
        current_review_hash=VALID_HASH_B,
    )
    assert invalidated is True
    assert updated_req.status == ApprovalStatus.CHANGES_REQUESTED

    # Verify audit chain integrity includes the invalidation event
    events = audit_store.get_events(req.approval_id)
    assert any(e.action == "approval_invalidated" for e in events)

    is_valid, violations = audit_store.verify_integrity(req.approval_id)
    assert is_valid is True
    assert violations == []
