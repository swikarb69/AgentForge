"""Service adapters integrating Sprints 1-8 engines into the orchestration pipeline."""

import hashlib
import inspect
import logging
from pathlib import Path
from typing import Any

from backend.app.approval.identity import UserIdentity
from backend.app.approval.models import (
    ApprovalRole,
    ApprovalStatus,
    ArtifactBinding,
)
from backend.app.approval.service import ApprovalService
from backend.app.core.config import settings
from backend.app.debugging.models import (
    RepairConfiguration,
    RepairRequest,
    RepairState,
)
from backend.app.debugging.service import DebuggingService
from backend.app.execution.models import (
    CreateExecutionRequest,
    ExecutionPolicy,
    VerificationProfile,
)
from backend.app.execution.service import ExecutionService
from backend.app.generation.models import (
    CreateGenerationRequest,
    GenerationStatus,
)
from backend.app.generation.service import GenerationService
from backend.app.issue.service import IssueContextBuilder
from backend.app.orchestration.context import WorkflowContext
from backend.app.orchestration.stages import (
    StageInput,
    StageOutput,
    WorkflowStageAdapter,
)
from backend.app.orchestration.states import StageStatus, StageType
from backend.app.planning.models import PlanStatus
from backend.app.planning.service import PlanningEngine
from backend.app.repository.analyzer import RepositoryAnalyzer
from backend.app.review.models import ReviewRequest, ReviewStatus
from backend.app.review.service import ReviewService

logger = logging.getLogger(__name__)


def _compute_sha256(data: str | bytes) -> str:
    """Compute standard SHA-256 hex digest for artifact content."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _resolve_active_patch(context: WorkflowContext) -> str:
    """Resolve current active patch content across memory and storage fallbacks.

    Prioritizes:
    1. context.shared_data["active_patch"]
    2. Repaired patch from repair artifact file or repair_result in shared_data
    3. Generated patch from generation artifact file or generation_result in shared_data
    """
    # 1. Ephemeral shared data
    active = context.shared_data.get("active_patch")
    if active and isinstance(active, str) and active.strip():
        return str(active)

    # 2. Repair artifacts / result
    repair_id = context.artifacts.repair_id
    if repair_id:
        repair_res = context.shared_data.get("repair_result")
        if repair_res and getattr(repair_res, "final_patch", None):
            return str(repair_res.final_patch)

        # Check disk artifacts for repair
        for candidate_path in [
            Path(f"agentforge-artifacts/repair/{repair_id}.patch"),
            Path(f"agentforge-artifacts/repair/{repair_id}.diff"),
        ]:
            if candidate_path.exists():
                try:
                    return candidate_path.read_text(encoding="utf-8")
                except Exception as exc:
                    logger.warning(
                        "Could not read repair patch artifact %s: %s",
                        candidate_path,
                        exc,
                    )

    # 3. Generation artifacts / result
    gen_id = context.artifacts.generation_id
    if gen_id:
        gen_res = context.shared_data.get("generation_result")
        if gen_res and getattr(gen_res, "patch_content", None):
            return str(gen_res.patch_content)

        # Check disk artifacts for generation
        for candidate_path in [
            Path(f"agentforge-artifacts/generation/{gen_id}.patch"),
            Path(f"agentforge-artifacts/generation/{gen_id}.diff"),
            Path(settings.GENERATION_ARTIFACT_DIR) / f"{gen_id}.patch",
        ]:
            if candidate_path.exists():
                try:
                    return candidate_path.read_text(encoding="utf-8")
                except Exception as exc:
                    logger.warning(
                        "Could not read generation patch artifact %s: %s",
                        candidate_path,
                        exc,
                    )

    return ""


async def _resolve_actor(
    identity_provider: Any,
    actor_id: str,
    default_role: ApprovalRole,
    display_name: str,
) -> UserIdentity:
    """Safely resolve actor from identity provider or fallback to mock user."""
    actor: UserIdentity | None = None
    if identity_provider and hasattr(identity_provider, "get_identity"):
        try:
            res = identity_provider.get_identity(actor_id)
            if inspect.isawaitable(res):
                resolved = await res
                if isinstance(resolved, UserIdentity):
                    actor = resolved
            elif isinstance(res, UserIdentity):
                actor = res
        except Exception:
            logger.debug("Failed resolving identity for '%s', using fallback", actor_id)
    if not actor:
        actor = UserIdentity(
            actor_id=actor_id,
            username=actor_id,
            display_name=display_name,
            roles=[default_role, ApprovalRole.ADMIN],
        )
    return actor


class RepositoryAnalysisAdapter(WorkflowStageAdapter):
    """Adapter invoking Sprint 1 Repository Intelligence scanner and indexer."""

    def __init__(self, analyzer: RepositoryAnalyzer | None = None) -> None:
        self.analyzer = analyzer or RepositoryAnalyzer()

    async def execute(self, stage_input: StageInput) -> StageOutput:
        """Scan repository filesystem, generate symbols and index."""
        try:
            repo_info = self.analyzer.analyze(stage_input.repository_path)
            total_symbols = len(repo_info.symbols)
            total_chunks = len(repo_info.chunks)
            summary_str = (
                f"{repo_info.root_path}:{repo_info.total_files}:{total_symbols}"
            )
            digest = _compute_sha256(summary_str)

            stage_input.context.artifacts.repository_analysis_id = str(
                repo_info.root_path
            )
            stage_input.context.artifacts.repository_analysis_hash = digest
            stage_input.context.shared_data["repository_info"] = repo_info

            summary = {
                "total_files": repo_info.total_files,
                "total_symbols": total_symbols,
                "total_chunks": total_chunks,
                "language_distribution": repo_info.language_counts,
                "analysis_hash": digest,
            }
            stage_input.context.record_stage_output(
                StageType.REPOSITORY_ANALYSIS, summary
            )

            return StageOutput(
                stage_type=StageType.REPOSITORY_ANALYSIS,
                status=StageStatus.COMPLETED,
                produced_artifact_ids=[str(repo_info.root_path)],
                summary=summary,
            )
        except Exception as exc:
            logger.exception("Repository analysis failed: %s", exc)
            return StageOutput(
                stage_type=StageType.REPOSITORY_ANALYSIS,
                status=StageStatus.FAILED,
                error=f"Repository analysis failed: {exc}",
            )


class IssueAnalysisAdapter(WorkflowStageAdapter):
    """Adapter invoking Sprint 2 Issue Intelligence parser and matcher."""

    def __init__(self, builder: IssueContextBuilder | None = None) -> None:
        self.builder = builder or IssueContextBuilder()

    async def execute(self, stage_input: StageInput) -> StageOutput:
        """Extract requirements, acceptance criteria, and map code references."""
        try:
            title = stage_input.issue_reference.strip()
            desc = ""
            if "description" in stage_input.extra_inputs:
                desc = str(stage_input.extra_inputs["description"]).strip()

            issue_ctx = self.builder.build_context(
                repository_path=stage_input.repository_path,
                title=title,
                description=desc,
                issue_id=f"ISSUE-{stage_input.workflow_id[:8]}",
            )

            analysis = issue_ctx.analysis
            analysis_json = analysis.model_dump_json()
            digest = _compute_sha256(analysis_json)

            stage_input.context.artifacts.issue_analysis_id = analysis.issue.id
            stage_input.context.artifacts.issue_analysis_hash = digest
            stage_input.context.shared_data["issue_context"] = issue_ctx

            summary = {
                "issue_id": analysis.issue.id,
                "requirements_count": len(analysis.requirements),
                "criteria_count": len(analysis.acceptance_criteria),
                "relevant_files_count": len(issue_ctx.relevant_files),
                "is_ambiguous": analysis.is_ambiguous,
                "analysis_hash": digest,
            }
            stage_input.context.record_stage_output(StageType.ISSUE_ANALYSIS, summary)

            return StageOutput(
                stage_type=StageType.ISSUE_ANALYSIS,
                status=StageStatus.COMPLETED,
                produced_artifact_ids=[analysis.issue.id],
                summary=summary,
            )
        except Exception as exc:
            logger.exception("Issue analysis failed: %s", exc)
            return StageOutput(
                stage_type=StageType.ISSUE_ANALYSIS,
                status=StageStatus.FAILED,
                error=f"Issue analysis failed: {exc}",
            )


class ImplementationPlanningAdapter(WorkflowStageAdapter):
    """Adapter invoking Sprint 3 Implementation Planning engine."""

    def __init__(self, engine: PlanningEngine | None = None) -> None:
        self.engine = engine or PlanningEngine()

    async def execute(self, stage_input: StageInput) -> StageOutput:
        """Construct deterministic task plan and risk assessment."""
        try:
            title = stage_input.issue_reference.strip()
            desc = ""
            if "description" in stage_input.extra_inputs:
                desc = str(stage_input.extra_inputs["description"]).strip()

            plan = self.engine.create_plan(
                repository_path=stage_input.repository_path,
                title=title,
                description=desc,
                issue_id=stage_input.context.artifacts.issue_analysis_id,
            )

            if plan.status == PlanStatus.BLOCKED:
                err_msg = f"Implementation plan is blocked: {plan.validation.errors}"
                return StageOutput(
                    stage_type=StageType.IMPLEMENTATION_PLANNING,
                    status=StageStatus.FAILED,
                    error=err_msg,
                )

            plan_json = plan.model_dump_json()
            digest = _compute_sha256(plan_json)

            stage_input.context.artifacts.implementation_plan_id = plan.id
            stage_input.context.artifacts.implementation_plan_hash = digest
            stage_input.context.shared_data["implementation_plan"] = plan

            summary = {
                "plan_id": plan.id,
                "tasks_count": len(plan.tasks),
                "files_to_modify": plan.files_to_modify,
                "files_to_create": plan.files_to_create,
                "risks_count": len(plan.risks),
                "plan_hash": digest,
            }
            stage_input.context.record_stage_output(
                StageType.IMPLEMENTATION_PLANNING, summary
            )

            return StageOutput(
                stage_type=StageType.IMPLEMENTATION_PLANNING,
                status=StageStatus.COMPLETED,
                produced_artifact_ids=[plan.id],
                summary=summary,
            )
        except Exception as exc:
            logger.exception("Implementation planning failed: %s", exc)
            return StageOutput(
                stage_type=StageType.IMPLEMENTATION_PLANNING,
                status=StageStatus.FAILED,
                error=f"Implementation planning failed: {exc}",
            )


class CodeGenerationAdapter(WorkflowStageAdapter):
    """Adapter invoking Sprint 4 Code Generation and patch validation engine."""

    def __init__(self, service: GenerationService | None = None) -> None:
        self.service = service or GenerationService()

    async def execute(self, stage_input: StageInput) -> StageOutput:
        """Synthesize source changes and build validated unified patch."""
        try:
            plan = stage_input.context.shared_data.get("implementation_plan")
            req = CreateGenerationRequest(
                repository_path=str(stage_input.repository_path),
                plan=plan,
                issue_id=stage_input.context.artifacts.issue_analysis_id,
                description=stage_input.issue_reference or "",
            )

            gen_result = await self.service.generate_code(req)

            if (
                gen_result.status != GenerationStatus.VALIDATED
                or not gen_result.validation.valid
            ):
                err_msg = (
                    "; ".join(gen_result.errors)
                    if gen_result.errors
                    else f"Code generation returned status {gen_result.status.value}"
                )
                return StageOutput(
                    stage_type=StageType.CODE_GENERATION,
                    status=StageStatus.FAILED,
                    error=err_msg,
                )

            digest = _compute_sha256(gen_result.patch_content)
            stage_input.context.artifacts.generation_id = gen_result.generation_id
            stage_input.context.artifacts.generation_patch_hash = digest
            stage_input.context.shared_data["generation_result"] = gen_result
            stage_input.context.shared_data["active_patch"] = gen_result.patch_content

            summary = {
                "generation_id": gen_result.generation_id,
                "files_changed_count": len(gen_result.changes),
                "patch_hash": digest,
                "files_changed": [c.path for c in gen_result.changes],
            }
            stage_input.context.record_stage_output(StageType.CODE_GENERATION, summary)

            return StageOutput(
                stage_type=StageType.CODE_GENERATION,
                status=StageStatus.COMPLETED,
                produced_artifact_ids=[gen_result.generation_id],
                summary=summary,
            )
        except Exception as exc:
            logger.exception("Code generation failed: %s", exc)
            return StageOutput(
                stage_type=StageType.CODE_GENERATION,
                status=StageStatus.FAILED,
                error=f"Code generation failed: {exc}",
            )


class ExecutionVerificationAdapter(WorkflowStageAdapter):
    """Adapter invoking Sprint 5 Isolated Execution and Verification engine."""

    def __init__(self, service: ExecutionService | None = None) -> None:
        self.service = service or ExecutionService()

    async def execute(self, stage_input: StageInput) -> StageOutput:
        """Apply patch in ephemeral sandbox and execute test verification."""
        try:
            active_patch = _resolve_active_patch(stage_input.context)
            exec_req = CreateExecutionRequest(
                repository_path=str(stage_input.repository_path),
                patch_content=active_patch,
                verification_profile=VerificationProfile.PYTHON_UNIT_TESTS,
                policy=ExecutionPolicy(timeout_seconds=120),
            )

            report = await self.service.execute_and_verify(exec_req)
            v_hash = _compute_sha256(
                f"{report.execution_id}:{report.verification_passed}:"
                f"{report.patch_applied}"
            )
            stage_input.context.artifacts.execution_id = report.execution_id
            stage_input.context.artifacts.execution_verification_hash = v_hash
            stage_input.context.shared_data["verification_report"] = report

            passed = report.verification_passed
            summary = {
                "execution_id": report.execution_id,
                "verification_passed": passed,
                "tests_executed": report.tests_executed,
                "patch_applied": report.patch_applied,
                "verification_hash": v_hash,
                "failures_count": len(report.failures),
            }
            stage_input.context.record_stage_output(
                StageType.EXECUTION_VERIFICATION, summary
            )

            if not passed:
                if stage_input.configuration.auto_repair_enabled:
                    # Proceed to repair stage
                    return StageOutput(
                        stage_type=StageType.EXECUTION_VERIFICATION,
                        status=StageStatus.COMPLETED,
                        produced_artifact_ids=[report.execution_id],
                        summary=summary,
                        requires_repair=True,
                    )
                return StageOutput(
                    stage_type=StageType.EXECUTION_VERIFICATION,
                    status=StageStatus.FAILED,
                    produced_artifact_ids=[report.execution_id],
                    summary=summary,
                    error=f"Verification failed: {report.failures}",
                )

            return StageOutput(
                stage_type=StageType.EXECUTION_VERIFICATION,
                status=StageStatus.COMPLETED,
                produced_artifact_ids=[report.execution_id],
                summary=summary,
                requires_repair=False,
            )
        except Exception as exc:
            logger.exception("Execution verification failed: %s", exc)
            return StageOutput(
                stage_type=StageType.EXECUTION_VERIFICATION,
                status=StageStatus.FAILED,
                error=f"Execution verification failed: {exc}",
            )


class AutomaticRepairAdapter(WorkflowStageAdapter):
    """Adapter invoking Sprint 6 Automatic Debugging and Repair loop."""

    def __init__(self, service: DebuggingService | None = None) -> None:
        self.service = service or DebuggingService()

    async def execute(self, stage_input: StageInput) -> StageOutput:
        """Diagnose root cause and execute bounded repair if verification failed."""
        try:
            report = stage_input.context.shared_data.get("verification_report")
            if report and report.verification_passed:
                return StageOutput(
                    stage_type=StageType.AUTOMATIC_REPAIR,
                    status=StageStatus.SKIPPED,
                    should_skip=True,
                    skip_reason="Execution verification succeeded; no repairs needed.",
                )

            if not stage_input.configuration.auto_repair_enabled:
                return StageOutput(
                    stage_type=StageType.AUTOMATIC_REPAIR,
                    status=StageStatus.SKIPPED,
                    should_skip=True,
                    skip_reason="Automatic repair is disabled in configuration.",
                )

            if not report:
                return StageOutput(
                    stage_type=StageType.AUTOMATIC_REPAIR,
                    status=StageStatus.FAILED,
                    error="Cannot execute repair: missing execution report.",
                )

            plan = stage_input.context.shared_data.get("implementation_plan")
            repair_req = RepairRequest(
                repository_path=str(stage_input.repository_path),
                execution_id=report.execution_id,
                generation_id=stage_input.context.artifacts.generation_id,
                plan_id=stage_input.context.artifacts.implementation_plan_id,
                config=RepairConfiguration(
                    max_attempts=stage_input.stage.metadata.get(
                        "max_repair_attempts", 3
                    )
                ),
            )

            repair_res = await self.service.start_repair(repair_req, plan=plan)
            stage_input.context.artifacts.repair_id = repair_res.repair_id
            stage_input.context.shared_data["repair_result"] = repair_res

            if repair_res.current_state != RepairState.SUCCEEDED:
                return StageOutput(
                    stage_type=StageType.AUTOMATIC_REPAIR,
                    status=StageStatus.FAILED,
                    produced_artifact_ids=[repair_res.repair_id],
                    error=(
                        f"Automatic repair could not resolve defect. "
                        f"State: {repair_res.current_state.value}"
                    ),
                )

            if repair_res.final_patch:
                patch_hash = _compute_sha256(repair_res.final_patch)
                stage_input.context.artifacts.repair_patch_hash = patch_hash
                stage_input.context.shared_data["active_patch"] = repair_res.final_patch
                try:
                    rep_dir = Path("agentforge-artifacts/repair")
                    rep_dir.mkdir(parents=True, exist_ok=True)
                    (rep_dir / f"{repair_res.repair_id}.patch").write_text(
                        repair_res.final_patch, encoding="utf-8"
                    )
                except Exception as exc:
                    logger.warning("Failed saving repair patch artifact: %s", exc)

            if repair_res.attempts:
                last_exec = repair_res.attempts[-1].execution_report
                if last_exec and last_exec.verification_passed:
                    stage_input.context.shared_data["verification_report"] = last_exec
                    stage_input.context.artifacts.execution_id = last_exec.execution_id
                    stage_input.context.artifacts.execution_verification_hash = (
                        _compute_sha256(
                            f"{last_exec.execution_id}:{last_exec.verification_passed}:"
                            f"{last_exec.patch_applied}"
                        )
                    )

            summary = {
                "repair_id": repair_res.repair_id,
                "current_state": repair_res.current_state.value,
                "attempts_count": len(repair_res.attempts),
                "repair_patch_hash": (stage_input.context.artifacts.repair_patch_hash),
            }
            stage_input.context.record_stage_output(StageType.AUTOMATIC_REPAIR, summary)

            return StageOutput(
                stage_type=StageType.AUTOMATIC_REPAIR,
                status=StageStatus.COMPLETED,
                produced_artifact_ids=[repair_res.repair_id],
                summary=summary,
            )
        except Exception as exc:
            logger.exception("Automatic repair failed: %s", exc)
            return StageOutput(
                stage_type=StageType.AUTOMATIC_REPAIR,
                status=StageStatus.FAILED,
                error=f"Automatic repair failed: {exc}",
            )


class CodeReviewAdapter(WorkflowStageAdapter):
    """Adapter invoking Sprint 7 Code Review and Quality Assurance engine."""

    def __init__(self, service: ReviewService | None = None) -> None:
        self.service = service or ReviewService()

    async def execute(self, stage_input: StageInput) -> StageOutput:
        """Inspect proposed changes for security, architectural, and quality defects."""
        try:
            if not stage_input.configuration.review_enabled:
                return StageOutput(
                    stage_type=StageType.CODE_REVIEW,
                    status=StageStatus.SKIPPED,
                    should_skip=True,
                    skip_reason="Code review is disabled in configuration.",
                )

            plan = stage_input.context.shared_data.get("implementation_plan")
            ver_report = stage_input.context.shared_data.get("verification_report")
            repair_res = stage_input.context.shared_data.get("repair_result")

            rev_req = ReviewRequest(
                review_id=f"rev-{stage_input.workflow_id[:8]}",
                repository_path=str(stage_input.repository_path),
                generation_id=stage_input.context.artifacts.generation_id,
                execution_id=stage_input.context.artifacts.execution_id,
                repair_id=stage_input.context.artifacts.repair_id,
                plan_id=stage_input.context.artifacts.implementation_plan_id,
                issue_id=stage_input.context.artifacts.issue_analysis_id,
                patch_content=_resolve_active_patch(stage_input.context) or None,
            )

            review_report = await self.service.create_review(
                request=rev_req,
                plan=plan,
                verification_report=ver_report,
                repair_result=repair_res,
            )

            rep_hash = _compute_sha256(
                f"{review_report.review_id}:{review_report.status.value}:"
                f"{review_report.summary.critical_count}"
            )
            stage_input.context.artifacts.review_id = review_report.review_id
            stage_input.context.artifacts.review_hash = rep_hash
            stage_input.context.shared_data["review_report"] = review_report

            summary = {
                "review_id": review_report.review_id,
                "status": review_report.status.value,
                "total_findings": len(review_report.findings),
                "critical_findings": review_report.summary.critical_count,
                "high_findings": review_report.summary.high_count,
                "quality_score": review_report.summary.quality_score,
                "review_hash": rep_hash,
            }
            stage_input.context.record_stage_output(StageType.CODE_REVIEW, summary)

            # Block progression if review detected critical findings or is blocked
            if (
                review_report.summary.critical_count > 0
                or review_report.status == ReviewStatus.BLOCKED
            ):
                return StageOutput(
                    stage_type=StageType.CODE_REVIEW,
                    status=StageStatus.FAILED,
                    produced_artifact_ids=[review_report.review_id],
                    summary=summary,
                    error=(
                        "Code review blocked progression: "
                        f"{review_report.summary.critical_count} "
                        "critical findings detected."
                    ),
                )

            return StageOutput(
                stage_type=StageType.CODE_REVIEW,
                status=StageStatus.COMPLETED,
                produced_artifact_ids=[review_report.review_id],
                summary=summary,
            )
        except Exception as exc:
            logger.exception("Code review failed: %s", exc)
            return StageOutput(
                stage_type=StageType.CODE_REVIEW,
                status=StageStatus.FAILED,
                error=f"Code review failed: {exc}",
            )


class HumanApprovalAdapter(WorkflowStageAdapter):
    """Adapter invoking Sprint 8 Human Authorization engine and checkpoints."""

    def __init__(self, service: ApprovalService | None = None) -> None:
        self.service = service or ApprovalService()

    async def execute(self, stage_input: StageInput) -> StageOutput:
        """Create or query human approval request and enforce signoff boundary."""
        try:
            approval_id = stage_input.context.artifacts.approval_id

            if not approval_id:
                # Create fresh approval request
                patch_hash = stage_input.context.get_effective_patch_hash() or (
                    "0" * 64
                )
                binding = ArtifactBinding(
                    patch_hash=patch_hash,
                    review_id=stage_input.context.artifacts.review_id,
                    review_hash=stage_input.context.artifacts.review_hash,
                    verification_id=stage_input.context.artifacts.execution_id,
                    verification_hash=(
                        stage_input.context.artifacts.execution_verification_hash
                    ),
                    plan_id=stage_input.context.artifacts.implementation_plan_id,
                    plan_hash=(stage_input.context.artifacts.implementation_plan_hash),
                    target_branch=stage_input.target_branch,
                )

                actor = await _resolve_actor(
                    getattr(self.service, "identity_provider", None),
                    actor_id="operator",
                    default_role=ApprovalRole.REQUESTER,
                    display_name="Operator",
                )

                appr_req = self.service.create_approval_request(
                    actor=actor,
                    repository_id=stage_input.context.repository_id,
                    release_candidate_id=f"rc-{stage_input.workflow_id[:8]}",
                    artifact_binding=binding,
                    issue_id=stage_input.context.artifacts.issue_analysis_id,
                )
                approval_id = appr_req.approval_id
                stage_input.context.artifacts.approval_id = approval_id
                stage_input.context.artifacts.approval_hash = (
                    appr_req.artifact_binding.patch_hash
                )

            # Query current approval status
            appr = self.service.get_approval_request(approval_id)
            if not appr:
                return StageOutput(
                    stage_type=StageType.HUMAN_APPROVAL,
                    status=StageStatus.FAILED,
                    error=f"Approval request '{approval_id}' not found.",
                )

            summary = {
                "approval_id": appr.approval_id,
                "status": appr.status.value,
                "risk_level": appr.risk_level.value,
                "approvals_count": len(appr.decisions),
            }
            stage_input.context.record_stage_output(StageType.HUMAN_APPROVAL, summary)

            if appr.status == ApprovalStatus.PENDING:
                return StageOutput(
                    stage_type=StageType.HUMAN_APPROVAL,
                    status=StageStatus.WAITING,
                    produced_artifact_ids=[appr.approval_id],
                    summary=summary,
                    should_pause_for_human=True,
                )

            if appr.status == ApprovalStatus.APPROVED:
                return StageOutput(
                    stage_type=StageType.HUMAN_APPROVAL,
                    status=StageStatus.COMPLETED,
                    produced_artifact_ids=[appr.approval_id],
                    summary=summary,
                    should_pause_for_human=False,
                )

            return StageOutput(
                stage_type=StageType.HUMAN_APPROVAL,
                status=StageStatus.FAILED,
                produced_artifact_ids=[appr.approval_id],
                summary=summary,
                error=(
                    f"Approval request {approval_id} is in non-approved "
                    f"status: {appr.status.value}"
                ),
            )
        except Exception as exc:
            logger.exception("Human approval stage failed: %s", exc)
            return StageOutput(
                stage_type=StageType.HUMAN_APPROVAL,
                status=StageStatus.FAILED,
                error=f"Human approval stage failed: {exc}",
            )


class ControlledReleaseAdapter(WorkflowStageAdapter):
    """Adapter invoking Sprint 8 Controlled Release provider (Zero Auto-Merge)."""

    def __init__(self, service: ApprovalService | None = None) -> None:
        self.service = service or ApprovalService()

    async def execute(self, stage_input: StageInput) -> StageOutput:
        """Validate 13-point readiness criteria and create release PR."""
        try:
            approval_id = stage_input.context.artifacts.approval_id
            if not approval_id:
                return StageOutput(
                    stage_type=StageType.CONTROLLED_RELEASE,
                    status=StageStatus.FAILED,
                    error="Cannot execute release: missing approved request ID.",
                )

            # 1. Evaluate release readiness
            active_patch = _resolve_active_patch(stage_input.context)
            repo_path_to_check = (
                stage_input.repository_path
                if stage_input.repository_path
                and Path(stage_input.repository_path).is_dir()
                else None
            )
            readiness = self.service.evaluate_readiness(
                approval_id,
                repository_path=repo_path_to_check,
                plan=stage_input.context.shared_data.get("implementation_plan"),
                generation_result=stage_input.context.shared_data.get(
                    "generation_result"
                ),
                verification_report=stage_input.context.shared_data.get(
                    "verification_report"
                ),
                review_report=stage_input.context.shared_data.get("review_report"),
                repair_session=stage_input.context.shared_data.get("repair_result"),
                current_patch_hash=stage_input.context.get_effective_patch_hash(),
                current_review_hash=stage_input.context.artifacts.review_hash,
            )
            if not readiness.is_release_ready:
                return StageOutput(
                    stage_type=StageType.CONTROLLED_RELEASE,
                    status=StageStatus.FAILED,
                    error=(
                        f"Release readiness checks failed: {readiness.blocking_reasons}"
                    ),
                )

            # 2. Execute release operation (Zero auto-merge: creates branch and PR)
            actor = await _resolve_actor(
                getattr(self.service, "identity_provider", None),
                actor_id="release_operator",
                default_role=ApprovalRole.RELEASE_OPERATOR,
                display_name="Release Operator",
            )

            rec = await self.service.execute_release(
                actor=actor,
                approval_id=approval_id,
                dry_run=stage_input.configuration.dry_run,
                patch_content=active_patch or "# empty release patch",
                commit_message=(
                    f"Release candidate for issue {stage_input.issue_reference}"
                ),
            )

            stage_input.context.artifacts.release_id = rec.release_id
            if rec.pull_request:
                stage_input.context.artifacts.release_pr_number = (
                    rec.pull_request.pr_number
                )
                stage_input.context.artifacts.release_pr_url = rec.pull_request.pr_url

            summary = {
                "release_id": rec.release_id,
                "status": rec.status.value,
                "pr_number": (rec.pull_request.pr_number if rec.pull_request else None),
                "pr_url": (rec.pull_request.pr_url if rec.pull_request else None),
                "source_branch": (
                    rec.pull_request.source_branch if rec.pull_request else None
                ),
                "dry_run": stage_input.configuration.dry_run,
            }
            stage_input.context.record_stage_output(
                StageType.CONTROLLED_RELEASE, summary
            )

            return StageOutput(
                stage_type=StageType.CONTROLLED_RELEASE,
                status=StageStatus.COMPLETED,
                produced_artifact_ids=[rec.release_id],
                summary=summary,
            )
        except Exception as exc:
            logger.exception("Controlled release stage failed: %s", exc)
            return StageOutput(
                stage_type=StageType.CONTROLLED_RELEASE,
                status=StageStatus.FAILED,
                error=f"Controlled release stage failed: {exc}",
            )
