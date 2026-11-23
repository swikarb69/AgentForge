"""Unit tests for the 9 orchestration stage adapters."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.app.approval.models import (
    ApprovalRequest,
    ApprovalRiskLevel,
    ApprovalStatus,
    ArtifactBinding,
)
from backend.app.approval.readiness import (
    ReadinessStatus,
    ReleaseReadinessReport,
)
from backend.app.approval.release_models import (
    PullRequestMetadata,
    ReleaseExecutionRecord,
    ReleaseStatus,
)
from backend.app.debugging.models import RepairResult, RepairState
from backend.app.execution.models import VerificationReport
from backend.app.generation.models import (
    GenerationResult,
    GenerationStatus,
    PatchValidation,
)
from backend.app.issue.models import Issue, IssueAnalysis, IssueContext
from backend.app.orchestration.adapters import (
    AutomaticRepairAdapter,
    CodeGenerationAdapter,
    CodeReviewAdapter,
    ControlledReleaseAdapter,
    ExecutionVerificationAdapter,
    HumanApprovalAdapter,
    ImplementationPlanningAdapter,
    IssueAnalysisAdapter,
    RepositoryAnalysisAdapter,
)
from backend.app.orchestration.context import WorkflowContext
from backend.app.orchestration.models import (
    WorkflowConfiguration,
    WorkflowStage,
)
from backend.app.orchestration.stages import StageInput
from backend.app.orchestration.states import StageStatus, StageType
from backend.app.planning.models import (
    ImplementationPlan,
    PlanStatus,
    PlanValidation,
)
from backend.app.repository.models import RepositoryInfo
from backend.app.review.models import (
    ReviewReport,
    ReviewStatus,
    ReviewSummary,
)


def _make_stage_input(
    stage_type: StageType,
    repo_path: Path,
    auto_repair: bool = True,
    review_enabled: bool = True,
) -> StageInput:
    """Helper to assemble a valid StageInput bundle."""
    context = WorkflowContext(
        workflow_id="wf-test-adapter",
        repository_id="test/repo",
        issue_reference="Fix login bug",
        target_branch="main",
    )
    stage = WorkflowStage(stage_type=stage_type)
    config = WorkflowConfiguration(
        auto_repair_enabled=auto_repair,
        review_enabled=review_enabled,
        dry_run=True,
    )
    return StageInput(
        workflow_id="wf-test-adapter",
        repository_path=repo_path,
        issue_reference="Fix login bug",
        target_branch="main",
        context=context,
        stage=stage,
        configuration=config,
    )


@pytest.mark.anyio
async def test_repository_analysis_adapter(tmp_path: Path) -> None:
    """Verify repository analysis adapter scans files and sets artifact hashes."""
    mock_analyzer = MagicMock()
    mock_analyzer.analyze.return_value = RepositoryInfo(
        repository_name="test_repo",
        root_path=str(tmp_path),
        total_files=5,
        total_source_files=5,
        total_test_files=0,
        language_counts={"Python": 5},
    )

    adapter = RepositoryAnalysisAdapter(analyzer=mock_analyzer)
    stage_in = _make_stage_input(StageType.REPOSITORY_ANALYSIS, tmp_path)

    out = await adapter.execute(stage_in)
    assert out.status == StageStatus.COMPLETED
    assert stage_in.context.artifacts.repository_analysis_id == str(tmp_path)
    assert stage_in.context.artifacts.repository_analysis_hash is not None
    assert out.summary["total_files"] == 5


@pytest.mark.anyio
async def test_issue_analysis_adapter(tmp_path: Path) -> None:
    """Verify issue analysis adapter extracts requirements and context."""
    mock_builder = MagicMock()
    analysis = IssueAnalysis(
        issue=Issue(id="ISSUE-01", title="Fix login bug", description="details"),
        requirements=[],
        acceptance_criteria=[],
        constraints=[],
        keywords=[],
        references=[],
        endpoints=[],
        is_ambiguous=False,
    )
    mock_builder.build_context.return_value = IssueContext(
        analysis=analysis,
        relevant_files=["auth.py"],
        relevant_symbols=[],
        relevant_chunks=[],
        matches=[],
    )

    adapter = IssueAnalysisAdapter(builder=mock_builder)
    stage_in = _make_stage_input(StageType.ISSUE_ANALYSIS, tmp_path)

    out = await adapter.execute(stage_in)
    assert out.status == StageStatus.COMPLETED
    assert stage_in.context.artifacts.issue_analysis_id == "ISSUE-01"
    assert stage_in.context.artifacts.issue_analysis_hash is not None


@pytest.mark.anyio
async def test_implementation_planning_adapter_success_and_blocked(
    tmp_path: Path,
) -> None:
    """Verify planning adapter produces valid plan or fails on blocked plan."""
    mock_engine = MagicMock()
    plan = ImplementationPlan(
        id="PLAN-01",
        issue_id="ISSUE-01",
        objective="Fix login bug",
        summary="Deterministic plan",
        status=PlanStatus.READY,
        tasks=[],
        files_to_modify=[],
        validation=PlanValidation(valid=True, status=PlanStatus.READY),
    )
    mock_engine.create_plan.return_value = plan

    adapter = ImplementationPlanningAdapter(engine=mock_engine)
    stage_in = _make_stage_input(StageType.IMPLEMENTATION_PLANNING, tmp_path)

    out = await adapter.execute(stage_in)
    assert out.status == StageStatus.COMPLETED
    assert stage_in.context.artifacts.implementation_plan_id == "PLAN-01"
    assert stage_in.context.artifacts.implementation_plan_hash is not None

    # Test blocked plan failure
    plan_blocked = ImplementationPlan(
        id="PLAN-02",
        issue_id="ISSUE-02",
        objective="Blocked",
        summary="Blocked plan",
        status=PlanStatus.BLOCKED,
        tasks=[],
        validation=PlanValidation(
            valid=False, status=PlanStatus.BLOCKED, errors=["Ambiguity"]
        ),
    )
    mock_engine.create_plan.return_value = plan_blocked
    out_blocked = await adapter.execute(stage_in)
    assert out_blocked.status == StageStatus.FAILED
    assert "blocked" in str(out_blocked.error)


@pytest.mark.anyio
async def test_code_generation_adapter(tmp_path: Path) -> None:
    """Verify generation adapter executes and sets patch hash."""
    mock_gen = MagicMock()
    mock_gen.generate_code = AsyncMock(
        return_value=GenerationResult(
            generation_id="GEN-123",
            status=GenerationStatus.VALIDATED,
            provider_type="mock",
            patch_content="--- a/auth.py\n+++ b/auth.py\n@@ -1 +1 @@\n-old\n+new\n",
            changes=[],
            validation=PatchValidation(valid=True, status=GenerationStatus.VALIDATED),
        )
    )

    adapter = CodeGenerationAdapter(service=mock_gen)
    stage_in = _make_stage_input(StageType.CODE_GENERATION, tmp_path)

    out = await adapter.execute(stage_in)
    assert out.status == StageStatus.COMPLETED
    assert stage_in.context.artifacts.generation_id == "GEN-123"
    assert stage_in.context.artifacts.generation_patch_hash is not None


@pytest.mark.anyio
async def test_execution_verification_adapter(tmp_path: Path) -> None:
    """Verify execution verification adapter handles pass and repair trigger."""
    mock_exec = MagicMock()
    # 1. Clean verification pass
    mock_exec.execute_and_verify = AsyncMock(
        return_value=VerificationReport(
            execution_id="EXEC-PASS",
            verification_passed=True,
            execution_success=True,
            patch_applied=True,
            tests_executed=True,
        )
    )

    adapter = ExecutionVerificationAdapter(service=mock_exec)
    stage_in = _make_stage_input(StageType.EXECUTION_VERIFICATION, tmp_path)

    out = await adapter.execute(stage_in)
    assert out.status == StageStatus.COMPLETED
    assert out.requires_repair is False

    # 2. Failed verification triggering repair requirement
    mock_exec.execute_and_verify = AsyncMock(
        return_value=VerificationReport(
            execution_id="EXEC-FAIL",
            verification_passed=False,
            execution_success=True,
            patch_applied=True,
            tests_executed=True,
            failures=["AssertionError: expected True got False"],
        )
    )
    out_fail = await adapter.execute(stage_in)
    assert out_fail.status == StageStatus.COMPLETED
    assert out_fail.requires_repair is True


@pytest.mark.anyio
async def test_automatic_repair_adapter_skips_and_executes(
    tmp_path: Path,
) -> None:
    """Verify automatic repair skips if passed, and executes if failed."""
    mock_debug = MagicMock()
    adapter = AutomaticRepairAdapter(service=mock_debug)
    stage_in = _make_stage_input(StageType.AUTOMATIC_REPAIR, tmp_path)

    # When tests passed, repair should be skipped
    stage_in.context.shared_data["verification_report"] = VerificationReport(
        execution_id="EXEC-01",
        verification_passed=True,
        execution_success=True,
        patch_applied=True,
        tests_executed=True,
    )
    out_skip = await adapter.execute(stage_in)
    assert out_skip.status == StageStatus.SKIPPED
    assert out_skip.should_skip is True

    # When tests failed, repair executes
    stage_in.context.shared_data["verification_report"] = VerificationReport(
        execution_id="EXEC-02",
        verification_passed=False,
        execution_success=True,
        patch_applied=True,
        tests_executed=True,
        failures=["Failed test"],
    )
    mock_debug.start_repair = AsyncMock(
        return_value=RepairResult(
            repair_id="REP-99",
            repository_path=str(tmp_path),
            initial_execution_id="EXEC-02",
            current_state=RepairState.SUCCEEDED,
            final_patch="--- a/auth.py\n+++ b/auth.py\n",
        )
    )
    out_rep = await adapter.execute(stage_in)
    assert out_rep.status == StageStatus.COMPLETED
    assert stage_in.context.artifacts.repair_id == "REP-99"
    assert stage_in.context.artifacts.repair_patch_hash is not None


@pytest.mark.anyio
async def test_code_review_adapter(tmp_path: Path) -> None:
    """Verify code review adapter blocks on critical findings and passes on clean."""
    mock_rev = MagicMock()
    adapter = CodeReviewAdapter(service=mock_rev)

    # 1. Clean report
    mock_rev.create_review = AsyncMock(
        return_value=ReviewReport(
            review_id="REV-01",
            repository_path="test/repo",
            status=ReviewStatus.APPROVED,
            summary=ReviewSummary(
                total_findings=0,
                critical_count=0,
                high_count=0,
                medium_count=0,
                low_count=0,
                quality_score=95.0,
            ),
        )
    )
    stage_in = _make_stage_input(StageType.CODE_REVIEW, tmp_path)
    out = await adapter.execute(stage_in)
    assert out.status == StageStatus.COMPLETED
    assert stage_in.context.artifacts.review_id == "REV-01"

    # 2. Critical finding blocks
    mock_rev.create_review = AsyncMock(
        return_value=ReviewReport(
            review_id="REV-02",
            repository_path="test/repo",
            status=ReviewStatus.FAILED,
            summary=ReviewSummary(
                total_findings=1,
                critical_count=1,
                high_count=0,
                medium_count=0,
                low_count=0,
                quality_score=20.0,
            ),
        )
    )
    out_crit = await adapter.execute(stage_in)
    assert out_crit.status == StageStatus.FAILED
    assert "critical findings detected" in str(out_crit.error)


@pytest.mark.anyio
async def test_human_approval_adapter(tmp_path: Path) -> None:
    """Verify human approval adapter requests approval and evaluates status."""
    mock_appr = MagicMock()
    adapter = HumanApprovalAdapter(service=mock_appr)
    stage_in = _make_stage_input(StageType.HUMAN_APPROVAL, tmp_path)
    stage_in.context.artifacts.review_id = "REV-01"
    stage_in.context.artifacts.generation_patch_hash = "a" * 64

    # 1. New request created with status PENDING -> WAITING stage
    mock_req_pending = ApprovalRequest(
        approval_id="APPR-01",
        repository_id="test/repo",
        release_candidate_id="rc-123",
        requested_by="dev",
        artifact_binding=ArtifactBinding(patch_hash="a" * 64, review_id="REV-01"),
        status=ApprovalStatus.PENDING,
        risk_level=ApprovalRiskLevel.LOW,
    )
    mock_appr.create_approval_request.return_value = mock_req_pending
    mock_appr.get_approval_request.return_value = mock_req_pending

    out_pending = await adapter.execute(stage_in)
    assert out_pending.status == StageStatus.WAITING
    assert out_pending.should_pause_for_human is True
    assert stage_in.context.artifacts.approval_id == "APPR-01"

    # 2. Once APPROVED -> COMPLETED stage
    mock_req_approved = ApprovalRequest(
        approval_id="APPR-01",
        repository_id="test/repo",
        release_candidate_id="rc-123",
        requested_by="dev",
        artifact_binding=ArtifactBinding(patch_hash="a" * 64, review_id="REV-01"),
        status=ApprovalStatus.APPROVED,
        risk_level=ApprovalRiskLevel.LOW,
    )
    mock_appr.get_approval_request.return_value = mock_req_approved
    out_appr = await adapter.execute(stage_in)
    assert out_appr.status == StageStatus.COMPLETED
    assert out_appr.should_pause_for_human is False


@pytest.mark.anyio
async def test_controlled_release_adapter(tmp_path: Path) -> None:
    """Verify controlled release adapter validates readiness and executes release."""
    mock_appr = MagicMock()
    adapter = ControlledReleaseAdapter(service=mock_appr)
    stage_in = _make_stage_input(StageType.CONTROLLED_RELEASE, tmp_path)
    stage_in.context.artifacts.approval_id = "APPR-01"

    # 1. Readiness fails
    mock_appr.evaluate_readiness.return_value = ReleaseReadinessReport(
        candidate_id="rc-123",
        repository_id="test/repo",
        target_branch="main",
        status=ReadinessStatus.BLOCKED,
        approval_id="APPR-01",
        blocking_reasons=["Approval not granted"],
    )
    out_not_ready = await adapter.execute(stage_in)
    assert out_not_ready.status == StageStatus.FAILED
    assert "readiness checks failed" in str(out_not_ready.error)

    # 2. Readiness succeeds and release PR is created
    mock_appr.evaluate_readiness.return_value = ReleaseReadinessReport(
        candidate_id="rc-123",
        repository_id="test/repo",
        target_branch="main",
        status=ReadinessStatus.READY,
        approval_id="APPR-01",
        blocking_reasons=[],
    )
    mock_appr.execute_release = AsyncMock(
        return_value=ReleaseExecutionRecord(
            release_id="REL-999",
            approval_id="APPR-01",
            candidate_id="rc-123",
            repository_id="test/repo",
            target_branch="main",
            release_branch="agentforge/rc-123",
            executed_by="release_operator",
            status=ReleaseStatus.SUCCEEDED,
            pull_request=PullRequestMetadata(
                pr_number=42,
                title="Release candidate",
                body="Body",
                source_branch="agentforge/rc-123",
                target_branch="main",
                pr_url="https://github.com/test/repo/pull/42",
            ),
        )
    )

    out_rel = await adapter.execute(stage_in)
    assert out_rel.status == StageStatus.COMPLETED
    assert stage_in.context.artifacts.release_id == "REL-999"
    assert stage_in.context.artifacts.release_pr_number == 42
    assert (
        stage_in.context.artifacts.release_pr_url
        == "https://github.com/test/repo/pull/42"
    )
