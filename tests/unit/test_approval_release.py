"""Unit tests for controlled release provider and release orchestration service."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from backend.app.approval.audit import AuditTrailStore
from backend.app.approval.authorization import AuthorizationEngine
from backend.app.approval.exceptions import (
    ReleaseAuthorizationError,
    ReleaseNotReadyError,
    ReleaseProviderError,
)
from backend.app.approval.identity import (
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
from backend.app.approval.readiness import ReleaseReadinessEngine
from backend.app.approval.release_models import (
    ReleaseStatus,
)
from backend.app.approval.release_provider import (
    GitHubReleaseProvider,
    MockReleaseProvider,
)
from backend.app.approval.release_service import ControlledReleaseService
from backend.app.approval.request_service import ApprovalRequestService

VALID_HASH_A = "a" * 64
VALID_HASH_B = "b" * 64
VALID_HASH_C = "c" * 64


def make_approved_request(
    approval_id: str = "appr_rel_01",
    candidate_id: str = "rc_rel_100",
    repo_id: str = "owner/repo",
    requester: str = "dev_bot",
) -> ApprovalRequest:
    """Helper creating a valid approved request for release testing."""
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
        approval_id=approval_id,
        actor_id="approver_user",
        actor_role=ApprovalRole.APPROVER,
        outcome=DecisionOutcome.APPROVED,
        associated_patch_hash=VALID_HASH_A,
        associated_review_hash=VALID_HASH_B,
    )
    return ApprovalRequest(
        approval_id=approval_id,
        repository_id=repo_id,
        release_candidate_id=candidate_id,
        requested_by=requester,
        approval_policy=policy,
        artifact_binding=binding,
        status=ApprovalStatus.APPROVED,
        risk_level=ApprovalRiskLevel.LOW,
        decisions=[decision],
    )


def make_operator(
    actor_id: str = "rel_operator",
    repo_id: str = "owner/repo",
) -> UserIdentity:
    """Helper creating an authorized release operator user."""
    return UserIdentity(
        actor_id=actor_id,
        username=actor_id,
        display_name=f"Operator {actor_id}",
        roles=[ApprovalRole.RELEASE_OPERATOR],
        repository_permissions={repo_id: [RepositoryPermission.RELEASE.value]},
    )


# ============================================================================
# MockReleaseProvider Tests
# ============================================================================


@pytest.mark.anyio
async def test_mock_provider_create_branch() -> None:
    """Verify mock provider branch creation registers branch in repo."""
    provider = MockReleaseProvider()
    sha = await provider.create_branch("my-org/my-repo", "main", "rc-branch-1")
    assert isinstance(sha, str)
    assert len(sha) == 64
    assert "rc-branch-1" in provider.branches["my-org/my-repo"]


@pytest.mark.anyio
async def test_mock_provider_commit_changes() -> None:
    """Verify mock provider records commit with expected metadata."""
    provider = MockReleaseProvider()
    sha = await provider.commit_changes(
        repository_id="my-org/my-repo",
        branch="rc-branch-1",
        patch_content="diff --git a/file b/file\n",
        commit_message="test: apply patch",
        author_name="Operator",
        author_email="op@example.com",
    )
    assert len(sha) == 64
    commits = provider.commits["my-org/my-repo"]
    assert len(commits) == 1
    assert commits[0]["message"] == "test: apply patch"


@pytest.mark.anyio
async def test_mock_provider_create_and_get_pull_request() -> None:
    """Verify PR creation produces non-merged PR and lookup works."""
    provider = MockReleaseProvider()
    pr = await provider.create_pull_request(
        repository_id="my-org/my-repo",
        title="Release Candidate 1",
        body="PR body description",
        source_branch="rc-branch-1",
        target_branch="main",
    )
    assert pr.pr_number == 101
    assert pr.is_merged is False
    assert pr.is_closed is False
    assert "101" in pr.pr_url

    fetched = await provider.get_pull_request_status("my-org/my-repo", 101)
    assert fetched.pr_number == 101
    assert fetched.title == "Release Candidate 1"


@pytest.mark.anyio
async def test_mock_provider_get_pr_not_found_raises() -> None:
    """Verify querying non-existent PR raises ReleaseProviderError."""
    provider = MockReleaseProvider()
    with pytest.raises(ReleaseProviderError, match="not found"):
        await provider.get_pull_request_status("my-org/my-repo", 999)


@pytest.mark.anyio
async def test_mock_provider_failure_modes() -> None:
    """Verify simulated failure modes trigger expected exceptions."""
    p_branch = MockReleaseProvider(failure_mode="branch_failed")
    with pytest.raises(ReleaseProviderError, match="Simulated branch creation"):
        await p_branch.create_branch("repo", "main", "rc-1")

    p_commit = MockReleaseProvider(failure_mode="commit_failed")
    with pytest.raises(ReleaseProviderError, match="Simulated commit failure"):
        await p_commit.commit_changes("repo", "rc-1", "patch", "msg", "a", "e")

    p_pr = MockReleaseProvider(failure_mode="pr_failed")
    with pytest.raises(ReleaseProviderError, match="Simulated pull request"):
        await p_pr.create_pull_request("repo", "t", "b", "rc-1", "main")


# ============================================================================
# GitHubReleaseProvider Tests
# ============================================================================


def test_github_provider_missing_token_raises() -> None:
    """Verify GitHub provider requires a non-empty token."""
    with patch.dict("os.environ", {"GITHUB_TOKEN": ""}):
        provider = GitHubReleaseProvider(token="")
        with pytest.raises(ReleaseProviderError, match="requires a valid GITHUB_TOKEN"):
            provider._ensure_token()


def test_github_provider_parse_owner_repo() -> None:
    """Verify repository owner and repo name extraction from various formats."""
    with patch.dict("os.environ", {"GITHUB_TOKEN": "mock_env_dummy_token_abc"}):
        provider = GitHubReleaseProvider()
        owner, repo = provider._parse_owner_repo("swikarb69/AgentForge")
        assert owner == "swikarb69"
        assert repo == "AgentForge"

        owner2, repo2 = provider._parse_owner_repo(
            "https://github.com/swikarb69/AgentForge.git"
        )
        assert owner2 == "swikarb69"
        assert repo2 == "AgentForge"

        with pytest.raises(ReleaseProviderError, match="Invalid GitHub repository"):
            provider._parse_owner_repo("single_name")


@pytest.mark.anyio
async def test_github_provider_mock_api_calls() -> None:
    """Verify live GitHub API calls using mock HTTP responses."""
    with patch.dict("os.environ", {"GITHUB_TOKEN": "mock_env_dummy_token_xyz"}):
        provider = GitHubReleaseProvider()

    # Mock create_branch (fetches base ref then creates new ref)
    mock_get = AsyncMock(
        return_value=httpx.Response(
            200, json={"object": {"sha": "base_sha_1234567890"}}
        )
    )
    mock_post = AsyncMock(
        return_value=httpx.Response(
            201,
            json={"ref": "refs/heads/rc-1", "object": {"sha": "base_sha_1234567890"}},
        )
    )

    with (
        patch("httpx.AsyncClient.get", mock_get),
        patch("httpx.AsyncClient.post", mock_post),
    ):
        base_sha = await provider.create_branch("owner/repo", "main", "rc-1")
        assert base_sha == "base_sha_1234567890"

    # Mock commit_changes
    commit_sha = await provider.commit_changes(
        "owner/repo", "rc-1", "patch", "commit msg", "Operator", "op@example.com"
    )
    assert len(commit_sha) == 64

    # Mock create_pull_request
    pr_json = {
        "number": 42,
        "title": "Release RC",
        "body": "PR description",
        "html_url": "https://github.com/owner/repo/pull/42",
        "merged": False,
        "state": "open",
    }
    mock_pr_post = AsyncMock(return_value=httpx.Response(201, json=pr_json))
    with patch("httpx.AsyncClient.post", mock_pr_post):
        pr = await provider.create_pull_request(
            "owner/repo", "Release RC", "PR description", "rc-1", "main"
        )
        assert pr.pr_number == 42
        assert pr.is_merged is False

    # Mock get_pull_request_status
    pr_get_json = {
        "number": 42,
        "title": "Release RC",
        "body": "PR description",
        "head": {"ref": "rc-1"},
        "base": {"ref": "main"},
        "html_url": "https://github.com/owner/repo/pull/42",
        "merged": False,
        "state": "open",
    }
    mock_pr_get = AsyncMock(return_value=httpx.Response(200, json=pr_get_json))
    with patch("httpx.AsyncClient.get", mock_pr_get):
        status = await provider.get_pull_request_status("owner/repo", 42)
        assert status.pr_number == 42
        assert status.source_branch == "rc-1"


# ============================================================================
# ControlledReleaseService Tests
# ============================================================================


@pytest.fixture
def release_setup() -> tuple[
    ControlledReleaseService,
    ApprovalRequestService,
    MockReleaseProvider,
    AuditTrailStore,
]:
    """Build controlled release service with in-memory stores and mock provider."""
    audit_store = AuditTrailStore()
    req_service = ApprovalRequestService(audit_store=audit_store)
    auth_engine = AuthorizationEngine()
    readiness_engine = ReleaseReadinessEngine()
    provider = MockReleaseProvider()
    service = ControlledReleaseService(
        request_service=req_service,
        auth_engine=auth_engine,
        readiness_engine=readiness_engine,
        release_provider=provider,
        audit_store=audit_store,
    )
    return service, req_service, provider, audit_store


@pytest.mark.anyio
async def test_controlled_release_successful_execution(
    release_setup: tuple[
        ControlledReleaseService,
        ApprovalRequestService,
        MockReleaseProvider,
        AuditTrailStore,
    ],
) -> None:
    """Verify authorized release creates candidate branch, commit, and PR."""
    service, req_service, provider, audit_store = release_setup
    req = make_approved_request()
    req_service._requests[req.approval_id] = req
    operator = make_operator()

    record = await service.execute_release(
        actor=operator,
        approval_id=req.approval_id,
        commit_message="Release patch RC-100",
    )

    assert record.status == ReleaseStatus.SUCCEEDED
    assert record.approval_id == req.approval_id
    assert record.candidate_id == req.release_candidate_id
    assert record.executed_by == operator.actor_id
    assert record.commit_sha is not None
    assert record.pull_request is not None
    assert record.pull_request.pr_number == 101
    assert record.pull_request.is_merged is False

    # Verify audit events
    events = audit_store.get_events(req.approval_id)
    actions = [e.action for e in events]
    assert "release_started" in actions
    assert "release_succeeded" in actions


@pytest.mark.anyio
async def test_controlled_release_dry_run(
    release_setup: tuple[
        ControlledReleaseService,
        ApprovalRequestService,
        MockReleaseProvider,
        AuditTrailStore,
    ],
) -> None:
    """Verify dry_run succeeds without calling external provider."""
    service, req_service, provider, audit_store = release_setup
    req = make_approved_request()
    req_service._requests[req.approval_id] = req
    operator = make_operator()

    record = await service.execute_release(
        actor=operator,
        approval_id=req.approval_id,
        dry_run=True,
    )

    assert record.status == ReleaseStatus.SUCCEEDED
    assert record.dry_run is True
    assert record.commit_sha is not None
    assert record.commit_sha.startswith("dry_run_")
    assert record.pull_request is not None
    assert record.pull_request.pr_number == 999

    # Provider should not have registered real branches
    assert req.repository_id not in provider.branches


@pytest.mark.anyio
async def test_controlled_release_readiness_enforcement(
    release_setup: tuple[
        ControlledReleaseService,
        ApprovalRequestService,
        MockReleaseProvider,
        AuditTrailStore,
    ],
) -> None:
    """Verify release blocked if approval request is not ready."""
    service, req_service, _, _ = release_setup
    req = make_approved_request()
    # Add an active blocker so readiness evaluation blocks release
    req.blocking_reasons = ["Critical vulnerability flagged in pre-release scan"]
    req_service._requests[req.approval_id] = req
    operator = make_operator()

    with pytest.raises(ReleaseNotReadyError, match="is not release ready"):
        await service.execute_release(actor=operator, approval_id=req.approval_id)


@pytest.mark.anyio
async def test_controlled_release_authorization_enforcement(
    release_setup: tuple[
        ControlledReleaseService,
        ApprovalRequestService,
        MockReleaseProvider,
        AuditTrailStore,
    ],
) -> None:
    """Verify release rejected if actor lacks release role or permission."""
    service, req_service, _, _ = release_setup
    req = make_approved_request()
    req_service._requests[req.approval_id] = req

    unauthorized_actor = UserIdentity(
        actor_id="guest_dev",
        username="guest_dev",
        display_name="Guest Dev",
        roles=[ApprovalRole.REQUESTER],
        repository_permissions={"owner/repo": [RepositoryPermission.READ.value]},
    )

    with pytest.raises(ReleaseAuthorizationError, match="authorization rejected"):
        await service.execute_release(
            actor=unauthorized_actor,
            approval_id=req.approval_id,
        )


@pytest.mark.anyio
async def test_controlled_release_separation_of_duties(
    release_setup: tuple[
        ControlledReleaseService,
        ApprovalRequestService,
        MockReleaseProvider,
        AuditTrailStore,
    ],
) -> None:
    """Verify requester cannot trigger release of their own candidate."""
    service, req_service, _, _ = release_setup
    req = make_approved_request(requester="lead_operator")
    req_service._requests[req.approval_id] = req

    # Requester has operator role, but requested the change
    requester_operator = UserIdentity(
        actor_id="lead_operator",
        username="lead_operator",
        display_name="Lead Operator",
        roles=[ApprovalRole.RELEASE_OPERATOR],
        repository_permissions={"owner/repo": [RepositoryPermission.RELEASE.value]},
    )

    with pytest.raises(
        ReleaseAuthorizationError, match="cannot authorize release of their own"
    ):
        await service.execute_release(
            actor=requester_operator,
            approval_id=req.approval_id,
        )


@pytest.mark.anyio
async def test_controlled_release_idempotency(
    release_setup: tuple[
        ControlledReleaseService,
        ApprovalRequestService,
        MockReleaseProvider,
        AuditTrailStore,
    ],
) -> None:
    """Verify duplicate release execution returns existing record."""
    service, req_service, _, _ = release_setup
    req = make_approved_request()
    req_service._requests[req.approval_id] = req
    operator = make_operator()

    record1 = await service.execute_release(actor=operator, approval_id=req.approval_id)
    record2 = await service.execute_release(actor=operator, approval_id=req.approval_id)

    assert record1.release_id == record2.release_id
    assert record1.commit_sha == record2.commit_sha


@pytest.mark.anyio
async def test_controlled_release_provider_failure_handling(
    release_setup: tuple[
        ControlledReleaseService,
        ApprovalRequestService,
        MockReleaseProvider,
        AuditTrailStore,
    ],
) -> None:
    """Verify provider failures mark record FAILED and log audit event."""
    _, req_service, _, audit_store = release_setup
    failing_provider = MockReleaseProvider(failure_mode="pr_failed")
    service = ControlledReleaseService(
        request_service=req_service,
        release_provider=failing_provider,
        audit_store=audit_store,
    )
    req = make_approved_request()
    req_service._requests[req.approval_id] = req
    operator = make_operator()

    with pytest.raises(ReleaseProviderError, match="Simulated pull request"):
        await service.execute_release(actor=operator, approval_id=req.approval_id)

    # Check that failed release record was stored and audit event recorded
    releases = service.list_releases(status=ReleaseStatus.FAILED)
    assert len(releases) == 1
    assert releases[0].status == ReleaseStatus.FAILED
    assert "Simulated pull request" in (releases[0].error_message or "")

    events = audit_store.get_events(req.approval_id)
    actions = [e.action for e in events]
    assert "release_failed" in actions


@pytest.mark.anyio
async def test_get_and_list_releases(
    release_setup: tuple[
        ControlledReleaseService,
        ApprovalRequestService,
        MockReleaseProvider,
        AuditTrailStore,
    ],
) -> None:
    """Verify retrieval by ID and filtering by repository or status."""
    service, req_service, _, _ = release_setup
    req1 = make_approved_request(approval_id="appr_01", candidate_id="rc_01")
    req2 = make_approved_request(
        approval_id="appr_02", candidate_id="rc_02", repo_id="other/repo"
    )
    req_service._requests[req1.approval_id] = req1
    req_service._requests[req2.approval_id] = req2

    op1 = make_operator(repo_id="owner/repo")
    op2 = make_operator(actor_id="other_op", repo_id="other/repo")

    rec1 = await service.execute_release(actor=op1, approval_id="appr_01")
    rec2 = await service.execute_release(actor=op2, approval_id="appr_02")

    assert service.get_release(rec1.release_id) == rec1
    assert service.get_release(rec2.release_id) == rec2
    assert service.get_release("non_existent") is None

    filtered_repo = service.list_releases(repository_id="owner/repo")
    assert len(filtered_repo) == 1
    assert filtered_repo[0].release_id == rec1.release_id

    filtered_status = service.list_releases(status=ReleaseStatus.SUCCEEDED)
    assert len(filtered_status) == 2


def test_release_service_provider_selection_logic(
    release_setup: tuple[
        ControlledReleaseService,
        ApprovalRequestService,
        MockReleaseProvider,
        AuditTrailStore,
    ],
) -> None:
    """Verify release services choose provider based on GITHUB_TOKEN."""
    from pydantic import SecretStr

    from backend.app.approval.release_provider import (
        GitHubReleaseProvider,
        MockReleaseProvider,
    )
    from backend.app.approval.release_service import ControlledReleaseService
    from backend.app.approval.service import ApprovalService
    from backend.app.core.config import settings

    _, req_service, _, _ = release_setup
    orig_token = settings.GITHUB_TOKEN
    test_gh_val = "ghp_test_token_1234567890"  # noqa: S105

    try:
        # 1. When GITHUB_TOKEN is None, defaults to MockReleaseProvider
        settings.GITHUB_TOKEN = None
        s1 = ControlledReleaseService(request_service=req_service)
        assert isinstance(s1.release_provider, MockReleaseProvider)

        appr1 = ApprovalService()
        assert isinstance(appr1.release_provider, MockReleaseProvider)

        # 2. When GITHUB_TOKEN is set, defaults to GitHubReleaseProvider
        settings.GITHUB_TOKEN = SecretStr(test_gh_val)
        s2 = ControlledReleaseService(request_service=req_service)
        assert isinstance(s2.release_provider, GitHubReleaseProvider)
        assert s2.release_provider.token == test_gh_val

        appr2 = ApprovalService()
        assert isinstance(appr2.release_provider, GitHubReleaseProvider)
        assert appr2.release_provider.token == test_gh_val

        # 3. GitHubReleaseProvider reads settings when token is None
        gh_provider = GitHubReleaseProvider()
        assert gh_provider.token == test_gh_val

    finally:
        settings.GITHUB_TOKEN = orig_token
