"""Unit tests for approval identity abstraction and authorization engine."""

import pytest

from backend.app.approval.authorization import AuthorizationEngine
from backend.app.approval.exceptions import (
    ApprovalPolicyViolationError,
    ApprovalValidationError,
    ConflictingDecisionError,
    IdentityVerificationError,
    RepositoryPermissionError,
    SeparationOfDutiesError,
    UnauthorizedApproverError,
)
from backend.app.approval.identity import (
    LocalTestIdentityProvider,
    ProductionIdentityProvider,
    RepositoryPermission,
    UserIdentity,
)
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

VALID_HASH_A = "a" * 64
VALID_HASH_B = "b" * 64


def make_test_request(
    requested_by: str = "agent_coder",
    status: ApprovalStatus = ApprovalStatus.PENDING,
    allow_self_approval: bool = False,
    decisions: list[ApprovalDecision] | None = None,
) -> ApprovalRequest:
    """Helper creating a standard test ApprovalRequest."""
    policy = ApprovalPolicy(
        policy_id="default",
        minimum_approvers=1,
        required_roles=[ApprovalRole.APPROVER],
        separation_of_duties=True,
        allow_self_approval=allow_self_approval,
    )
    binding = ArtifactBinding(
        patch_hash=VALID_HASH_A,
        review_hash=VALID_HASH_B,
        target_branch="main",
    )
    return ApprovalRequest(
        approval_id="appr_test_01",
        repository_id="repo_corp",
        release_candidate_id="rc_01",
        requested_by=requested_by,
        approval_policy=policy,
        artifact_binding=binding,
        status=status,
        risk_level=ApprovalRiskLevel.LOW,
        decisions=decisions or [],
    )


# ==============================================================================
# UserIdentity Tests
# ==============================================================================


def test_user_identity_validation() -> None:
    """Test UserIdentity validation and non-empty constraints."""
    user = UserIdentity(
        actor_id="usr_01",
        username="alice",
        display_name="Alice Smith",
        roles=[ApprovalRole.APPROVER],
    )
    assert user.actor_id == "usr_01"
    assert user.has_role(ApprovalRole.APPROVER) is True
    assert user.has_role(ApprovalRole.ADMIN) is False

    with pytest.raises(ApprovalValidationError, match="must not be empty"):
        UserIdentity(actor_id="", username="alice", display_name="Alice")

    with pytest.raises(ApprovalValidationError, match="must not be empty"):
        UserIdentity(actor_id="usr_01", username="   ", display_name="Alice")


def test_user_identity_admin_inherent_roles() -> None:
    """Test that ADMIN role satisfies any role inquiry."""
    admin = UserIdentity(
        actor_id="admin_01",
        username="admin",
        display_name="Administrator",
        roles=[ApprovalRole.ADMIN],
    )
    assert admin.has_role(ApprovalRole.APPROVER) is True
    assert admin.has_role(ApprovalRole.REQUESTER) is True
    assert admin.has_role(ApprovalRole.RELEASE_OPERATOR) is True
    assert admin.has_role(ApprovalRole.REVIEWER) is True


def test_user_identity_repo_permissions_and_hierarchy() -> None:
    """Test repository-level permission hierarchy and wildcard support."""
    # Write permission implies triage and read
    writer = UserIdentity(
        actor_id="dev_01",
        username="dev",
        display_name="Developer",
        roles=[ApprovalRole.REQUESTER],
        repository_permissions={"repo_corp": [RepositoryPermission.WRITE.value]},
    )
    assert writer.has_repo_permission("repo_corp", RepositoryPermission.WRITE) is True
    assert writer.has_repo_permission("repo_corp", RepositoryPermission.TRIAGE) is True
    assert writer.has_repo_permission("repo_corp", RepositoryPermission.READ) is True
    assert (
        writer.has_repo_permission("repo_corp", RepositoryPermission.APPROVE) is False
    )
    assert writer.has_repo_permission("other_repo", RepositoryPermission.READ) is False

    # Wildcard permissions
    lead = UserIdentity(
        actor_id="lead_01",
        username="lead",
        display_name="Tech Lead",
        roles=[ApprovalRole.APPROVER],
        repository_permissions={"*": [RepositoryPermission.APPROVE.value]},
    )
    assert lead.has_repo_permission("any_repo_x", RepositoryPermission.APPROVE) is True
    assert lead.has_repo_permission("any_repo_x", RepositoryPermission.READ) is True
    assert lead.has_repo_permission("any_repo_x", RepositoryPermission.WRITE) is False


# ==============================================================================
# IdentityProvider Tests
# ==============================================================================


@pytest.mark.anyio
async def test_local_test_identity_provider_defaults() -> None:
    """Test default seeded users in LocalTestIdentityProvider."""
    provider = LocalTestIdentityProvider(populate_defaults=True)

    # Authenticate via mock token
    admin = await provider.authenticate("mock_token_admin")
    assert admin.actor_id == "admin_user"
    assert admin.has_role(ApprovalRole.ADMIN) is True

    # Authenticate via actor_id
    approver = await provider.authenticate("lead_approver")
    assert approver.actor_id == "lead_approver"
    assert approver.has_role(ApprovalRole.APPROVER) is True

    # Retrieve via get_identity
    retrieved = await provider.get_identity("release_engineer")
    assert retrieved is not None
    assert retrieved.has_role(ApprovalRole.RELEASE_OPERATOR) is True

    assert await provider.get_identity("unknown_actor") is None


@pytest.mark.anyio
async def test_local_test_identity_provider_custom_registration() -> None:
    """Test custom user registration in LocalTestIdentityProvider."""
    provider = LocalTestIdentityProvider(populate_defaults=False)
    custom = UserIdentity(
        actor_id="custom_01",
        username="custom_user",
        display_name="Custom User",
        roles=[ApprovalRole.REVIEWER],
    )
    provider.register_user(custom, "mock_token_custom_abc")

    authenticated = await provider.authenticate("mock_token_custom_abc")
    assert authenticated.actor_id == "custom_01"


@pytest.mark.anyio
async def test_local_test_identity_provider_invalid_tokens() -> None:
    """Test authentication errors on empty or unknown credentials."""
    provider = LocalTestIdentityProvider(populate_defaults=True)

    with pytest.raises(IdentityVerificationError, match="Empty token"):
        await provider.authenticate("   ")

    with pytest.raises(IdentityVerificationError, match="unknown actor"):
        await provider.authenticate("invalid_nonexistent_token")


@pytest.mark.anyio
async def test_production_identity_provider_explicitly_unconfigured() -> None:
    """Test that ProductionIdentityProvider safely rejects requests."""
    prod_provider = ProductionIdentityProvider()

    with pytest.raises(IdentityVerificationError, match="explicitly unconfigured"):
        await prod_provider.authenticate("any_token")

    with pytest.raises(IdentityVerificationError, match="directory is unconfigured"):
        await prod_provider.get_identity("any_actor")


# ==============================================================================
# AuthorizationEngine Tests
# ==============================================================================


def test_authorization_check_can_request_approval() -> None:
    """Test authorization checks for initiating approval requests."""
    engine = AuthorizationEngine()

    authorized_requester = UserIdentity(
        actor_id="coder_bot",
        username="coder",
        display_name="Coder Bot",
        roles=[ApprovalRole.REQUESTER],
        repository_permissions={"repo_corp": [RepositoryPermission.WRITE.value]},
    )
    dec = engine.check_can_request_approval(authorized_requester, "repo_corp")
    assert dec.allowed is True
    engine.enforce_can_request_approval(authorized_requester, "repo_corp")

    unauthenticated_user = UserIdentity(
        actor_id="guest_01",
        username="guest",
        display_name="Guest",
        is_authenticated=False,
    )
    with pytest.raises(IdentityVerificationError, match="not authenticated"):
        engine.enforce_can_request_approval(unauthenticated_user, "repo_corp")

    unauthorized_reader = UserIdentity(
        actor_id="reader_01",
        username="reader",
        display_name="Reader",
        roles=[],
        repository_permissions={"repo_corp": [RepositoryPermission.READ.value]},
    )
    with pytest.raises(RepositoryPermissionError, match="lacks write permission"):
        engine.enforce_can_request_approval(unauthorized_reader, "repo_corp")


def test_authorization_separation_of_duties_prevents_self_approval() -> None:
    """Test that a requester cannot approve their own change request."""
    engine = AuthorizationEngine()
    req = make_test_request(requested_by="alice")

    alice = UserIdentity(
        actor_id="alice",
        username="alice_dev",
        display_name="Alice Developer",
        roles=[ApprovalRole.APPROVER],
        repository_permissions={"repo_corp": [RepositoryPermission.APPROVE.value]},
    )
    decision = engine.check_can_decide(alice, req, DecisionOutcome.APPROVED)
    assert decision.allowed is False
    assert "Separation of duties" in decision.reason

    with pytest.raises(SeparationOfDutiesError, match="Separation of duties"):
        engine.enforce_can_decide(alice, req, DecisionOutcome.APPROVED)


def test_authorization_self_approval_allowed_when_policy_permits() -> None:
    """Test self-approval is permitted when policy explicitly enables it."""
    engine = AuthorizationEngine()
    req = make_test_request(requested_by="solo_dev", allow_self_approval=True)

    solo = UserIdentity(
        actor_id="solo_dev",
        username="solo",
        display_name="Solo Developer",
        roles=[ApprovalRole.APPROVER],
        repository_permissions={"repo_corp": [RepositoryPermission.APPROVE.value]},
    )
    decision = engine.check_can_decide(solo, req, DecisionOutcome.APPROVED)
    assert decision.allowed is True
    engine.enforce_can_decide(solo, req, DecisionOutcome.APPROVED)


def test_authorization_role_requirements_and_repo_permissions() -> None:
    """Test role requirements and repository permission enforcement on approval."""
    engine = AuthorizationEngine()
    req = make_test_request(requested_by="alice")

    # User has APPROVE role but lacks APPROVE repo permission
    approver_no_repo = UserIdentity(
        actor_id="bob",
        username="bob",
        display_name="Bob Reviewer",
        roles=[ApprovalRole.APPROVER],
        repository_permissions={"repo_corp": [RepositoryPermission.READ.value]},
    )
    with pytest.raises(RepositoryPermissionError, match="lacks 'approve' permission"):
        engine.enforce_can_decide(approver_no_repo, req, DecisionOutcome.APPROVED)

    # User has repo APPROVE permission but lacks APPROVER role
    dev_with_repo = UserIdentity(
        actor_id="charlie",
        username="charlie",
        display_name="Charlie Dev",
        roles=[ApprovalRole.REQUESTER],
        repository_permissions={"repo_corp": [RepositoryPermission.APPROVE.value]},
    )
    with pytest.raises(UnauthorizedApproverError, match="lacks required role"):
        engine.enforce_can_decide(dev_with_repo, req, DecisionOutcome.APPROVED)

    # Fully authorized approver
    valid_approver = UserIdentity(
        actor_id="dave",
        username="dave",
        display_name="Dave Approver",
        roles=[ApprovalRole.APPROVER],
        repository_permissions={"repo_corp": [RepositoryPermission.APPROVE.value]},
    )
    engine.enforce_can_decide(valid_approver, req, DecisionOutcome.APPROVED)


def test_authorization_duplicate_decision_rejection() -> None:
    """Test rejection of duplicate decision by the same actor."""
    engine = AuthorizationEngine()
    prior_decision = ApprovalDecision(
        decision_id="dec_01",
        approval_id="appr_test_01",
        actor_id="dave",
        actor_role=ApprovalRole.APPROVER,
        outcome=DecisionOutcome.APPROVED,
    )
    req = make_test_request(requested_by="alice", decisions=[prior_decision])

    dave = UserIdentity(
        actor_id="dave",
        username="dave",
        display_name="Dave Approver",
        roles=[ApprovalRole.APPROVER],
        repository_permissions={"repo_corp": [RepositoryPermission.APPROVE.value]},
    )
    decision = engine.check_can_decide(dave, req, DecisionOutcome.APPROVED)
    assert decision.allowed is False
    assert "already submitted an identical decision" in decision.reason

    with pytest.raises(ConflictingDecisionError, match="identical decision"):
        engine.enforce_can_decide(dave, req, DecisionOutcome.APPROVED)


def test_authorization_check_can_revoke() -> None:
    """Test authorization rules for revoking approval."""
    engine = AuthorizationEngine()
    prior_decision = ApprovalDecision(
        decision_id="dec_01",
        approval_id="appr_test_01",
        actor_id="approver_dave",
        actor_role=ApprovalRole.APPROVER,
        outcome=DecisionOutcome.APPROVED,
    )
    req = make_test_request(
        requested_by="alice",
        status=ApprovalStatus.APPROVED,
        decisions=[prior_decision],
    )

    # Prior approver can revoke
    approver_dave = UserIdentity(
        actor_id="approver_dave",
        username="dave",
        display_name="Dave Approver",
        roles=[ApprovalRole.APPROVER],
    )
    engine.enforce_can_revoke(approver_dave, req)

    # Administrator can revoke
    admin = UserIdentity(
        actor_id="admin_01",
        username="admin",
        display_name="Admin",
        roles=[ApprovalRole.ADMIN],
    )
    engine.enforce_can_revoke(admin, req)

    # Random unauthorized actor cannot revoke
    rando = UserIdentity(
        actor_id="rando_01",
        username="rando",
        display_name="Rando",
        roles=[ApprovalRole.REVIEWER],
    )
    with pytest.raises(UnauthorizedApproverError, match="not an administrator"):
        engine.enforce_can_revoke(rando, req)


def test_authorization_check_can_release() -> None:
    """Test release authorization requirements and separation of duties."""
    engine = AuthorizationEngine()

    # 1. Unapproved candidate cannot release
    pending_req = make_test_request(requested_by="alice", status=ApprovalStatus.PENDING)
    rel_operator = UserIdentity(
        actor_id="operator_01",
        username="ops",
        display_name="Ops Engineer",
        roles=[ApprovalRole.RELEASE_OPERATOR],
        repository_permissions={"repo_corp": [RepositoryPermission.RELEASE.value]},
    )
    with pytest.raises(ApprovalPolicyViolationError, match="not 'approved'"):
        engine.enforce_can_release(rel_operator, pending_req)

    # 2. Approved candidate with valid release operator succeeds
    approved_req = make_test_request(
        requested_by="alice", status=ApprovalStatus.APPROVED
    )
    engine.enforce_can_release(rel_operator, approved_req)

    # 3. Separation of duties: requester cannot release own approved change
    alice_with_ops_role = UserIdentity(
        actor_id="alice",
        username="alice",
        display_name="Alice (Lead & Ops)",
        roles=[ApprovalRole.RELEASE_OPERATOR],
        repository_permissions={"repo_corp": [RepositoryPermission.RELEASE.value]},
    )
    with pytest.raises(SeparationOfDutiesError, match="Separation of duties"):
        engine.enforce_can_release(alice_with_ops_role, approved_req)

    # 4. Operator lacking repo release permission fails
    ops_no_repo = UserIdentity(
        actor_id="ops_no_repo",
        username="ops2",
        display_name="Ops 2",
        roles=[ApprovalRole.RELEASE_OPERATOR],
        repository_permissions={"repo_corp": [RepositoryPermission.READ.value]},
    )
    with pytest.raises(RepositoryPermissionError, match="lacks release permission"):
        engine.enforce_can_release(ops_no_repo, approved_req)
