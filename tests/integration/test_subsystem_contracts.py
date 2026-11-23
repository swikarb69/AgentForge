"""Inter-stage subsystem contract verification and cross-module integration tests."""

from pathlib import Path

import pytest

from backend.app.approval.identity import UserIdentity
from backend.app.approval.models import (
    ApprovalRole,
    ApprovalStatus,
    ArtifactBinding,
    DecisionOutcome,
)
from backend.app.approval.release_models import ReleaseStatus
from backend.app.approval.service import ApprovalService
from backend.app.debugging.models import (
    RepairConfiguration,
    RepairRequest,
    RepairState,
)
from backend.app.debugging.repair_controller import BoundedRepairController
from backend.app.debugging.service import DebuggingService
from backend.app.execution.docker_runner import DockerRunner, MockDockerClient
from backend.app.execution.models import (
    CreateExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
    VerificationProfile,
    VerificationReport,
)
from backend.app.execution.service import ExecutionService
from backend.app.generation.models import (
    CreateGenerationRequest,
    GenerationStatus,
)
from backend.app.generation.service import GenerationService
from backend.app.issue.service import IssueContextBuilder
from backend.app.orchestration.adapters import (
    AutomaticRepairAdapter,
    CodeGenerationAdapter,
    CodeReviewAdapter,
    ControlledReleaseAdapter,
    HumanApprovalAdapter,
    ImplementationPlanningAdapter,
    _compute_sha256,
    _resolve_active_patch,
)
from backend.app.orchestration.context import WorkflowContext
from backend.app.orchestration.models import (
    WorkflowConfiguration,
    WorkflowStage,
)
from backend.app.orchestration.stages import StageInput
from backend.app.orchestration.states import StageStatus, StageType
from backend.app.planning.models import PlanStatus
from backend.app.planning.service import PlanningEngine
from backend.app.repository.analyzer import RepositoryAnalyzer
from backend.app.review.models import ReviewRequest
from backend.app.review.service import ReviewService

FIXTURE_PATH = Path("agentforge-fixtures/sample-project").resolve()


def _make_mock_exec_service(
    stdout: str = "=== 5 passed in 0.05s ===\n",
    exit_code: int = 0,
) -> ExecutionService:
    """Construct deterministic execution service with mock docker runner."""
    client = MockDockerClient(
        available=True,
        simulated_exit_code=exit_code,
        simulated_stdout=stdout,
    )
    runner = DockerRunner(docker_client=client)
    return ExecutionService(docker_runner=runner)


def _make_failing_report(
    execution_id: str = "exec-fail-contract",
) -> VerificationReport:
    """Create a deterministic failing verification report."""
    return VerificationReport(
        execution_id=execution_id,
        execution_success=False,
        patch_applied=True,
        tests_executed=True,
        verification_passed=False,
        failures=["tests/test_auth.py::test_login failed"],
        execution_result=ExecutionResult(
            execution_id=execution_id,
            status=ExecutionStatus.FAILED,
            exit_code=1,
            stdout=(
                "FAILED tests/test_auth.py::test_login - AssertionError: False != True"
            ),
            stderr="",
            errors=[],
        ),
    )


def _make_stage_input(
    stage_type: StageType,
    context: WorkflowContext,
    extra_inputs: dict | None = None,
) -> StageInput:
    """Construct a validated StageInput for direct adapter testing."""
    return StageInput(
        workflow_id=context.workflow_id,
        stage=WorkflowStage(
            stage_id=f"stg-{stage_type.value}",
            stage_type=stage_type,
            name=stage_type.value,
        ),
        context=context,
        configuration=context.configuration,
        repository_path=FIXTURE_PATH,
        issue_reference=context.issue_reference,
        extra_inputs=extra_inputs or {},
    )


def test_repo_to_issue_contract() -> None:
    """Contract 1: Repository intelligence output is ingested by Issue analyzer."""
    analyzer = RepositoryAnalyzer()
    repo_info = analyzer.analyze(FIXTURE_PATH)
    assert repo_info.total_files > 0
    assert len(repo_info.symbols) >= 0

    builder = IssueContextBuilder()
    issue_ctx = builder.build_context(
        repository_path=FIXTURE_PATH,
        title="Fix verify_token in app/auth.py",
        description="Expired JWT tokens must return HTTP 401 in app/auth.py",
        issue_id="ISSUE-CONTRACT-01",
    )
    assert issue_ctx.analysis.issue.id == "ISSUE-CONTRACT-01"
    assert issue_ctx.analysis.issue.title == "Fix verify_token in app/auth.py"
    assert len(issue_ctx.analysis.requirements) > 0


def test_issue_to_planning_contract() -> None:
    """Contract 2: Issue analysis output is ingested by Planning engine."""
    builder = IssueContextBuilder()
    issue_ctx = builder.build_context(
        repository_path=FIXTURE_PATH,
        title="Fix verify_token in app/auth.py",
        description="Expired JWT tokens must return HTTP 401 in app/auth.py",
        issue_id="ISSUE-CONTRACT-02",
    )

    engine = PlanningEngine()
    plan = engine.create_plan(
        repository_path=FIXTURE_PATH,
        title=issue_ctx.analysis.issue.title,
        description=issue_ctx.analysis.issue.description,
        issue_id=issue_ctx.analysis.issue.id,
    )

    assert plan.status != PlanStatus.BLOCKED
    assert len(plan.tasks) > 0
    assert plan.validation.valid is True
    assert plan.issue_id == "ISSUE-CONTRACT-02"


@pytest.mark.anyio
async def test_planning_to_generation_contract() -> None:
    """Contract 3: Implementation plan is ingested by Code Generation service."""
    engine = PlanningEngine()
    plan = engine.create_plan(
        repository_path=FIXTURE_PATH,
        title="Fix verify_token in app/auth.py",
        description="Expired JWT tokens must return HTTP 401 in app/auth.py",
        issue_id="ISSUE-CONTRACT-03",
    )

    gen_service = GenerationService()
    req = CreateGenerationRequest(
        repository_path=str(FIXTURE_PATH),
        plan=plan,
        issue_id=plan.issue_id,
        description="Fix verify_token in app/auth.py",
    )
    result = await gen_service.generate_code(req)

    assert result.status == GenerationStatus.VALIDATED
    assert result.validation.valid is True
    assert len(result.patch_content) > 0
    assert result.generation_id is not None


@pytest.mark.anyio
async def test_generation_to_execution_contract() -> None:
    """Contract 4: Generated patch content is ingested by Execution sandbox."""
    exec_service = _make_mock_exec_service()
    diff_text = (
        "--- a/app/main.py\n"
        "+++ b/app/main.py\n"
        "@@ -4,3 +4,3 @@\n"
        " def main() -> None:\n"
        '-    print("Sample Project Running")\n'
        '+    print("Sample Project Running Verified")\n'
    )

    req = CreateExecutionRequest(
        repository_path=str(FIXTURE_PATH),
        patch_content=diff_text,
        verification_profile=VerificationProfile.PYTHON_UNIT_TESTS,
    )
    report = await exec_service.execute_and_verify(req)

    assert report.execution_id is not None
    assert report.patch_applied is True
    assert report.verification_passed is True


@pytest.mark.anyio
async def test_execution_to_repair_contract() -> None:
    """Contract 5: Failing verification report triggers Debugging repair session."""
    exec_service = _make_mock_exec_service(
        stdout="=== 1 passed in 0.02s ===\n", exit_code=0
    )
    initial_fail = _make_failing_report("exec-fail-c5")
    exec_service._reports["exec-fail-c5"] = initial_fail

    controller = BoundedRepairController(execution_service=exec_service)
    debug_service = DebuggingService(
        controller=controller, execution_service=exec_service
    )

    repair_req = RepairRequest(
        repository_path=str(FIXTURE_PATH),
        execution_id="exec-fail-c5",
        config=RepairConfiguration(max_attempts=2),
    )
    repair_result = await debug_service.start_repair(repair_req)

    assert repair_result.current_state == RepairState.SUCCEEDED
    assert repair_result.final_patch is not None
    assert len(repair_result.attempts) >= 1
    assert repair_result.attempts[-1].execution_report.verification_passed is True


@pytest.mark.anyio
async def test_repair_to_review_contract_uses_repaired_patch() -> None:
    """Contract 6: Code review prioritizes repaired patch over initial generation."""
    repaired_patch = (
        "--- a/app/main.py\n"
        "+++ b/app/main.py\n"
        "@@ -1,3 +1,3 @@\n"
        "-print('hello')\n"
        "+print('safe repaired code')\n"
    )

    rev_service = ReviewService()
    req = ReviewRequest(
        review_id="rev-contract-06",
        repository_path=str(FIXTURE_PATH),
        generation_id="gen-old-initial",
        repair_id="repair-successful-01",
        patch_content=repaired_patch,
    )

    report = await rev_service.create_review(request=req)
    assert report.review_id == "rev-contract-06"
    assert report.summary.critical_count == 0


def test_review_to_approval_contract() -> None:
    """Contract 7: Review report and effective patch hash are bound in approval."""
    patch_hash = _compute_sha256("diff --git safe-repaired-patch")
    review_hash = _compute_sha256("rev-07:approved:0")
    exec_hash = _compute_sha256("exec-07:true:true")
    plan_hash = _compute_sha256("plan-07:valid")

    binding = ArtifactBinding(
        patch_hash=patch_hash,
        review_id="rev-contract-07",
        review_hash=review_hash,
        verification_id="exec-07",
        verification_hash=exec_hash,
        plan_id="plan-07",
        plan_hash=plan_hash,
        target_branch="main",
    )

    appr_service = ApprovalService()
    requester = UserIdentity(
        actor_id="eng_lead",
        username="lead",
        display_name="Engineering Lead",
        roles=[ApprovalRole.REQUESTER],
    )

    appr_req = appr_service.create_approval_request(
        actor=requester,
        repository_id="swikarb69/AgentForge",
        release_candidate_id="rc-contract-07",
        artifact_binding=binding,
        issue_id="ISSUE-07",
    )

    assert appr_req.status == ApprovalStatus.PENDING
    assert appr_req.artifact_binding.patch_hash == patch_hash
    assert appr_req.artifact_binding.review_id == "rev-contract-07"


@pytest.mark.anyio
async def test_approval_to_release_contract() -> None:
    """Contract 8: Approved request satisfies readiness and releases zero-auto-merge."""
    patch_hash = _compute_sha256("safe release patch content")
    review_hash = _compute_sha256("rev-08:completed:0")

    binding = ArtifactBinding(
        patch_hash=patch_hash,
        review_id="rev-08",
        review_hash=review_hash,
        target_branch="main",
    )

    appr_service = ApprovalService()
    requester = UserIdentity(
        actor_id="requester_user",
        username="requester",
        display_name="Requester",
        roles=[ApprovalRole.REQUESTER],
    )
    appr_req = appr_service.create_approval_request(
        actor=requester,
        repository_id="swikarb69/AgentForge",
        release_candidate_id="rc-contract-08",
        artifact_binding=binding,
    )

    approver = UserIdentity(
        actor_id="approver_user",
        username="approver_user",
        display_name="Approver User",
        roles=[ApprovalRole.APPROVER, ApprovalRole.ADMIN],
    )
    appr_service.submit_decision(
        actor=approver,
        approval_id=appr_req.approval_id,
        outcome=DecisionOutcome.APPROVED,
        comments="Approved for release",
        current_patch_hash=patch_hash,
        current_review_hash=review_hash,
    )

    # 1. Readiness check confirms is_release_ready
    readiness = appr_service.evaluate_readiness(
        approval_id=appr_req.approval_id,
        current_patch_hash=patch_hash,
        current_review_hash=review_hash,
    )
    assert readiness.is_release_ready is True

    # 2. Controlled release creates PR without auto-merging
    rel_op = UserIdentity(
        actor_id="rel_op",
        username="rel_op",
        display_name="Release Operator",
        roles=[ApprovalRole.RELEASE_OPERATOR, ApprovalRole.ADMIN],
    )
    record = await appr_service.execute_release(
        actor=rel_op,
        approval_id=appr_req.approval_id,
        dry_run=True,
        patch_content="safe release patch content",
    )
    assert record.status == ReleaseStatus.SUCCEEDED
    assert record.pull_request is not None
    assert record.pull_request.source_branch.startswith("agentforge/")


@pytest.mark.anyio
async def test_pipeline_repaired_patch_reaches_release() -> None:
    """Contract 9: End-to-end adapters hand off repaired patch to release stage."""
    context = WorkflowContext(
        workflow_id="wf-contract-repaired-09",
        repository_id="swikarb69/AgentForge",
        repository_path=FIXTURE_PATH,
        issue_reference="Fix verify_token in app/auth.py",
        target_branch="main",
        configuration=WorkflowConfiguration(
            auto_repair_enabled=True,
            review_enabled=True,
            dry_run=True,
        ),
    )

    # 1. Stage 3: Implementation planning
    plan_adapter = ImplementationPlanningAdapter(engine=PlanningEngine())
    stage_inp_plan = _make_stage_input(
        StageType.IMPLEMENTATION_PLANNING,
        context,
        extra_inputs={"description": "Fix verify_token in app/auth.py"},
    )
    out_plan = await plan_adapter.execute(stage_inp_plan)
    assert out_plan.status == StageStatus.COMPLETED

    # 2. Stage 4: Code generation produces initial patch
    gen_service = GenerationService()
    gen_adapter = CodeGenerationAdapter(service=gen_service)
    stage_inp_gen = _make_stage_input(StageType.CODE_GENERATION, context)
    out_gen = await gen_adapter.execute(stage_inp_gen)
    assert out_gen.status == StageStatus.COMPLETED

    # 3. Stage 5: Execution verification fails, triggering repair
    mock_exec_service = _make_mock_exec_service(
        stdout="=== 1 passed in 0.01s ===\n", exit_code=0
    )
    failing_report = _make_failing_report("exec-contract-09-fail")
    mock_exec_service._reports["exec-contract-09-fail"] = failing_report
    context.shared_data["verification_report"] = failing_report
    context.artifacts.execution_id = failing_report.execution_id

    # 4. Stage 6: Automatic repair succeeds and stores repaired patch
    controller = BoundedRepairController(execution_service=mock_exec_service)
    debug_service = DebuggingService(
        controller=controller, execution_service=mock_exec_service
    )
    repair_adapter = AutomaticRepairAdapter(service=debug_service)
    stage_inp_rep = _make_stage_input(StageType.AUTOMATIC_REPAIR, context)
    out_rep = await repair_adapter.execute(stage_inp_rep)
    assert out_rep.status == StageStatus.COMPLETED
    assert context.artifacts.repair_patch_hash is not None
    assert context.shared_data.get("active_patch") is not None
    repaired_patch = context.shared_data["active_patch"]
    assert len(repaired_patch) > 0

    # 5. Stage 7: Code review evaluates the repaired patch
    review_adapter = CodeReviewAdapter(service=ReviewService())
    stage_inp_rev = _make_stage_input(StageType.CODE_REVIEW, context)
    out_rev = await review_adapter.execute(stage_inp_rev)
    assert out_rev.status == StageStatus.COMPLETED

    # 6. Stage 8: Human approval request created binding repaired patch hash
    appr_service = ApprovalService()
    appr_adapter = HumanApprovalAdapter(service=appr_service)
    stage_inp_appr = _make_stage_input(StageType.HUMAN_APPROVAL, context)
    out_appr = await appr_adapter.execute(stage_inp_appr)
    assert out_appr.status == StageStatus.WAITING
    assert out_appr.should_pause_for_human is True
    appr_id = context.artifacts.approval_id
    assert appr_id is not None

    # Verify bound patch hash is the repaired patch hash
    req_record = appr_service.get_approval_request(appr_id)
    assert req_record.artifact_binding.patch_hash == context.artifacts.repair_patch_hash

    # Human operator approves
    appr_service.submit_decision(
        actor=UserIdentity(
            actor_id="approver_1",
            username="approver",
            display_name="Approver",
            roles=[ApprovalRole.APPROVER, ApprovalRole.ADMIN],
        ),
        approval_id=appr_id,
        outcome=DecisionOutcome.APPROVED,
        comments="Repaired patch verified and approved",
        current_patch_hash=context.get_effective_patch_hash(),
        current_review_hash=context.artifacts.review_hash,
    )

    # 7. Stage 9: Controlled release publishes repaired patch
    release_adapter = ControlledReleaseAdapter(service=appr_service)
    stage_inp_rel = _make_stage_input(StageType.CONTROLLED_RELEASE, context)
    out_rel = await release_adapter.execute(stage_inp_rel)
    assert out_rel.status == StageStatus.COMPLETED
    assert context.artifacts.release_id is not None
    rel_rec = appr_service.get_release(context.artifacts.release_id)
    assert rel_rec is not None
    assert rel_rec.status == ReleaseStatus.SUCCEEDED
    assert rel_rec.pull_request is not None
    assert rel_rec.pull_request.source_branch.startswith("agentforge/")
    assert rel_rec.approval_id == appr_id


def test_active_patch_fallback_resolution(tmp_path: Path) -> None:
    """Contract 10: _resolve_active_patch falls back systematically across tiers."""
    # Tier 1: Shared data present
    ctx1 = WorkflowContext(
        workflow_id="wf-p1",
        repository_id="repo1",
        issue_reference="issue-1",
        shared_data={"active_patch": "patch-from-shared-data"},
    )
    assert _resolve_active_patch(ctx1) == "patch-from-shared-data"

    # Tier 2: Repair artifact file fallback
    repair_id = "rep-test-fallback"
    ctx2 = WorkflowContext(
        workflow_id="wf-p2",
        repository_id="repo2",
        issue_reference="issue-2",
        shared_data={},
    )
    ctx2.artifacts.repair_id = repair_id
    rep_dir = Path("agentforge-artifacts/repair")
    rep_dir.mkdir(parents=True, exist_ok=True)
    rep_file = rep_dir / f"{repair_id}.patch"
    rep_file.write_text("patch-from-repair-disk-file", encoding="utf-8")
    try:
        assert _resolve_active_patch(ctx2) == "patch-from-repair-disk-file"
    finally:
        if rep_file.exists():
            rep_file.unlink()

    # Tier 3: Generation artifact file fallback
    gen_id = "gen-test-fallback"
    ctx3 = WorkflowContext(
        workflow_id="wf-p3",
        repository_id="repo3",
        issue_reference="issue-3",
        shared_data={},
    )
    ctx3.artifacts.generation_id = gen_id
    gen_dir = Path("agentforge-artifacts/generation")
    gen_dir.mkdir(parents=True, exist_ok=True)
    gen_file = gen_dir / f"{gen_id}.patch"
    gen_file.write_text("patch-from-generation-disk-file", encoding="utf-8")
    try:
        assert _resolve_active_patch(ctx3) == "patch-from-generation-disk-file"
    finally:
        if gen_file.exists():
            gen_file.unlink()

    # Tier 4: Empty string when nothing found
    ctx4 = WorkflowContext(
        workflow_id="wf-p4",
        repository_id="repo4",
        issue_reference="issue-4",
        shared_data={},
    )
    assert _resolve_active_patch(ctx4) == ""
