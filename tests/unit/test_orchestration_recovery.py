"""Unit tests for workflow storage, concurrency, recovery, and lifecycle controls."""

import hashlib
from pathlib import Path

import anyio
import pytest

from backend.app.orchestration.context import (
    WorkflowArtifactReferences,
    WorkflowContext,
)
from backend.app.orchestration.exceptions import (
    ConcurrencyConflictError,
    IdempotencyConflictError,
    InvalidWorkflowStateTransitionError,
    WorkflowArtifactIntegrityError,
    WorkflowNotFoundError,
)
from backend.app.orchestration.executor import WorkflowExecutor
from backend.app.orchestration.models import (
    ExecutionLimits,
    WorkflowConfiguration,
    WorkflowRun,
    WorkflowStage,
)
from backend.app.orchestration.recovery import (
    WorkflowRecoveryManager,
    verify_artifact_integrity,
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


class StubStageAdapter(WorkflowStageAdapter):
    """Stub adapter returning configurable stage outputs."""

    def __init__(
        self,
        output: StageOutput | None = None,
        stage_type: StageType = StageType.REPOSITORY_ANALYSIS,
    ) -> None:
        self.output = output or StageOutput(
            stage_type=stage_type,
            status=StageStatus.COMPLETED,
            produced_artifact_ids=["test-artifact-1"],
        )
        self.invocations: list[StageInput] = []

    async def execute(self, stage_input: StageInput) -> StageOutput:
        self.invocations.append(stage_input)
        return self.output


def _make_run(
    workflow_id: str = "wf-test-1",
    status: WorkflowStatus = WorkflowStatus.CREATED,
    idempotency_key: str | None = None,
) -> WorkflowRun:
    return WorkflowRun(
        workflow_id=workflow_id,
        status=status,
        requested_by="dev-tester",
        repository_reference="repo-test",
        issue_reference="issue-test",
        target_branch="main",
        idempotency_key=idempotency_key,
    )


def _make_context(
    workflow_id: str = "wf-test-1",
    artifacts: WorkflowArtifactReferences | None = None,
    tmp_path: Path | None = None,
) -> WorkflowContext:
    return WorkflowContext(
        workflow_id=workflow_id,
        repository_id="repo-test",
        issue_reference="issue-test",
        target_branch="main",
        repository_path=tmp_path,
        configuration=WorkflowConfiguration(
            limits=ExecutionLimits(
                stage_timeout_seconds=10,
                workflow_timeout_seconds=60,
            )
        ),
        artifacts=artifacts or WorkflowArtifactReferences(),
    )


@pytest.mark.anyio
async def test_store_save_and_get() -> None:
    """Store persists and retrieves run and context."""
    store = InMemoryWorkflowStore()
    run = _make_run("wf-1")
    ctx = _make_context("wf-1")

    await store.save(run, ctx)

    item = await store.get("wf-1")
    assert item is not None
    saved_run, saved_ctx = item
    assert saved_run.workflow_id == "wf-1"
    assert saved_run.version == 1
    assert saved_ctx.workflow_id == "wf-1"

    # Deep copy isolation check
    saved_run.status = WorkflowStatus.FAILED
    fetched_again = await store.get_run("wf-1")
    assert fetched_again is not None
    assert fetched_again.status == WorkflowStatus.CREATED


@pytest.mark.anyio
async def test_store_concurrency_conflict_on_version() -> None:
    """Expected version mismatch raises ConcurrencyConflictError."""
    store = InMemoryWorkflowStore()
    run = _make_run("wf-cc")
    ctx = _make_context("wf-cc")

    await store.save(run, ctx)
    assert run.version == 1

    # Second save with wrong expected version
    with pytest.raises(ConcurrencyConflictError, match="expected version 99"):
        await store.save(run, ctx, expected_version=99)


@pytest.mark.anyio
async def test_store_idempotency_key_enforcement() -> None:
    """Idempotency key prevents duplicate workflow creation."""
    store = InMemoryWorkflowStore()
    run1 = _make_run("wf-idem-1", idempotency_key="key-abc")
    ctx1 = _make_context("wf-idem-1")

    await store.save(run1, ctx1)
    retrieved = await store.get_by_idempotency_key("key-abc")
    assert retrieved is not None
    assert retrieved.workflow_id == "wf-idem-1"

    # Same key with same workflow_id succeeds
    run1.requested_by = "updated-requester"
    await store.save(run1, ctx1)
    updated = await store.get_by_idempotency_key("key-abc")
    assert updated is not None
    assert updated.requested_by == "updated-requester"

    # Same key with DIFFERENT workflow_id raises IdempotencyConflictError
    run2 = _make_run("wf-idem-2", idempotency_key="key-abc")
    ctx2 = _make_context("wf-idem-2")
    with pytest.raises(IdempotencyConflictError, match="already associated"):
        await store.save(run2, ctx2)


@pytest.mark.anyio
async def test_store_list_count_and_delete() -> None:
    """Listing, pagination, counting, and deletion work as expected."""
    store = InMemoryWorkflowStore()

    for i in range(5):
        run = _make_run(
            f"wf-{i}",
            status=WorkflowStatus.RUNNING if i % 2 == 0 else WorkflowStatus.PAUSED,
        )
        ctx = _make_context(f"wf-{i}")
        await store.save(run, ctx)

    assert await store.count() == 5
    assert await store.count(status=WorkflowStatus.RUNNING) == 3
    assert await store.count(status=WorkflowStatus.PAUSED) == 2

    # Pagination
    page = await store.list(limit=2, offset=0)
    assert len(page) == 2

    # Delete
    deleted = await store.delete("wf-0")
    assert deleted is True
    assert await store.exists("wf-0") is False
    assert await store.count() == 4


@pytest.mark.anyio
async def test_store_exclusive_lock() -> None:
    """Exclusive lock serializes operations across concurrent tasks."""
    store = InMemoryWorkflowStore()
    run = _make_run("wf-locked")
    ctx = _make_context("wf-locked")
    await store.save(run, ctx)

    events: list[str] = []

    async def task_a() -> None:
        async with store.lock("wf-locked", timeout_seconds=2.0):
            events.append("a_start")
            await anyio.sleep(0.05)
            events.append("a_end")

    async def task_b() -> None:
        await anyio.sleep(0.01)
        async with store.lock("wf-locked", timeout_seconds=2.0):
            events.append("b_start")
            events.append("b_end")

    async with anyio.create_task_group() as tg:
        tg.start_soon(task_a)
        tg.start_soon(task_b)

    assert events == ["a_start", "a_end", "b_start", "b_end"]


def test_verify_artifact_integrity_success(tmp_path: Path) -> None:
    """Verification passes when all completed stage artifacts are present & valid."""
    patch_file = tmp_path / "patch.diff"
    patch_content = b"diff --git a/foo.py b/foo.py\n+print('hello')\n"
    patch_file.write_bytes(patch_content)
    patch_hash = hashlib.sha256(patch_content).hexdigest()

    artifacts = WorkflowArtifactReferences(
        repository_analysis_id="prof-1",
        repository_analysis_hash="a" * 64,
        issue_analysis_id="issue-1",
        issue_analysis_hash="b" * 64,
        implementation_plan_id="plan-1",
        implementation_plan_hash="c" * 64,
        generation_id="patch-1",
        generation_patch_hash=patch_hash,
        review_id="review-1",
        review_hash="d" * 64,
        approval_id="appr-1",
        approval_hash="e" * 64,
    )

    run = _make_run("wf-verify")
    run.completed_stages = [
        StageType.REPOSITORY_ANALYSIS,
        StageType.ISSUE_ANALYSIS,
        StageType.IMPLEMENTATION_PLANNING,
        StageType.CODE_GENERATION,
        StageType.CODE_REVIEW,
        StageType.HUMAN_APPROVAL,
    ]
    ctx = _make_context("wf-verify", artifacts=artifacts, tmp_path=tmp_path)
    ctx.shared_data["patch_path"] = str(patch_file)

    # Should not raise
    verify_artifact_integrity(run, ctx)


def test_verify_artifact_integrity_missing_artifact() -> None:
    """Missing artifact for completed stage raises WorkflowArtifactIntegrityError."""
    run = _make_run("wf-missing")
    run.completed_stages = [StageType.REPOSITORY_ANALYSIS]
    ctx = _make_context("wf-missing")

    with pytest.raises(
        WorkflowArtifactIntegrityError, match="repository_analysis_id is missing"
    ):
        verify_artifact_integrity(run, ctx)


def test_verify_artifact_integrity_corrupted_hash(tmp_path: Path) -> None:
    """Disk content sha256 mismatch raises WorkflowArtifactIntegrityError."""
    patch_file = tmp_path / "corrupt.diff"
    patch_file.write_bytes(b"corrupted content")

    artifacts = WorkflowArtifactReferences(
        generation_id="patch-corrupt",
        generation_patch_hash="a" * 64,  # mismatched hash
    )
    run = _make_run("wf-corrupt")
    run.completed_stages = [StageType.CODE_GENERATION]
    ctx = _make_context("wf-corrupt", artifacts=artifacts, tmp_path=tmp_path)
    ctx.shared_data["patch_path"] = str(patch_file)

    with pytest.raises(
        WorkflowArtifactIntegrityError, match="Artifact hash mismatch on disk"
    ):
        verify_artifact_integrity(run, ctx)


@pytest.mark.anyio
async def test_recovery_manager_pause_and_cancel() -> None:
    """Pause and cancel transition workflow correctly and update store."""
    store = InMemoryWorkflowStore()
    run = _make_run("wf-lifecycle", status=WorkflowStatus.RUNNING)
    run.stages["repository_analysis"] = WorkflowStage(
        stage_type=StageType.REPOSITORY_ANALYSIS, status=StageStatus.RUNNING
    )
    ctx = _make_context("wf-lifecycle")
    await store.save(run, ctx)

    manager = WorkflowRecoveryManager(store=store)

    # 1. Pause
    paused_run = await manager.pause("wf-lifecycle", reason="Paused for inspection")
    assert paused_run.status == WorkflowStatus.PAUSED
    persisted_paused = await store.get_run("wf-lifecycle")
    assert persisted_paused is not None
    assert persisted_paused.status == WorkflowStatus.PAUSED

    # Pausing again is idempotent
    again = await manager.pause("wf-lifecycle")
    assert again.status == WorkflowStatus.PAUSED

    # 2. Cancel
    cancelled_run = await manager.cancel("wf-lifecycle", reason="Cancelled by user")
    assert cancelled_run.status == WorkflowStatus.CANCELLED
    assert cancelled_run.cancellation_reason == "Cancelled by user"
    assert (
        cancelled_run.stages["repository_analysis"].status == StageStatus.CANCELLED
    )

    # Cannot pause or cancel a terminal workflow
    with pytest.raises(
        InvalidWorkflowStateTransitionError, match="terminal state"
    ):
        await manager.pause("wf-lifecycle")


@pytest.mark.anyio
async def test_recovery_manager_resume_happy_path() -> None:
    """Resume executes remaining stages after verifying integrity."""
    store = InMemoryWorkflowStore()
    run = _make_run("wf-resume", status=WorkflowStatus.PAUSED)
    run.completed_stages = [StageType.REPOSITORY_ANALYSIS]

    artifacts = WorkflowArtifactReferences(repository_analysis_id="prof-1")
    ctx = _make_context("wf-resume", artifacts=artifacts)
    await store.save(run, ctx)

    # Custom registry with remaining stages as stubs
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
    registry.register(
        StageDefinition(
            stage_type=StageType.ISSUE_ANALYSIS,
            name="Issue Analysis",
            description="Analyze issue",
            prerequisites=[StageType.REPOSITORY_ANALYSIS],
            timeout_seconds=10,
        ),
        StubStageAdapter(
            output=StageOutput(
                stage_type=StageType.ISSUE_ANALYSIS,
                status=StageStatus.COMPLETED,
                produced_artifact_ids=["issue-resumed"],
            )
        ),
    )

    executor = WorkflowExecutor(stage_registry=registry)
    manager = WorkflowRecoveryManager(
        store=store, stage_registry=registry, executor=executor
    )

    resumed = await manager.resume("wf-resume")
    assert resumed.status == WorkflowStatus.COMPLETED
    assert StageType.ISSUE_ANALYSIS in resumed.completed_stages


@pytest.mark.anyio
async def test_recovery_manager_recover_crashed_workflow() -> None:
    """Recover resets interrupted running stage to PENDING and completes."""
    store = InMemoryWorkflowStore()
    run = _make_run("wf-crash", status=WorkflowStatus.RUNNING)
    stage = run.get_or_create_stage(StageType.REPOSITORY_ANALYSIS)
    stage.status = StageStatus.RUNNING  # Interrupted mid-execution
    ctx = _make_context("wf-crash")
    await store.save(run, ctx)

    registry = StageRegistry()
    registry.register(
        StageDefinition(
            stage_type=StageType.REPOSITORY_ANALYSIS,
            name="Repository Analysis",
            description="Analyze repo",
            timeout_seconds=10,
        ),
        StubStageAdapter(
            output=StageOutput(
                stage_type=StageType.REPOSITORY_ANALYSIS,
                status=StageStatus.COMPLETED,
                produced_artifact_ids=["recovered-prof"],
            )
        ),
    )

    executor = WorkflowExecutor(stage_registry=registry)
    manager = WorkflowRecoveryManager(
        store=store, stage_registry=registry, executor=executor
    )

    recovered = await manager.recover("wf-crash")
    assert recovered.status == WorkflowStatus.COMPLETED
    assert StageType.REPOSITORY_ANALYSIS in recovered.completed_stages


@pytest.mark.anyio
async def test_recovery_manager_not_found_raises() -> None:
    """Operations on non-existent workflow raise WorkflowNotFoundError."""
    store = InMemoryWorkflowStore()
    manager = WorkflowRecoveryManager(store=store)

    with pytest.raises(
        WorkflowNotFoundError, match="Workflow 'wf-nonexistent' not found"
    ):
        await manager.pause("wf-nonexistent")

    with pytest.raises(
        WorkflowNotFoundError, match="Workflow 'wf-nonexistent' not found"
    ):
        await manager.cancel("wf-nonexistent")

    with pytest.raises(
        WorkflowNotFoundError, match="Workflow 'wf-nonexistent' not found"
    ):
        await manager.resume("wf-nonexistent")

    with pytest.raises(
        WorkflowNotFoundError, match="Workflow 'wf-nonexistent' not found"
    ):
        await manager.recover("wf-nonexistent")


@pytest.mark.anyio
async def test_recovery_manager_resume_active_conflict() -> None:
    """Resuming an already RUNNING workflow raises ConcurrencyConflictError."""
    store = InMemoryWorkflowStore()
    run = _make_run("wf-running", status=WorkflowStatus.RUNNING)
    ctx = _make_context("wf-running")
    await store.save(run, ctx)

    manager = WorkflowRecoveryManager(store=store)
    with pytest.raises(ConcurrencyConflictError, match="currently active"):
        await manager.resume("wf-running")


@pytest.mark.anyio
async def test_recovery_manager_resume_integrity_failure(tmp_path: Path) -> None:
    """Resuming with a tampered artifact raises WorkflowArtifactIntegrityError."""
    store = InMemoryWorkflowStore()
    patch_file = tmp_path / "tampered.diff"
    patch_file.write_bytes(b"tampered content")

    artifacts = WorkflowArtifactReferences(
        generation_id="patch-tampered",
        generation_patch_hash="f" * 64,
    )
    run = _make_run("wf-tampered", status=WorkflowStatus.PAUSED)
    run.completed_stages = [StageType.CODE_GENERATION]
    ctx = _make_context("wf-tampered", artifacts=artifacts, tmp_path=tmp_path)
    ctx.shared_data["patch_path"] = str(patch_file)
    await store.save(run, ctx)

    manager = WorkflowRecoveryManager(store=store)
    with pytest.raises(
        WorkflowArtifactIntegrityError, match="Artifact hash mismatch"
    ):
        await manager.resume("wf-tampered")


@pytest.mark.anyio
async def test_recovery_manager_cancel_terminal_and_idempotent() -> None:
    """Cancelling a terminal workflow raises, and cancelling cancelled is idempotent."""
    store = InMemoryWorkflowStore()
    run = _make_run("wf-term", status=WorkflowStatus.COMPLETED)
    ctx = _make_context("wf-term")
    await store.save(run, ctx)

    manager = WorkflowRecoveryManager(store=store)
    with pytest.raises(InvalidWorkflowStateTransitionError, match="terminal state"):
        await manager.cancel("wf-term")

    # Already cancelled is idempotent
    run_canc = _make_run("wf-canc", status=WorkflowStatus.CANCELLED)
    ctx_canc = _make_context("wf-canc")
    await store.save(run_canc, ctx_canc)
    res = await manager.cancel("wf-canc")
    assert res.status == WorkflowStatus.CANCELLED
