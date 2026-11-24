"""Unified service orchestrating Human Approval & Controlled Release."""

from pathlib import Path
from typing import Any

from backend.app.approval.audit import (
    AuditEventRecord,
    AuditTrailStore,
)
from backend.app.approval.authorization import AuthorizationEngine
from backend.app.approval.decision_service import ApprovalDecisionService
from backend.app.approval.expiration import ExpirationManager
from backend.app.approval.identity import (
    IdentityProvider,
    LocalTestIdentityProvider,
    UserIdentity,
)
from backend.app.approval.models import (
    ApprovalPolicy,
    ApprovalRequest,
    ApprovalRiskLevel,
    ApprovalStatus,
    ArtifactBinding,
    DecisionOutcome,
)
from backend.app.approval.readiness import (
    ReleaseReadinessEngine,
    ReleaseReadinessReport,
)
from backend.app.approval.release_models import (
    ReleaseExecutionRecord,
    ReleaseStatus,
)
from backend.app.approval.release_provider import (
    GitHubReleaseProvider,
    MockReleaseProvider,
    ReleaseProvider,
)
from backend.app.approval.release_service import ControlledReleaseService
from backend.app.approval.request_service import ApprovalRequestService
from backend.app.approval.state_machine import ApprovalStateMachine
from backend.app.core.config import settings


class ApprovalService:
    """Unified facade for requests, decisions, readiness, and release."""

    def __init__(
        self,
        identity_provider: IdentityProvider | None = None,
        auth_engine: AuthorizationEngine | None = None,
        audit_store: AuditTrailStore | None = None,
        state_machine: ApprovalStateMachine | None = None,
        expiration_manager: ExpirationManager | None = None,
        request_service: ApprovalRequestService | None = None,
        decision_service: ApprovalDecisionService | None = None,
        readiness_engine: ReleaseReadinessEngine | None = None,
        release_provider: ReleaseProvider | None = None,
        release_service: ControlledReleaseService | None = None,
    ) -> None:
        self.identity_provider = identity_provider or LocalTestIdentityProvider(
            populate_defaults=True
        )
        self.auth_engine = auth_engine or AuthorizationEngine()
        self.audit_store = audit_store or AuditTrailStore()
        self.state_machine = state_machine or ApprovalStateMachine()
        self.expiration_manager = expiration_manager or ExpirationManager(
            state_machine=self.state_machine,
            audit_store=self.audit_store,
        )
        self.request_service = request_service or ApprovalRequestService(
            auth_engine=self.auth_engine,
            audit_store=self.audit_store,
            state_machine=self.state_machine,
            expiration_manager=self.expiration_manager,
        )
        self.decision_service = decision_service or ApprovalDecisionService(
            request_service=self.request_service,
            auth_engine=self.auth_engine,
            state_machine=self.state_machine,
            audit_store=self.audit_store,
        )
        self.readiness_engine = readiness_engine or ReleaseReadinessEngine()
        if release_provider is not None:
            self.release_provider = release_provider
        elif release_service is not None:
            self.release_provider = release_service.release_provider
        elif settings.GITHUB_TOKEN and settings.GITHUB_TOKEN.get_secret_value().strip():
            self.release_provider = GitHubReleaseProvider()
        else:
            self.release_provider = MockReleaseProvider()
        self.release_service = release_service or ControlledReleaseService(
            request_service=self.request_service,
            auth_engine=self.auth_engine,
            readiness_engine=self.readiness_engine,
            release_provider=self.release_provider,
            audit_store=self.audit_store,
        )

    async def authenticate_actor(self, token_or_id: str) -> UserIdentity:
        """Authenticate actor credentials against the configured identity provider."""
        return await self.identity_provider.authenticate(token_or_id)

    def create_approval_request(
        self,
        actor: UserIdentity,
        repository_id: str,
        release_candidate_id: str,
        artifact_binding: ArtifactBinding,
        policy: ApprovalPolicy | None = None,
        risk_level: ApprovalRiskLevel | None = None,
        issue_id: str | None = None,
        plan_id: str | None = None,
        generation_id: str | None = None,
        execution_id: str | None = None,
        review_id: str | None = None,
        repair_id: str | None = None,
        blocking_reasons: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ApprovalRequest:
        """Create and register a new human approval request for a release candidate."""
        return self.request_service.create_request(
            actor=actor,
            repository_id=repository_id,
            release_candidate_id=release_candidate_id,
            artifact_binding=artifact_binding,
            policy=policy,
            risk_level=risk_level,
            issue_id=issue_id,
            plan_id=plan_id,
            generation_id=generation_id,
            execution_id=execution_id,
            review_id=review_id,
            repair_id=repair_id,
            blocking_reasons=blocking_reasons,
            metadata=metadata,
        )

    def get_approval_request(self, approval_id: str) -> ApprovalRequest:
        """Fetch approval request with on-read expiration check."""
        return self.request_service.get_request(approval_id)

    def list_approval_requests(
        self,
        repository_id: str | None = None,
        candidate_id: str | None = None,
        status_filter: str | None = None,
    ) -> list[ApprovalRequest]:
        """List active approval requests filtered by repository or status."""
        st = ApprovalStatus(status_filter) if status_filter else None
        results = self.request_service.list_requests(
            repository_id=repository_id,
            status=st,
        )
        if candidate_id:
            results = [r for r in results if r.release_candidate_id == candidate_id]
        return results

    def submit_decision(
        self,
        actor: UserIdentity,
        approval_id: str,
        outcome: DecisionOutcome,
        comments: str | None = None,
        current_patch_hash: str | None = None,
        current_review_hash: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ApprovalRequest:
        """Submit an authorized human approval or rejection decision."""
        return self.decision_service.submit_decision(
            actor=actor,
            approval_id=approval_id,
            outcome=outcome,
            reason=comments,
            associated_patch_hash=current_patch_hash,
            associated_review_hash=current_review_hash,
            metadata=metadata,
        )

    def revoke_approval(
        self,
        actor: UserIdentity,
        approval_id: str,
        reason: str,
    ) -> ApprovalRequest:
        """Revoke a previously granted approval due to new findings or risks."""
        return self.decision_service.revoke_approval(
            actor=actor,
            approval_id=approval_id,
            reason=reason,
        )

    def evaluate_readiness(
        self,
        approval_id: str,
        repository_path: str | Path | None = None,
        plan: Any = None,
        generation_result: Any = None,
        verification_report: Any = None,
        review_report: Any = None,
        repair_session: Any = None,
        current_patch_hash: str | None = None,
        current_review_hash: str | None = None,
    ) -> ReleaseReadinessReport:
        """Evaluate complete release readiness criteria across all 13 checks."""
        request = self.get_approval_request(approval_id)
        return self.readiness_engine.evaluate(
            request=request,
            repository_path=repository_path,
            plan=plan,
            generation_result=generation_result,
            verification_report=verification_report,
            review_report=review_report,
            repair_session=repair_session,
            current_patch_hash=current_patch_hash,
            current_review_hash=current_review_hash,
        )

    async def execute_release(
        self,
        actor: UserIdentity,
        approval_id: str,
        dry_run: bool = False,
        patch_content: str | None = None,
        commit_message: str | None = None,
        custom_branch_name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ReleaseExecutionRecord:
        """Authorize and execute release operation creating candidate branch and PR."""
        return await self.release_service.execute_release(
            actor=actor,
            approval_id=approval_id,
            dry_run=dry_run,
            patch_content=patch_content,
            commit_message=commit_message,
            custom_branch_name=custom_branch_name,
            metadata=metadata,
        )

    def get_release(self, release_id: str) -> ReleaseExecutionRecord | None:
        """Fetch release execution record by ID."""
        return self.release_service.get_release(release_id)

    def list_releases(
        self,
        repository_id: str | None = None,
        status: ReleaseStatus | None = None,
    ) -> list[ReleaseExecutionRecord]:
        """List recorded releases matching filter criteria."""
        return self.release_service.list_releases(
            repository_id=repository_id,
            status=status,
        )

    def get_audit_trail(self, approval_id: str) -> list[AuditEventRecord]:
        """Retrieve complete chronological audit events for an approval request."""
        return self.audit_store.get_events(approval_id)

    def verify_audit_integrity(self, approval_id: str) -> tuple[bool, list[str]]:
        """Verify SHA-256 hash-chain integrity of audit log for an approval request."""
        return self.audit_store.verify_integrity(approval_id)
