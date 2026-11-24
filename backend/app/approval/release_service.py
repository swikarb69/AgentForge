"""Controlled release service orchestrating authorized shipping and PR creation."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from backend.app.approval.audit import AuditTrailStore
from backend.app.approval.authorization import AuthorizationEngine
from backend.app.approval.exceptions import (
    ReleaseAuthorizationError,
    ReleaseNotReadyError,
    ReleaseProviderError,
)
from backend.app.approval.identity import UserIdentity
from backend.app.approval.models import ApprovalRequest, ApprovalStatus
from backend.app.approval.readiness import (
    ReleaseReadinessEngine,
    ReleaseReadinessReport,
)
from backend.app.approval.release_models import (
    PullRequestMetadata,
    ReleaseExecutionRecord,
    ReleaseStatus,
)
from backend.app.approval.release_provider import (
    GitHubReleaseProvider,
    MockReleaseProvider,
    ReleaseProvider,
)
from backend.app.approval.request_service import ApprovalRequestService
from backend.app.core.config import settings


class ControlledReleaseService:
    """Orchestrates authorized, verified release operations and PR creation."""

    def __init__(
        self,
        request_service: ApprovalRequestService,
        auth_engine: AuthorizationEngine | None = None,
        readiness_engine: ReleaseReadinessEngine | None = None,
        release_provider: ReleaseProvider | None = None,
        audit_store: AuditTrailStore | None = None,
    ) -> None:
        self.request_service = request_service
        self.auth_engine = auth_engine or AuthorizationEngine()
        self.readiness_engine = readiness_engine or ReleaseReadinessEngine()
        if release_provider is not None:
            self.release_provider = release_provider
        elif settings.GITHUB_TOKEN and settings.GITHUB_TOKEN.get_secret_value().strip():
            self.release_provider = GitHubReleaseProvider()
        else:
            self.release_provider = MockReleaseProvider()
        self.audit_store = audit_store or request_service.audit_store
        self._releases: dict[str, ReleaseExecutionRecord] = {}
        self._releases_by_candidate: dict[str, str] = {}

    def _format_pull_request_body(
        self,
        request: ApprovalRequest,
        readiness_report: ReleaseReadinessReport,
        commit_sha: str,
    ) -> str:
        """Construct detailed, auditable pull request markdown body."""
        binding = request.artifact_binding
        return (
            "## AgentForge Controlled Release Candidate\n\n"
            f"**Candidate ID:** `{request.release_candidate_id}`\n"
            f"**Approval Request:** `{request.approval_id}`\n"
            f"**Commit SHA:** `{commit_sha}`\n"
            f"**Target Branch:** `{binding.target_branch}`\n\n"
            "### Verification & Audit Summary\n"
            f"- **Readiness Verdict:** `{readiness_report.status.value.upper()}`\n"
            f"- **Approved by:** {len(request.decisions)} reviewer(s)\n"
            f"- **Patch Hash (SHA-256):** `{binding.patch_hash}`\n"
            f"- **Review Hash (SHA-256):** `{binding.review_hash or 'N/A'}`\n\n"
            "### Upstream Traceability\n"
            f"- **Plan ID:** `{request.plan_id or 'N/A'}`\n"
            f"- **Generation ID:** `{request.generation_id or 'N/A'}`\n"
            f"- **Execution ID:** `{request.execution_id or 'N/A'}`\n"
            f"- **Review ID:** `{request.review_id or 'N/A'}`\n\n"
            "---\n"
            "*Notice: AgentForge NEVER automatically merges into default branches. "
            "Human review is required before merging.*\n"
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
        """Authorize and execute release operation, creating candidate branch and PR."""
        request = self.request_service.get_request(approval_id)

        # 1. Enforce actor authorization
        try:
            self.auth_engine.enforce_can_release(actor, request)
        except Exception as err:
            raise ReleaseAuthorizationError(
                f"Release authorization rejected: {err}"
            ) from err

        # 2. Check idempotency: avoid duplicate releases for the same candidate
        candidate_id = request.release_candidate_id
        if candidate_id in self._releases_by_candidate:
            prior_id = self._releases_by_candidate[candidate_id]
            prior_rec = self._releases.get(prior_id)
            if prior_rec and prior_rec.status == ReleaseStatus.SUCCEEDED:
                return prior_rec

        # 3. Validate release readiness
        readiness = self.readiness_engine.evaluate(request)
        if not readiness.is_release_ready:
            blockers = "; ".join(readiness.blocking_reasons)
            msg = (
                f"Candidate '{candidate_id}' is not release ready "
                f"(status: '{readiness.status.value}'). Blockers: {blockers}"
            )
            raise ReleaseNotReadyError(msg)

        # 4. Generate unique release ID and release branch name
        release_id = f"rel_{uuid4().hex[:12]}"
        short_id = candidate_id[:8] if len(candidate_id) > 8 else candidate_id
        generated_branch = f"agentforge/rc-{short_id}-{uuid4().hex[:6]}"
        release_branch = custom_branch_name or generated_branch
        target_branch = request.artifact_binding.target_branch

        start_time = datetime.now(UTC).isoformat()
        record = ReleaseExecutionRecord(
            release_id=release_id,
            candidate_id=candidate_id,
            approval_id=approval_id,
            repository_id=request.repository_id,
            target_branch=target_branch,
            release_branch=release_branch,
            status=ReleaseStatus.IN_PROGRESS,
            dry_run=dry_run,
            executed_by=actor.actor_id,
            started_at=start_time,
            metadata=metadata or {},
        )
        self._releases[release_id] = record
        self._releases_by_candidate[candidate_id] = release_id

        # 5. Record audit event: release started
        self.audit_store.append_event(
            approval_id=approval_id,
            actor_id=actor.actor_id,
            action="release_started",
            previous_state=ApprovalStatus.APPROVED.value,
            new_state=ReleaseStatus.IN_PROGRESS.value,
            reason=f"Controlled release initiated (dry_run={dry_run})",
            correlation_id=release_id,
            metadata={
                "candidate_id": candidate_id,
                "release_branch": release_branch,
                "target_branch": target_branch,
            },
        )

        # 6. Handle dry run mode
        if dry_run:
            simulated_sha = f"dry_run_{uuid4().hex[:16]}"
            simulated_pr = PullRequestMetadata(
                pr_number=999,
                title=f"Release Candidate: {candidate_id}",
                body=self._format_pull_request_body(request, readiness, simulated_sha),
                source_branch=release_branch,
                target_branch=target_branch,
                pr_url=f"https://github.com/simulated/{request.repository_id}/pull/999",
                is_merged=False,
                is_closed=False,
            )
            record.status = ReleaseStatus.SUCCEEDED
            record.commit_sha = simulated_sha
            record.pull_request = simulated_pr
            record.completed_at = datetime.now(UTC).isoformat()

            self.audit_store.append_event(
                approval_id=approval_id,
                actor_id=actor.actor_id,
                action="release_succeeded",
                previous_state=ReleaseStatus.IN_PROGRESS.value,
                new_state=ReleaseStatus.SUCCEEDED.value,
                reason="Simulated dry-run release completed cleanly",
                correlation_id=release_id,
            )
            return record

        # 7. Execute actual branch, commit, and PR creation via provider
        try:
            # 7a. Create candidate branch
            await self.release_provider.create_branch(
                repository_id=request.repository_id,
                base_branch=target_branch,
                new_branch=release_branch,
            )

            # 7b. Commit changes
            effective_msg = (
                commit_message or f"feat(release): apply candidate {candidate_id}"
            )
            eff_patch = (
                patch_content
                or f"# Approved patch {request.artifact_binding.patch_hash}\n"
            )
            commit_sha = await self.release_provider.commit_changes(
                repository_id=request.repository_id,
                branch=release_branch,
                patch_content=eff_patch,
                commit_message=effective_msg,
                author_name="AgentForge Release Operator",
                author_email="release@agentforge.internal",
            )
            record.commit_sha = commit_sha

            # 7c. Create Pull Request
            pr_title = f"Release Candidate: {candidate_id}"
            pr_body = self._format_pull_request_body(request, readiness, commit_sha)
            pr_meta = await self.release_provider.create_pull_request(
                repository_id=request.repository_id,
                title=pr_title,
                body=pr_body,
                source_branch=release_branch,
                target_branch=target_branch,
            )
            record.pull_request = pr_meta
            record.status = ReleaseStatus.SUCCEEDED
            record.completed_at = datetime.now(UTC).isoformat()

            # Record audit event: release succeeded
            self.audit_store.append_event(
                approval_id=approval_id,
                actor_id=actor.actor_id,
                action="release_succeeded",
                previous_state=ReleaseStatus.IN_PROGRESS.value,
                new_state=ReleaseStatus.SUCCEEDED.value,
                reason=(
                    f"Controlled release PR #{pr_meta.pr_number} created successfully"
                ),
                correlation_id=release_id,
                metadata={
                    "pr_number": pr_meta.pr_number,
                    "pr_url": pr_meta.pr_url,
                    "commit_sha": commit_sha,
                },
            )
            return record

        except Exception as err:
            record.status = ReleaseStatus.FAILED
            record.completed_at = datetime.now(UTC).isoformat()
            record.error_message = str(err)

            self.audit_store.append_event(
                approval_id=approval_id,
                actor_id=actor.actor_id,
                action="release_failed",
                previous_state=ReleaseStatus.IN_PROGRESS.value,
                new_state=ReleaseStatus.FAILED.value,
                reason=f"Release execution failure: {err}",
                correlation_id=release_id,
            )
            raise ReleaseProviderError(f"Release execution failed: {err}") from err

    def get_release(self, release_id: str) -> ReleaseExecutionRecord | None:
        """Look up release execution record by ID."""
        return self._releases.get(release_id)

    def list_releases(
        self,
        repository_id: str | None = None,
        status: ReleaseStatus | None = None,
    ) -> list[ReleaseExecutionRecord]:
        """List recorded releases matching filter criteria."""
        results: list[ReleaseExecutionRecord] = []
        for r in self._releases.values():
            if repository_id and r.repository_id != repository_id:
                continue
            if status and r.status != status:
                continue
            results.append(r)
        return results
