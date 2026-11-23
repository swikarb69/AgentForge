"""Unit tests for workflow events, sensitive data redaction, and event bus."""

import pytest

from backend.app.orchestration.context import (
    WorkflowArtifactReferences,
    WorkflowContext,
)
from backend.app.orchestration.event_bus import WorkflowEventBus
from backend.app.orchestration.events import (
    WorkflowEvent,
    WorkflowEventType,
    redact_sensitive_data,
)
from backend.app.orchestration.executor import WorkflowExecutor
from backend.app.orchestration.models import (
    ExecutionLimits,
    WorkflowConfiguration,
    WorkflowRun,
)
from backend.app.orchestration.recovery import WorkflowRecoveryManager
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


class StubStageAdapter(WorkflowStageAdapter):
    """Stub adapter for testing event bus integration."""

    def __init__(
        self,
        output: StageOutput | None = None,
        stage_type: StageType = StageType.REPOSITORY_ANALYSIS,
    ) -> None:
        self.output = output or StageOutput(
            stage_type=stage_type,
            status=StageStatus.COMPLETED,
            produced_artifact_ids=["art-1"],
        )

    async def execute(self, stage_input: StageInput) -> StageOutput:
        return self.output


def test_redact_sensitive_data() -> None:
    """Sensitive keys are redacted while safe keys remain untouched."""
    raw = {
        "user": "alice",
        "api_key": "secret-12345",
        "github_token": "ghp_abcdef",
        "db_password": "supersecretpassword",
        "nested": {
            "auth_header": "Bearer xyz",
            "safe_count": 42,
            "sub_list": [
                {"private_key": "-----BEGIN RSA PRIVATE KEY-----", "name": "cert"},
                {"public_data": True},
            ],
        },
    }

    sanitized = redact_sensitive_data(raw)
    assert sanitized["user"] == "alice"
    assert sanitized["api_key"] == "[REDACTED]"
    assert sanitized["github_token"] == "[REDACTED]"  # noqa: S105
    assert sanitized["db_password"] == "[REDACTED]"  # noqa: S105
    assert sanitized["nested"]["auth_header"] == "[REDACTED]"
    assert sanitized["nested"]["safe_count"] == 42
    assert sanitized["nested"]["sub_list"][0]["private_key"] == "[REDACTED]"
    assert sanitized["nested"]["sub_list"][0]["name"] == "cert"
    assert sanitized["nested"]["sub_list"][1]["public_data"] is True


def test_workflow_event_model_defaults_and_sanitization() -> None:
    """WorkflowEvent defaults correlation_id and automatically redacts secrets."""
    event = WorkflowEvent(
        event_type=WorkflowEventType.WORKFLOW_STARTED,
        workflow_id="wf-100",
        payload={
            "safe_info": "ok",
            "access_token": "sensitive-token",
        },
    )

    assert event.event_id.startswith("evt-")
    assert event.correlation_id == "wf-100"
    assert event.payload["safe_info"] == "ok"
    assert event.payload["access_token"] == "[REDACTED]"  # noqa: S105


@pytest.mark.anyio
async def test_event_bus_publish_subscribe() -> None:
    """Event bus routes events to subscribed handlers based on filter."""
    bus = WorkflowEventBus()
    all_events: list[WorkflowEvent] = []
    filtered_events: list[WorkflowEvent] = []

    async def on_all(evt: WorkflowEvent) -> None:
        all_events.append(evt)

    async def on_filtered(evt: WorkflowEvent) -> None:
        filtered_events.append(evt)

    bus.subscribe(on_all)
    bus.subscribe(on_filtered, event_types={WorkflowEventType.STAGE_COMPLETED})

    evt1 = WorkflowEvent(
        event_type=WorkflowEventType.STAGE_STARTED,
        workflow_id="wf-1",
        stage_type=StageType.REPOSITORY_ANALYSIS,
    )
    evt2 = WorkflowEvent(
        event_type=WorkflowEventType.STAGE_COMPLETED,
        workflow_id="wf-1",
        stage_type=StageType.REPOSITORY_ANALYSIS,
    )

    await bus.publish(evt1)
    await bus.publish(evt2)

    assert len(all_events) == 2
    assert len(filtered_events) == 1
    assert filtered_events[0].event_type == WorkflowEventType.STAGE_COMPLETED

    # Unsubscribe test
    assert bus.unsubscribe(on_all) is True
    await bus.publish(evt1)
    assert len(all_events) == 2  # unchanged


@pytest.mark.anyio
async def test_event_bus_subscriber_error_resilience() -> None:
    """Exception in a subscriber callback does not abort publishing to others."""
    bus = WorkflowEventBus()
    successful_events: list[WorkflowEvent] = []

    async def buggy_subscriber(_: WorkflowEvent) -> None:
        raise RuntimeError("Subscriber explosion!")

    async def healthy_subscriber(evt: WorkflowEvent) -> None:
        successful_events.append(evt)

    bus.subscribe(buggy_subscriber)
    bus.subscribe(healthy_subscriber)

    evt = WorkflowEvent(
        event_type=WorkflowEventType.WORKFLOW_STARTED,
        workflow_id="wf-resilience",
    )
    # Must not raise
    await bus.publish(evt)

    assert len(successful_events) == 1
    assert successful_events[0].workflow_id == "wf-resilience"


@pytest.mark.anyio
async def test_event_bus_history_query_and_buffer_limit() -> None:
    """Event history query supports filtering by workflow, type, and limits buffer."""
    bus = WorkflowEventBus(max_history_size=5)

    for i in range(10):
        evt = WorkflowEvent(
            event_type=(
                WorkflowEventType.STAGE_STARTED
                if i % 2 == 0
                else WorkflowEventType.STAGE_COMPLETED
            ),
            workflow_id=f"wf-{i % 2}",
        )
        await bus.publish(evt)

    # Buffer capped at max_history_size=5
    all_stored = await bus.get_events()
    assert len(all_stored) == 5

    # Filter by workflow_id
    wf0_events = await bus.get_events(workflow_id="wf-0")
    assert all(e.workflow_id == "wf-0" for e in wf0_events)

    # Filter by event_types
    completed_events = await bus.get_events(
        event_types={WorkflowEventType.STAGE_COMPLETED}
    )
    assert all(
        e.event_type == WorkflowEventType.STAGE_COMPLETED for e in completed_events
    )

    # Clear history
    await bus.clear_history()
    assert len(await bus.get_events()) == 0


@pytest.mark.anyio
async def test_executor_event_emission() -> None:
    """WorkflowExecutor emits lifecycle events throughout workflow execution."""
    bus = WorkflowEventBus()
    emitted: list[WorkflowEvent] = []

    async def record_event(evt: WorkflowEvent) -> None:
        emitted.append(evt)

    bus.subscribe(record_event)

    registry = StageRegistry()
    registry.register(
        StageDefinition(
            stage_type=StageType.REPOSITORY_ANALYSIS,
            name="Repository Analysis",
            description="Analyze repo",
            timeout_seconds=10,
        ),
        StubStageAdapter(),
    )

    executor = WorkflowExecutor(stage_registry=registry, event_bus=bus)

    run = WorkflowRun(
        workflow_id="wf-evt-exec",
        requested_by="dev-tester",
        repository_reference="repo-test",
        issue_reference="issue-test",
    )
    ctx = WorkflowContext(
        workflow_id="wf-evt-exec",
        repository_id="repo-test",
        issue_reference="issue-test",
        configuration=WorkflowConfiguration(
            limits=ExecutionLimits(
                stage_timeout_seconds=10,
                workflow_timeout_seconds=60,
            )
        ),
    )

    await executor.execute(run, ctx)

    types = [e.event_type for e in emitted]
    assert WorkflowEventType.WORKFLOW_STARTED in types
    assert WorkflowEventType.STAGE_STARTED in types
    assert WorkflowEventType.STAGE_COMPLETED in types
    assert WorkflowEventType.WORKFLOW_COMPLETED in types


@pytest.mark.anyio
async def test_recovery_manager_event_emission() -> None:
    """WorkflowRecoveryManager emits pause, resume, and cancellation events."""
    bus = WorkflowEventBus()
    emitted: list[WorkflowEvent] = []

    async def record_event(evt: WorkflowEvent) -> None:
        emitted.append(evt)

    bus.subscribe(record_event)

    store = InMemoryWorkflowStore()
    run = WorkflowRun(
        workflow_id="wf-evt-recovery",
        status=WorkflowStatus.RUNNING,
        requested_by="operator-1",
        repository_reference="repo",
        issue_reference="issue",
    )
    ctx = WorkflowContext(
        workflow_id="wf-evt-recovery",
        repository_id="repo",
        issue_reference="issue",
        artifacts=WorkflowArtifactReferences(repository_analysis_id="prof-1"),
    )
    await store.save(run, ctx)

    registry = StageRegistry()
    registry.register(
        StageDefinition(
            stage_type=StageType.REPOSITORY_ANALYSIS,
            name="Repository Analysis",
            description="Analyze repo",
            timeout_seconds=10,
        ),
        StubStageAdapter(),
    )
    executor = WorkflowExecutor(stage_registry=registry, event_bus=bus)
    manager = WorkflowRecoveryManager(
        store=store, stage_registry=registry, executor=executor, event_bus=bus
    )

    # 1. Pause
    await manager.pause("wf-evt-recovery", reason="Paused for inspection")
    # 2. Cancel
    await manager.cancel("wf-evt-recovery", reason="Cancelled by user")

    # 3. Resume on a separate paused workflow
    run2 = WorkflowRun(
        workflow_id="wf-evt-resume",
        status=WorkflowStatus.PAUSED,
        requested_by="operator-1",
        repository_reference="repo",
        issue_reference="issue",
    )
    ctx2 = WorkflowContext(
        workflow_id="wf-evt-resume",
        repository_id="repo",
        issue_reference="issue",
        artifacts=WorkflowArtifactReferences(repository_analysis_id="prof-1"),
    )
    await store.save(run2, ctx2)
    await manager.resume("wf-evt-resume")

    types = [e.event_type for e in emitted]
    assert WorkflowEventType.WORKFLOW_PAUSED in types
    assert WorkflowEventType.WORKFLOW_CANCELLED in types
    assert WorkflowEventType.WORKFLOW_RESUMED in types
