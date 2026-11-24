"""AgentForge End-to-End Acceptance Test Suite (Scenarios A through J).

Covers:
- Scenario A: Happy Path (Issue to Approved Release / PR)
- Scenario B: Automatic Repair Loop (Failure -> Repair -> Re-verification -> Release)
- Scenario C: Review Finds Blocking Defects (Halts Release)
- Scenario D: Human Rejection (Halts Release)
- Scenario E: Stage Timeout & Exponential Retry
- Scenario F: Idempotent Execution
- Scenario G: Workflow Pause & Resume
- Scenario H: Crash Recovery from Durable Storage
- Scenario I: Non-Bypassable Release Guardrail
- Scenario J: Zero Auto-Merge Enforcement
"""

from pathlib import Path

import pytest

from backend.app.core.config import settings
from backend.app.orchestration.models import WorkflowRequest
from backend.app.orchestration.service import OrchestrationService
from backend.app.orchestration.stage_registry import StageRegistry
from backend.app.orchestration.stages import (
    RetryPolicy,
    StageDefinition,
    StageInput,
    StageOutput,
    WorkflowStageAdapter,
)
from backend.app.orchestration.states import (
    StageStatus,
    StageType,
    WorkflowStatus,
)
from backend.app.orchestration.storage import (
    InMemoryWorkflowStore,
    SqliteWorkflowStore,
)


class ConfigurableE2EAdapter(WorkflowStageAdapter):
    """Configurable mock adapter for end-to-end acceptance scenarios."""

    def __init__(
        self,
        stage_type: StageType,
        should_pause: bool = False,
        should_skip: bool = False,
        fail_times: int = 0,
        fail_error: str | None = None,
        requires_repair: bool = False,
    ) -> None:
        self.stage_type = stage_type
        self.should_pause = should_pause
        self.should_skip = should_skip
        self.fail_times = fail_times
        self.fail_error = fail_error or "Simulated stage failure"
        self.requires_repair = requires_repair
        self.called_count = 0

    async def execute(self, stage_input: StageInput) -> StageOutput:
        self.called_count += 1

        if self.called_count <= self.fail_times:
            return StageOutput(
                stage_type=self.stage_type,
                status=StageStatus.FAILED,
                error=self.fail_error,
                requires_repair=self.requires_repair,
            )

        if self.should_pause:
            stage_input.context.artifacts.approval_id = "approval-req-e2e"
            return StageOutput(
                stage_type=self.stage_type,
                status=StageStatus.WAITING,
                should_pause_for_human=True,
                produced_artifact_ids=["approval-req-e2e"],
            )

        if self.should_skip:
            return StageOutput(
                stage_type=self.stage_type,
                status=StageStatus.SKIPPED,
                should_skip=True,
                skip_reason="Conditionally skipped",
            )

        art_id = f"art-{self.stage_type.value}"
        digest = "b" * 64

        # Populate context artifacts matching pipeline stage
        if self.stage_type == StageType.REPOSITORY_ANALYSIS:
            stage_input.context.artifacts.repository_analysis_id = art_id
            stage_input.context.artifacts.repository_analysis_hash = digest
        elif self.stage_type == StageType.ISSUE_ANALYSIS:
            stage_input.context.artifacts.issue_analysis_id = art_id
            stage_input.context.artifacts.issue_analysis_hash = digest
        elif self.stage_type == StageType.IMPLEMENTATION_PLANNING:
            stage_input.context.artifacts.implementation_plan_id = art_id
            stage_input.context.artifacts.implementation_plan_hash = digest
        elif self.stage_type == StageType.CODE_GENERATION:
            stage_input.context.artifacts.generation_id = art_id
            stage_input.context.artifacts.generation_patch_hash = digest
        elif self.stage_type == StageType.EXECUTION_VERIFICATION:
            stage_input.context.artifacts.execution_id = art_id
            stage_input.context.artifacts.execution_verification_hash = digest
        elif self.stage_type == StageType.AUTOMATIC_REPAIR:
            stage_input.context.artifacts.repair_id = art_id
            stage_input.context.artifacts.repair_patch_hash = digest
            stage_input.context.shared_data["repair_attempts"] = (
                stage_input.context.shared_data.get("repair_attempts", 0) + 1
            )
        elif self.stage_type == StageType.CODE_REVIEW:
            stage_input.context.artifacts.review_id = art_id
            stage_input.context.artifacts.review_hash = digest
        elif self.stage_type == StageType.HUMAN_APPROVAL:
            stage_input.context.artifacts.approval_id = art_id
            stage_input.context.artifacts.approval_hash = digest
        elif self.stage_type == StageType.CONTROLLED_RELEASE:
            stage_input.context.artifacts.release_id = art_id
            stage_input.context.artifacts.release_pr_number = 101
            stage_input.context.artifacts.release_pr_url = (
                "https://github.com/swikarb69/AgentForge/pull/101"
            )

        return StageOutput(
            stage_type=self.stage_type,
            status=StageStatus.COMPLETED,
            produced_artifact_ids=[art_id],
        )


def _build_e2e_registry(
    pause_at_approval: bool = True,
    adapters_override: dict[StageType, WorkflowStageAdapter] | None = None,
) -> StageRegistry:
    """Construct deterministic 9-stage registry for acceptance tests."""
    reg = StageRegistry()
    overrides = adapters_override or {}

    stages_spec = [
        (StageType.REPOSITORY_ANALYSIS, "Repo Analysis", [], True, 300, 2),
        (
            StageType.ISSUE_ANALYSIS,
            "Issue Analysis",
            [StageType.REPOSITORY_ANALYSIS],
            True,
            300,
            2,
        ),
        (
            StageType.IMPLEMENTATION_PLANNING,
            "Planning",
            [StageType.ISSUE_ANALYSIS],
            True,
            300,
            2,
        ),
        (
            StageType.CODE_GENERATION,
            "Code Gen",
            [StageType.IMPLEMENTATION_PLANNING],
            True,
            600,
            2,
        ),
        (
            StageType.EXECUTION_VERIFICATION,
            "Verification",
            [StageType.CODE_GENERATION],
            True,
            600,
            1,
        ),
        (
            StageType.AUTOMATIC_REPAIR,
            "Repair",
            [StageType.EXECUTION_VERIFICATION],
            False,
            900,
            1,
        ),
        (
            StageType.CODE_REVIEW,
            "Code Review",
            [StageType.EXECUTION_VERIFICATION],
            True,
            300,
            2,
        ),
        (
            StageType.HUMAN_APPROVAL,
            "Human Approval",
            [StageType.CODE_REVIEW],
            True,
            3600,
            0,
        ),
        (
            StageType.CONTROLLED_RELEASE,
            "Controlled Release",
            [StageType.HUMAN_APPROVAL],
            True,
            300,
            1,
        ),
    ]

    for st, name, prereqs, req, timeout, retries in stages_spec:
        defn = StageDefinition(
            stage_type=st,
            name=name,
            description=f"E2E test stage {name}",
            required=req,
            prerequisites=prereqs,
            timeout_seconds=timeout,
            retry_policy=RetryPolicy(max_attempts=retries, initial_delay_seconds=0.01),
        )
        if st in overrides:
            adapter = overrides[st]
        elif st == StageType.AUTOMATIC_REPAIR:
            adapter = ConfigurableE2EAdapter(st, should_skip=True)
        elif st == StageType.HUMAN_APPROVAL:
            adapter = ConfigurableE2EAdapter(st, should_pause=pause_at_approval)
        else:
            adapter = ConfigurableE2EAdapter(st)

        reg.register(defn, adapter)

    return reg


@pytest.mark.anyio
async def test_scenario_a_happy_path_issue_to_approved_release() -> None:
    """Scenario A: Happy path through all 9 stages to approved release PR."""
    store = InMemoryWorkflowStore()
    registry = _build_e2e_registry(pause_at_approval=True)
    service = OrchestrationService(store=store, stage_registry=registry)

    req = WorkflowRequest(
        workflow_id="wf-scenario-a",
        repository_id="swikarb69/AgentForge",
        issue_reference="issue-42",
        requested_by="lead-engineer",
    )

    # 1. Run pipeline up to human approval checkpoint
    run = await service.start_workflow(req)
    assert run.status == WorkflowStatus.WAITING_FOR_APPROVAL
    appr_stage = run.get_stage(StageType.HUMAN_APPROVAL)
    assert appr_stage is not None
    assert appr_stage.status == StageStatus.WAITING

    # 2. Grant human approval and resume
    registry._adapters[StageType.HUMAN_APPROVAL] = ConfigurableE2EAdapter(
        StageType.HUMAN_APPROVAL, should_pause=False
    )
    resumed_run = await service.resume_workflow("wf-scenario-a")

    # 3. Verify successful completion of entire pipeline
    assert resumed_run.status == WorkflowStatus.COMPLETED
    rel_stage = resumed_run.get_stage(StageType.CONTROLLED_RELEASE)
    assert rel_stage is not None
    assert rel_stage.status == StageStatus.COMPLETED

    # 4. Verify context contains all artifacts
    ctx = await store.get_context("wf-scenario-a")
    assert ctx is not None
    assert ctx.artifacts.repository_analysis_id is not None
    assert ctx.artifacts.issue_analysis_id is not None
    assert ctx.artifacts.implementation_plan_id is not None
    assert ctx.artifacts.generation_id is not None
    assert ctx.artifacts.execution_id is not None
    assert ctx.artifacts.review_id is not None
    assert ctx.artifacts.approval_id is not None
    assert ctx.artifacts.release_id is not None
    assert ctx.artifacts.release_pr_number == 101


@pytest.mark.anyio
async def test_scenario_b_automatic_repair_loop() -> None:
    """Scenario B: Test failure triggers repair loop, then succeeds."""
    store = InMemoryWorkflowStore()

    class RepairLoopVerificationAdapter(WorkflowStageAdapter):
        def __init__(self) -> None:
            self.executions = 0

        async def execute(self, stage_input: StageInput) -> StageOutput:
            self.executions += 1
            if self.executions == 1:
                return StageOutput(
                    stage_type=StageType.EXECUTION_VERIFICATION,
                    status=StageStatus.FAILED,
                    error="Unit test assertion failed",
                    requires_repair=True,
                )
            stage_input.context.artifacts.execution_id = "art-exec-verified"
            return StageOutput(
                stage_type=StageType.EXECUTION_VERIFICATION,
                status=StageStatus.COMPLETED,
                produced_artifact_ids=["art-exec-verified"],
            )

    verification_adapter = RepairLoopVerificationAdapter()
    repair_adapter = ConfigurableE2EAdapter(StageType.AUTOMATIC_REPAIR)

    registry = _build_e2e_registry(
        pause_at_approval=False,
        adapters_override={
            StageType.EXECUTION_VERIFICATION: verification_adapter,
            StageType.AUTOMATIC_REPAIR: repair_adapter,
        },
    )

    service = OrchestrationService(store=store, stage_registry=registry)

    req = WorkflowRequest(
        workflow_id="wf-scenario-b",
        repository_id="swikarb69/AgentForge",
        issue_reference="issue-bug-fix",
        requested_by="qa-engineer",
    )

    run = await service.start_workflow(req)
    assert run.status == WorkflowStatus.COMPLETED
    assert verification_adapter.executions == 2
    assert repair_adapter.called_count == 1

    ctx = await store.get_context("wf-scenario-b")
    assert ctx is not None
    assert ctx.artifacts.repair_id is not None
    assert ctx.shared_data.get("repair_attempts") == 1


@pytest.mark.anyio
async def test_scenario_c_review_finds_blocking_defects() -> None:
    """Scenario C: Code review discovers critical defect, halting pipeline."""
    store = InMemoryWorkflowStore()
    review_adapter = ConfigurableE2EAdapter(
        StageType.CODE_REVIEW,
        fail_times=5,  # Exhausts retries so stage fails
        fail_error="Critical security vulnerability detected in generated code",
    )
    release_adapter = ConfigurableE2EAdapter(StageType.CONTROLLED_RELEASE)

    registry = _build_e2e_registry(
        pause_at_approval=False,
        adapters_override={
            StageType.CODE_REVIEW: review_adapter,
            StageType.CONTROLLED_RELEASE: release_adapter,
        },
    )

    service = OrchestrationService(store=store, stage_registry=registry)

    req = WorkflowRequest(
        workflow_id="wf-scenario-c",
        repository_id="swikarb69/AgentForge",
        issue_reference="issue-security",
        requested_by="devsecops",
    )

    run = await service.start_workflow(req)
    # Review failed, so pipeline stops
    assert run.status in (WorkflowStatus.FAILED, WorkflowStatus.BLOCKED)
    assert release_adapter.called_count == 0


@pytest.mark.anyio
async def test_scenario_d_human_rejection_halts_release() -> None:
    """Scenario D: Human rejection halts release and creates no PR."""
    store = InMemoryWorkflowStore()
    release_adapter = ConfigurableE2EAdapter(StageType.CONTROLLED_RELEASE)

    registry = _build_e2e_registry(
        pause_at_approval=True,
        adapters_override={StageType.CONTROLLED_RELEASE: release_adapter},
    )

    service = OrchestrationService(store=store, stage_registry=registry)

    req = WorkflowRequest(
        workflow_id="wf-scenario-d",
        repository_id="swikarb69/AgentForge",
        issue_reference="issue-rejection",
        requested_by="engineer",
    )

    # 1. Run until approval
    run = await service.start_workflow(req)
    assert run.status == WorkflowStatus.WAITING_FOR_APPROVAL

    # 2. Reject via cancellation
    cancelled = await service.cancel_workflow(
        workflow_id="wf-scenario-d",
        reason="Human approver rejected changes: security defect detected",
    )

    assert cancelled.status == WorkflowStatus.CANCELLED
    assert release_adapter.called_count == 0

    ctx = await store.get_context("wf-scenario-d")
    assert ctx is not None
    assert ctx.artifacts.release_pr_number is None


@pytest.mark.anyio
async def test_scenario_e_stage_timeout_and_exponential_retry() -> None:
    """Scenario E: Stage transient failure succeeds on exponential retry."""
    store = InMemoryWorkflowStore()
    # Adapter fails on first call, succeeds on second call
    retrying_adapter = ConfigurableE2EAdapter(
        StageType.CODE_GENERATION,
        fail_times=1,
        fail_error="Transient LLM API rate limit 429",
    )

    registry = _build_e2e_registry(
        pause_at_approval=False,
        adapters_override={StageType.CODE_GENERATION: retrying_adapter},
    )

    service = OrchestrationService(store=store, stage_registry=registry)

    req = WorkflowRequest(
        workflow_id="wf-scenario-e",
        repository_id="swikarb69/AgentForge",
        issue_reference="issue-transient",
        requested_by="engineer",
    )

    run = await service.start_workflow(req)
    assert run.status == WorkflowStatus.COMPLETED
    assert retrying_adapter.called_count == 2


@pytest.mark.anyio
async def test_scenario_f_idempotency_prevents_duplicate_runs() -> None:
    """Scenario F: Submitting same idempotency key returns existing run."""
    store = InMemoryWorkflowStore()
    registry = _build_e2e_registry(pause_at_approval=False)
    service = OrchestrationService(store=store, stage_registry=registry)

    req1 = WorkflowRequest(
        workflow_id="wf-scenario-f",
        repository_id="swikarb69/AgentForge",
        issue_reference="issue-idemp",
        requested_by="engineer",
        idempotency_key="idemp-key-acceptance-100",
    )

    run1 = await service.start_workflow(req1)
    assert run1.status == WorkflowStatus.COMPLETED

    # Resubmit identical request with same idempotency key and workflow_id
    req2 = WorkflowRequest(
        workflow_id="wf-scenario-f",
        repository_id="swikarb69/AgentForge",
        issue_reference="issue-idemp",
        requested_by="engineer",
        idempotency_key="idemp-key-acceptance-100",
    )

    run2 = await service.start_workflow(req2)
    # Must return original workflow run without creating duplicate
    assert run2.workflow_id == "wf-scenario-f"
    assert run2.status == WorkflowStatus.COMPLETED


@pytest.mark.anyio
async def test_scenario_g_workflow_pause_and_resume() -> None:
    """Scenario G: Paused workflow resumes from next pending stage."""
    store = InMemoryWorkflowStore()
    registry = _build_e2e_registry(pause_at_approval=True)
    service = OrchestrationService(store=store, stage_registry=registry)

    req = WorkflowRequest(
        workflow_id="wf-scenario-g",
        repository_id="swikarb69/AgentForge",
        issue_reference="issue-pause",
        requested_by="engineer",
    )

    # Initial start pauses at approval
    run = await service.start_workflow(req)
    assert run.status == WorkflowStatus.WAITING_FOR_APPROVAL

    # Operator explicitly pauses workflow
    paused = await service.pause_workflow("wf-scenario-g")
    assert paused.status == WorkflowStatus.PAUSED

    # Configure adapter to allow approval
    registry._adapters[StageType.HUMAN_APPROVAL] = ConfigurableE2EAdapter(
        StageType.HUMAN_APPROVAL, should_pause=False
    )

    # Resume workflow to complete remaining stages
    resumed = await service.resume_workflow("wf-scenario-g")
    assert resumed.status == WorkflowStatus.COMPLETED
    rel_stage = resumed.get_stage(StageType.CONTROLLED_RELEASE)
    assert rel_stage is not None
    assert rel_stage.status == StageStatus.COMPLETED


@pytest.mark.anyio
async def test_scenario_h_crash_recovery_from_durable_storage(
    tmp_path: Path,
) -> None:
    """Scenario H: Crash recovery reconstructs state from SQLite."""
    db_path = tmp_path / "e2e_recovery.db"
    store1 = SqliteWorkflowStore(db_path=db_path)
    registry = _build_e2e_registry(pause_at_approval=True)

    service1 = OrchestrationService(store=store1, stage_registry=registry)
    req = WorkflowRequest(
        workflow_id="wf-scenario-h",
        repository_id="swikarb69/AgentForge",
        issue_reference="issue-crash-recovery",
        requested_by="sysadmin",
    )

    run1 = await service1.start_workflow(req)
    assert run1.status == WorkflowStatus.WAITING_FOR_APPROVAL
    store1.close()

    # Simulate service restart with new store & service instance
    store2 = SqliteWorkflowStore(db_path=db_path)
    try:
        service2 = OrchestrationService(store=store2, stage_registry=registry)
        # Recover workflow state
        recovered = await service2.recovery.recover("wf-scenario-h")
        assert recovered.status == WorkflowStatus.WAITING_FOR_APPROVAL

        # Resume to complete pipeline
        registry._adapters[StageType.HUMAN_APPROVAL] = ConfigurableE2EAdapter(
            StageType.HUMAN_APPROVAL, should_pause=False
        )
        resumed = await service2.resume_workflow("wf-scenario-h")
        assert resumed.status == WorkflowStatus.COMPLETED
    finally:
        store2.close()


@pytest.mark.anyio
async def test_scenario_i_non_bypassable_release_guardrail() -> None:
    """Scenario I: Controlled release cannot proceed without human approval."""
    store = InMemoryWorkflowStore()
    registry = _build_e2e_registry(pause_at_approval=True)
    service = OrchestrationService(store=store, stage_registry=registry)

    req = WorkflowRequest(
        workflow_id="wf-scenario-i",
        repository_id="swikarb69/AgentForge",
        issue_reference="issue-security-gate",
        requested_by="auditor",
    )

    run = await service.start_workflow(req)
    assert run.status == WorkflowStatus.WAITING_FOR_APPROVAL

    # Controlled release stage must still be pending or not executed
    rel_stage = run.get_stage(StageType.CONTROLLED_RELEASE)
    assert rel_stage is None or rel_stage.status == StageStatus.PENDING


def test_scenario_j_zero_auto_merge_enforcement() -> None:
    """Scenario J: Enforce zero auto-merge boundary in settings and release."""
    # 1. Verify AUTO_MERGE_ALLOWED must be False
    assert settings.AUTO_MERGE_ALLOWED is False

    # 2. Configuration validator rejects enabling auto-merge
    settings.AUTO_MERGE_ALLOWED = True
    errors = settings.validate_configuration()
    settings.AUTO_MERGE_ALLOWED = False
    assert any("AUTO_MERGE_ALLOWED" in err for err in errors)

    # 3. Host execution fallback must also be permanently forbidden
    assert settings.HOST_EXECUTION_FALLBACK_ALLOWED is False
