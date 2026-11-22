"""End-to-end integration tests for Human Approval & Controlled Release pipeline."""

from fastapi.testclient import TestClient

from backend.app.api.routes.approval import approval_service
from backend.app.approval.models import (
    ApprovalRiskLevel,
    ApprovalStatus,
    DecisionOutcome,
)
from backend.app.approval.readiness import ReadinessStatus
from backend.app.approval.release_models import ReleaseStatus
from backend.app.main import app

client = TestClient(app)

VALID_HASH_A = "a" * 64
VALID_HASH_B = "b" * 64
VALID_HASH_C = "c" * 64


def test_end_to_end_approval_and_release_flow() -> None:
    """Verify complete approval and controlled release workflow via REST API."""
    # 1. Create Approval Request
    create_payload = {
        "repository_id": "swikarb69/AgentForge",
        "release_candidate_id": "RC-E2E-2026-001",
        "artifact_binding": {
            "patch_hash": VALID_HASH_A,
            "plan_id": "plan_e2e_01",
            "plan_hash": VALID_HASH_C,
            "review_id": "rev_e2e_01",
            "review_hash": VALID_HASH_B,
            "target_branch": "main",
        },
        "actor_token": "agent_coder",
        "risk_level": ApprovalRiskLevel.LOW.value,
        "issue_id": "ISSUE-100",
        "plan_id": "plan_e2e_01",
        "generation_id": "gen_e2e_01",
        "execution_id": "exec_e2e_01",
        "review_id": "rev_e2e_01",
    }

    create_resp = client.post("/api/v1/approval/requests", json=create_payload)
    assert create_resp.status_code == 201
    created_data = create_resp.json()
    approval_id = created_data["approval_id"]
    assert created_data["status"] == ApprovalStatus.PENDING.value
    assert created_data["requested_by"] == "agent_coder"

    # 2. List requests with filter
    list_resp = client.get(
        "/api/v1/approval/requests",
        params={"status": "pending", "repository_id": "swikarb69/AgentForge"},
    )
    assert list_resp.status_code == 200
    listed_items = list_resp.json()
    assert any(r["approval_id"] == approval_id for r in listed_items)

    # 3. Get request by ID
    get_resp = client.get(f"/api/v1/approval/requests/{approval_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["approval_id"] == approval_id

    # 4. Check readiness before approval (should be NOT_READY)
    pre_readiness = client.get(f"/api/v1/approval/requests/{approval_id}/readiness")
    assert pre_readiness.status_code == 200
    assert pre_readiness.json()["is_release_ready"] is False
    assert pre_readiness.json()["status"] == ReadinessStatus.NOT_READY.value

    # 5. Submit human decision (approve) via lead_approver
    decision_payload = {
        "actor_token": "lead_approver",
        "outcome": DecisionOutcome.APPROVED.value,
        "comments": "Code review and sandbox execution verified cleanly.",
        "current_patch_hash": VALID_HASH_A,
        "current_review_hash": VALID_HASH_B,
    }
    decide_resp = client.post(
        f"/api/v1/approval/requests/{approval_id}/decide",
        json=decision_payload,
    )
    assert decide_resp.status_code == 200
    decided_data = decide_resp.json()
    assert decided_data["status"] == ApprovalStatus.APPROVED.value
    assert len(decided_data["decisions"]) == 1

    # 6. Check readiness after approval (should be READY)
    post_readiness = client.get(f"/api/v1/approval/requests/{approval_id}/readiness")
    assert post_readiness.status_code == 200
    readiness_data = post_readiness.json()
    assert readiness_data["is_release_ready"] is True
    assert readiness_data["status"] == ReadinessStatus.READY.value
    assert len(readiness_data["blocking_reasons"]) == 0

    # 7. Check audit trail and hash-chain verification
    audit_resp = client.get(f"/api/v1/approval/requests/{approval_id}/audit")
    assert audit_resp.status_code == 200
    audit_events = audit_resp.json()
    assert len(audit_events) >= 2  # request_created, decision_submitted

    verify_resp = client.get(f"/api/v1/approval/requests/{approval_id}/audit/verify")
    assert verify_resp.status_code == 200
    verify_data = verify_resp.json()
    assert verify_data["is_valid"] is True
    assert len(verify_data["violations"]) == 0

    # 8. Execute controlled release via release_engineer
    release_payload = {
        "actor_token": "release_engineer",
        "commit_message": "feat(core): ship candidate RC-E2E-2026-001",
    }
    rel_resp = client.post(
        f"/api/v1/approval/requests/{approval_id}/release",
        json=release_payload,
    )
    assert rel_resp.status_code == 201
    rel_data = rel_resp.json()
    release_id = rel_data["release_id"]
    assert rel_data["status"] == ReleaseStatus.SUCCEEDED.value
    assert rel_data["executed_by"] == "release_engineer"
    assert rel_data["pull_request"] is not None
    assert rel_data["pull_request"]["is_merged"] is False
    assert rel_data["pull_request"]["pr_number"] > 0

    # 9. Query release by ID and list releases
    get_rel_resp = client.get(f"/api/v1/approval/releases/{release_id}")
    assert get_rel_resp.status_code == 200
    assert get_rel_resp.json()["release_id"] == release_id

    list_rel_resp = client.get(
        "/api/v1/approval/releases",
        params={"status": "succeeded"},
    )
    assert list_rel_resp.status_code == 200
    assert any(r["release_id"] == release_id for r in list_rel_resp.json())


def test_separation_of_duties_prevents_self_approval_via_api() -> None:
    """Verify requester cannot approve their own change request via API."""
    create_payload = {
        "repository_id": "swikarb69/AgentForge",
        "release_candidate_id": "RC-SOD-001",
        "artifact_binding": {
            "patch_hash": VALID_HASH_A,
            "target_branch": "main",
        },
        "actor_token": "admin_user",  # admin requesting
    }
    create_resp = client.post("/api/v1/approval/requests", json=create_payload)
    assert create_resp.status_code == 201
    approval_id = create_resp.json()["approval_id"]

    # Admin requester attempts to approve own request
    decision_payload = {
        "actor_token": "admin_user",
        "outcome": DecisionOutcome.APPROVED.value,
        "comments": "Self approval attempt",
        "current_patch_hash": VALID_HASH_A,
    }
    decide_resp = client.post(
        f"/api/v1/approval/requests/{approval_id}/decide",
        json=decision_payload,
    )
    assert decide_resp.status_code == 403
    assert "Separation of duties" in decide_resp.json()["detail"]


def test_revocation_lifecycle_blocks_subsequent_release() -> None:
    """Verify revoking approval transitions status and prevents release operations."""
    # 1. Create request
    create_payload = {
        "repository_id": "swikarb69/AgentForge",
        "release_candidate_id": "RC-REVOKE-001",
        "artifact_binding": {
            "patch_hash": VALID_HASH_A,
            "target_branch": "main",
        },
        "actor_token": "agent_coder",
    }
    resp = client.post("/api/v1/approval/requests", json=create_payload)
    approval_id = resp.json()["approval_id"]

    # 2. Approve request
    decide_payload = {
        "actor_token": "lead_approver",
        "outcome": DecisionOutcome.APPROVED.value,
        "current_patch_hash": VALID_HASH_A,
    }
    client.post(f"/api/v1/approval/requests/{approval_id}/decide", json=decide_payload)

    # 3. Revoke approval
    revoke_payload = {
        "actor_token": "lead_approver",
        "reason": "Late regression found in pre-release staging run.",
    }
    rev_resp = client.post(
        f"/api/v1/approval/requests/{approval_id}/revoke",
        json=revoke_payload,
    )
    assert rev_resp.status_code == 200
    assert rev_resp.json()["status"] == ApprovalStatus.REVOKED.value

    # 4. Attempt release on revoked candidate -> must fail
    rel_payload = {
        "actor_token": "release_engineer",
    }
    rel_fail = client.post(
        f"/api/v1/approval/requests/{approval_id}/release",
        json=rel_payload,
    )
    assert rel_fail.status_code in (400, 403)


def test_dry_run_release_via_api() -> None:
    """Verify dry_run release succeeds with simulated PR and zero side-effects."""
    create_payload = {
        "repository_id": "swikarb69/AgentForge",
        "release_candidate_id": "RC-DRY-001",
        "artifact_binding": {
            "patch_hash": VALID_HASH_A,
            "review_id": "rev_dry_01",
            "review_hash": VALID_HASH_B,
            "target_branch": "main",
        },
        "actor_token": "agent_coder",
    }
    resp = client.post("/api/v1/approval/requests", json=create_payload)
    approval_id = resp.json()["approval_id"]

    client.post(
        f"/api/v1/approval/requests/{approval_id}/decide",
        json={
            "actor_token": "lead_approver",
            "outcome": DecisionOutcome.APPROVED.value,
            "current_patch_hash": VALID_HASH_A,
        },
    )

    dry_rel_resp = client.post(
        f"/api/v1/approval/requests/{approval_id}/release",
        json={
            "actor_token": "release_engineer",
            "dry_run": True,
        },
    )
    assert dry_rel_resp.status_code == 201
    dry_data = dry_rel_resp.json()
    assert dry_data["dry_run"] is True
    assert dry_data["status"] == ReleaseStatus.SUCCEEDED.value
    assert dry_data["commit_sha"].startswith("dry_run_")
    assert dry_data["pull_request"]["pr_number"] == 999


def test_audit_chain_tamper_detection_via_api() -> None:
    """Verify verify_audit_integrity endpoint detects modified records."""
    create_payload = {
        "repository_id": "swikarb69/AgentForge",
        "release_candidate_id": "RC-TAMPER-001",
        "artifact_binding": {
            "patch_hash": VALID_HASH_A,
            "target_branch": "main",
        },
        "actor_token": "agent_coder",
    }
    resp = client.post("/api/v1/approval/requests", json=create_payload)
    approval_id = resp.json()["approval_id"]

    # Verify pristine log
    v_resp = client.get(f"/api/v1/approval/requests/{approval_id}/audit/verify")
    assert v_resp.status_code == 200
    assert v_resp.json()["is_valid"] is True

    # Tamper with the event hash of the record in storage
    events = approval_service.audit_store.get_events(approval_id)
    assert len(events) >= 1
    events[0].event_hash = "0" * 64

    # Verify API detects the tampering
    tampered_resp = client.get(f"/api/v1/approval/requests/{approval_id}/audit/verify")
    assert tampered_resp.status_code == 200
    tampered_data = tampered_resp.json()
    assert tampered_data["is_valid"] is False
    assert len(tampered_data["violations"]) >= 1
    assert "Tampering detected" in tampered_data["violations"][0]
