"""End-to-end integration tests for Central Orchestration Engine and REST APIs."""

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.orchestration.context import WorkflowContext
from backend.app.orchestration.event_bus import WorkflowEventBus
from backend.app.orchestration.events import WorkflowEventType
from backend.app.orchestration.executor import WorkflowExecutor
from backend.app.orchestration.models import (
    WorkflowRequest,
    WorkflowRun,
)
from backend.app.orchestration.recovery import WorkflowRecoveryManager
from backend.app.orchestration.service import (
    OrchestrationService,
    get_orchestration_service,
)
from backend.app.orchestration.stage_registry import StageRegistry
from backend.app.orchestration.stages import (
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
from backend.app.orchestration.storage import InMemoryWorkflowStore

client = TestClient(app)


class MockStageAdapter(WorkflowStageAdapter):
    """Deterministic mock adapter producing configurable stage outcomes."""

    def __init__(
        self,
        stage_type: StageType,
        output: StageOutput | None = None,
        should_pause: bool = False,
        should_skip: bool = False,
    ) -> None:
        self.stage_type = stage_type
        self.should_pause = should_pause
        self.should_skip = should_skip
        self.output = output
        self.called_count = 0

    async def execute(self, stage_input: StageInput) -> StageOutput:
        self.called_count += 1
        if self.should_pause:
            stage_input.context.artifacts.approval_id = "mock-appr-req-1"
            return StageOutput(
                stage_type=self.stage_type,
                status=StageStatus.WAITING,
                should_pause_for_human=True,
                produced_artifact_ids=["mock-appr-req-1"],
            )

        if self.should_skip:
            return StageOutput(
                stage_type=self.stage_type,
                status=StageStatus.SKIPPED,
                should_skip=True,
                skip_reason="Skipped conditionally",
            )

        if self.output is not None:
            return self.output

        # Default artifact generation per stage
        art_id = f"art-{self.stage_type.value}-1"
        digest = "a" * 64
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
        elif self.stage_type == StageType.CODE_REVIEW:
            stage_input.context.artifacts.review_id = art_id
            stage_input.context.artifacts.review_hash = digest
        elif self.stage_type == StageType.HUMAN_APPROVAL:
            stage_input.context.artifacts.approval_id = art_id
            stage_input.context.artifacts.approval_hash = digest
        elif self.stage_type == StageType.CONTROLLED_RELEASE:
            stage_input.context.artifacts.release_id = art_id
            stage_input.context.artifacts.release_pr_number = 42
            stage_input.context.artifacts.release_pr_url = (
                "https://github.com/swikarb69/AgentForge/pull/42"
            )

        return StageOutput(
            stage_type=self.stage_type,
            status=StageStatus.COMPLETED,
            produced_artifact_ids=[art_id],
        )


def _build_test_registry(pause_at_approval: bool = False) -> StageRegistry:
    """Construct deterministic stage registry for end-to-end integration tests."""
    reg = StageRegistry()
    reg.register(
        StageDefinition(
            stage_type=StageType.REPOSITORY_ANALYSIS,
            name="Repository Analysis",
            description="Analyze repo structure",
            timeout_seconds=10,
        ),
        MockStageAdapter(StageType.REPOSITORY_ANALYSIS),
    )
    reg.register(
        StageDefinition(
            stage_type=StageType.ISSUE_ANALYSIS,
            name="Issue Analysis",
            description="Extract issue requirements",
            prerequisites=[StageType.REPOSITORY_ANALYSIS],
            timeout_seconds=10,
        ),
        MockStageAdapter(StageType.ISSUE_ANALYSIS),
    )
    reg.register(
        StageDefinition(
            stage_type=StageType.IMPLEMENTATION_PLANNING,
            name="Implementation Planning",
            description="Create task breakdown plan",
            prerequisites=[StageType.ISSUE_ANALYSIS],
            timeout_seconds=10,
        ),
        MockStageAdapter(StageType.IMPLEMENTATION_PLANNING),
    )
    reg.register(
        StageDefinition(
            stage_type=StageType.CODE_GENERATION,
            name="Code Generation",
            description="Synthesize code changes",
            prerequisites=[StageType.IMPLEMENTATION_PLANNING],
            timeout_seconds=10,
        ),
        MockStageAdapter(StageType.CODE_GENERATION),
    )
    reg.register(
        StageDefinition(
            stage_type=StageType.EXECUTION_VERIFICATION,
            name="Execution Verification",
            description="Execute test suite in isolated sandbox",
            prerequisites=[StageType.CODE_GENERATION],
            timeout_seconds=10,
        ),
        MockStageAdapter(StageType.EXECUTION_VERIFICATION),
    )
    reg.register(
        StageDefinition(
            stage_type=StageType.AUTOMATIC_REPAIR,
            name="Automatic Repair",
            description="Debug and repair failing code",
            prerequisites=[StageType.EXECUTION_VERIFICATION],
            timeout_seconds=10,
        ),
        MockStageAdapter(StageType.AUTOMATIC_REPAIR, should_skip=True),
    )
    reg.register(
        StageDefinition(
            stage_type=StageType.CODE_REVIEW,
            name="Code Review",
            description="Perform deterministic and static quality checks",
            prerequisites=[StageType.EXECUTION_VERIFICATION],
            timeout_seconds=10,
        ),
        MockStageAdapter(StageType.CODE_REVIEW),
    )
    reg.register(
        StageDefinition(
            stage_type=StageType.HUMAN_APPROVAL,
            name="Human Approval",
            description="Enforce human authorization gate",
            prerequisites=[StageType.CODE_REVIEW],
            timeout_seconds=10,
        ),
        MockStageAdapter(StageType.HUMAN_APPROVAL, should_pause=pause_at_approval),
    )
    reg.register(
        StageDefinition(
            stage_type=StageType.CONTROLLED_RELEASE,
            name="Controlled Release",
            description="Create release candidate branch and pull request",
            prerequisites=[StageType.HUMAN_APPROVAL],
            timeout_seconds=10,
        ),
        MockStageAdapter(StageType.CONTROLLED_RELEASE),
    )
    return reg


@pytest.fixture
def override_orchestration_service() -> Generator[
    tuple[OrchestrationService, StageRegistry]
]:
    """Fixture providing an isolated OrchestrationService wired to FastAPI."""
    store = InMemoryWorkflowStore()
    event_bus = WorkflowEventBus()
    registry = _build_test_registry(pause_at_approval=False)
    executor = WorkflowExecutor(stage_registry=registry, event_bus=event_bus)
    recovery = WorkflowRecoveryManager(
        store=store,
        stage_registry=registry,
        executor=executor,
        event_bus=event_bus,
    )
    test_service = OrchestrationService(
        store=store,
        stage_registry=registry,
        event_bus=event_bus,
        executor=executor,
        recovery_manager=recovery,
    )

    app.dependency_overrides[get_orchestration_service] = lambda: test_service
    yield test_service, registry
    app.dependency_overrides.clear()


def test_rest_api_workflow_lifecycle(
    override_orchestration_service: tuple[OrchestrationService, StageRegistry],
) -> None:
    """Verify REST API lifecycle: create, get, context, events, and list."""
    service, _ = override_orchestration_service

    # 1. Start Workflow via POST /api/v1/workflows
    request_payload = {
        "workflow_id": "wf-api-e2e-1",
        "repository_id": "swikarb69/AgentForge",
        "issue_reference": "ISSUE-901: End-to-end Orchestration",
        "requested_by": "qa-lead",
        "target_branch": "main",
        "idempotency_key": "idem-api-901",
        "configuration": {
            "auto_repair_enabled": True,
            "review_enabled": True,
            "dry_run": False,
            "target_branch": "main",
        },
    }

    create_resp = client.post("/api/v1/workflows", json=request_payload)
    assert create_resp.status_code == 201
    created_data = create_resp.json()
    assert created_data["workflow_id"] == "wf-api-e2e-1"
    assert created_data["status"] == WorkflowStatus.COMPLETED.value
    assert len(created_data["completed_stages"]) >= 8

    # 2. Idempotency Key check: duplicate launch returns existing run
    idem_resp = client.post("/api/v1/workflows", json=request_payload)
    assert idem_resp.status_code == 201
    assert idem_resp.json()["workflow_id"] == "wf-api-e2e-1"

    # 3. GET /api/v1/workflows/{workflow_id}
    get_resp = client.get("/api/v1/workflows/wf-api-e2e-1")
    assert get_resp.status_code == 200
    assert get_resp.json()["workflow_id"] == "wf-api-e2e-1"

    # 4. GET /api/v1/workflows/{workflow_id}/context
    ctx_resp = client.get("/api/v1/workflows/wf-api-e2e-1/context")
    assert ctx_resp.status_code == 200
    ctx_data = ctx_resp.json()
    assert ctx_data["workflow_id"] == "wf-api-e2e-1"
    assert ctx_data["artifacts"]["release_pr_number"] == 42
    assert (
        "https://github.com/swikarb69/AgentForge/pull/42"
        in (ctx_data["artifacts"]["release_pr_url"])
    )

    # 5. GET /api/v1/workflows/{workflow_id}/events
    evt_resp = client.get("/api/v1/workflows/wf-api-e2e-1/events")
    assert evt_resp.status_code == 200
    events = evt_resp.json()
    assert len(events) >= 10
    types = [e["event_type"] for e in events]
    assert WorkflowEventType.WORKFLOW_STARTED.value in types
    assert WorkflowEventType.WORKFLOW_COMPLETED.value in types

    # 6. GET /api/v1/workflows (List)
    list_resp = client.get("/api/v1/workflows", params={"limit": 10, "offset": 0})
    assert list_resp.status_code == 200
    list_data = list_resp.json()
    assert list_data["total"] >= 1
    assert any(i["workflow_id"] == "wf-api-e2e-1" for i in list_data["items"])

    # 7. 404 Not Found tests
    assert client.get("/api/v1/workflows/wf-missing-999").status_code == 404
    assert client.get("/api/v1/workflows/wf-missing-999/context").status_code == 404


def test_rest_api_pause_resume_cancel(
    override_orchestration_service: tuple[OrchestrationService, StageRegistry],
) -> None:
    """Verify REST API pause, resume, and cancel endpoints."""
    service, _ = override_orchestration_service

    # Create a pre-staged paused workflow
    run = WorkflowRun(
        workflow_id="wf-api-ctrl",
        status=WorkflowStatus.PAUSED,
        requested_by="dev-operator",
        repository_reference="repo",
        issue_reference="issue",
    )
    ctx = WorkflowContext(
        workflow_id="wf-api-ctrl",
        repository_id="repo",
        issue_reference="issue",
    )
    client.app.state = None

    import anyio

    anyio.run(service.store.save, run, ctx)

    # 1. Resume
    resume_resp = client.post(
        "/api/v1/workflows/wf-api-ctrl/resume",
        json={"actor": "operator-bob"},
    )
    assert resume_resp.status_code == 200
    assert resume_resp.json()["status"] == WorkflowStatus.COMPLETED.value

    # 2. Pre-stage a running workflow to test pause and cancel
    run2 = WorkflowRun(
        workflow_id="wf-api-ctrl-2",
        status=WorkflowStatus.RUNNING,
        requested_by="dev-operator",
        repository_reference="repo",
        issue_reference="issue",
    )
    ctx2 = WorkflowContext(
        workflow_id="wf-api-ctrl-2",
        repository_id="repo",
        issue_reference="issue",
    )
    anyio.run(service.store.save, run2, ctx2)

    # Pause
    pause_resp = client.post(
        "/api/v1/workflows/wf-api-ctrl-2/pause",
        json={"reason": "Holding for QA verification"},
    )
    assert pause_resp.status_code == 200
    assert pause_resp.json()["status"] == WorkflowStatus.PAUSED.value

    # Cancel
    cancel_resp = client.post(
        "/api/v1/workflows/wf-api-ctrl-2/cancel",
        json={"reason": "Cancelled per sprint priorities"},
    )
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == WorkflowStatus.CANCELLED.value


@pytest.mark.anyio
async def test_end_to_end_human_approval_pause_and_resume() -> None:
    """Verify workflow pauses at human approval gate and resumes to release."""
    store = InMemoryWorkflowStore()
    event_bus = WorkflowEventBus()
    # Registry configured to pause at approval gate
    registry = _build_test_registry(pause_at_approval=True)
    executor = WorkflowExecutor(stage_registry=registry, event_bus=event_bus)
    recovery = WorkflowRecoveryManager(
        store=store,
        stage_registry=registry,
        executor=executor,
        event_bus=event_bus,
    )
    service = OrchestrationService(
        store=store,
        stage_registry=registry,
        event_bus=event_bus,
        executor=executor,
        recovery_manager=recovery,
    )

    req = WorkflowRequest(
        workflow_id="wf-human-gate-1",
        repository_id="swikarb69/AgentForge",
        issue_reference="ISSUE-888: Human Approval Gate Test",
        requested_by="ai-agent",
        target_branch="main",
    )

    # 1. Execute up to approval gate
    initial_run = await service.start_workflow(req)
    assert initial_run.status == WorkflowStatus.WAITING_FOR_APPROVAL
    assert initial_run.current_stage == StageType.HUMAN_APPROVAL
    assert StageType.CODE_REVIEW in initial_run.completed_stages
    assert StageType.CONTROLLED_RELEASE not in initial_run.completed_stages

    # 2. Simulate human approval grant: switch adapter to complete
    registry.register(
        StageDefinition(
            stage_type=StageType.HUMAN_APPROVAL,
            name="Human Approval",
            description="Enforce human authorization gate",
            prerequisites=[StageType.CODE_REVIEW],
            timeout_seconds=10,
        ),
        MockStageAdapter(StageType.HUMAN_APPROVAL, should_pause=False),
        allow_override=True,
    )

    # 3. Resume workflow to execute release
    resumed_run = await service.resume_workflow(
        "wf-human-gate-1", actor="human-approver-lead"
    )
    assert resumed_run.status == WorkflowStatus.COMPLETED
    assert StageType.HUMAN_APPROVAL in resumed_run.completed_stages
    assert StageType.CONTROLLED_RELEASE in resumed_run.completed_stages

    # Verify context has PR details and release artifacts
    ctx = await service.get_workflow_context("wf-human-gate-1")
    assert ctx is not None
    assert ctx.artifacts.release_pr_number == 42
    assert ctx.artifacts.release_pr_url is not None


def test_rest_api_error_handling(
    override_orchestration_service: tuple[OrchestrationService, StageRegistry],
) -> None:
    """Verify REST API error responses (404, 400, 409)."""
    # 1. 404 Not Found tests
    resp = client.get("/api/v1/workflows/non-existent-wf")
    assert resp.status_code == 404

    resp = client.get("/api/v1/workflows/non-existent-wf/context")
    assert resp.status_code == 404

    resp = client.post(
        "/api/v1/workflows/non-existent-wf/pause",
        json={"reason": "test"},
    )
    assert resp.status_code == 404

    resp = client.post(
        "/api/v1/workflows/non-existent-wf/resume",
        json={"actor": "test"},
    )
    assert resp.status_code == 404

    resp = client.post(
        "/api/v1/workflows/non-existent-wf/cancel",
        json={"reason": "test"},
    )
    assert resp.status_code == 404

    # 2. 400 Bad Request on invalid transitions
    # First create and complete a workflow
    create_resp = client.post(
        "/api/v1/workflows",
        json={
            "workflow_id": "wf-err-test",
            "repository_id": "repo-err",
            "issue_reference": "issue-err",
            "requested_by": "tester",
            "idempotency_key": "idem-err-1",
        },
    )
    assert create_resp.status_code == 201
    assert create_resp.json()["status"] == "completed"

    # Cannot resume, pause, or cancel a completed workflow (terminal state -> 400)
    resume_resp = client.post(
        "/api/v1/workflows/wf-err-test/resume",
        json={"actor": "tester"},
    )
    assert resume_resp.status_code == 400

    pause_resp = client.post(
        "/api/v1/workflows/wf-err-test/pause",
        json={"reason": "holding"},
    )
    assert pause_resp.status_code == 400

    cancel_resp = client.post(
        "/api/v1/workflows/wf-err-test/cancel",
        json={"reason": "holding"},
    )
    assert cancel_resp.status_code == 400

    # 3. 409 Idempotency Conflict
    conflict_resp = client.post(
        "/api/v1/workflows",
        json={
            "workflow_id": "wf-err-different-id",
            "repository_id": "repo-err-2",
            "issue_reference": "issue-err-2",
            "requested_by": "tester",
            "idempotency_key": "idem-err-1",  # Same key, different request!
        },
    )
    assert conflict_resp.status_code == 409
