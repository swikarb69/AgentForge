"""Unit tests for durable persistence backends (SQLite, File) and workflow recovery."""

from pathlib import Path

import pytest

from backend.app.orchestration.context import (
    WorkflowArtifactReferences,
    WorkflowContext,
)
from backend.app.orchestration.exceptions import (
    ConcurrencyConflictError,
    IdempotencyConflictError,
)
from backend.app.orchestration.executor import WorkflowExecutor
from backend.app.orchestration.models import (
    ExecutionLimits,
    WorkflowConfiguration,
    WorkflowRun,
    WorkflowStage,
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
from backend.app.orchestration.storage import (
    FileWorkflowStore,
    InMemoryWorkflowStore,
    SqliteWorkflowStore,
    create_workflow_store,
)


class StubRecoveryStageAdapter(WorkflowStageAdapter):
    """Stub adapter for testing recovery lifecycle execution."""

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
        self.invocations: list[StageInput] = []

    async def execute(self, stage_input: StageInput) -> StageOutput:
        self.invocations.append(stage_input)
        return self.output


def _make_sample_run(
    workflow_id: str = "wf-persist-1",
    status: WorkflowStatus = WorkflowStatus.CREATED,
    idempotency_key: str | None = None,
) -> WorkflowRun:
    """Build a deterministic sample WorkflowRun."""
    run = WorkflowRun(
        workflow_id=workflow_id,
        status=status,
        requested_by="dev-tester",
        repository_reference="repo-test",
        issue_reference="issue-test",
        target_branch="main",
        idempotency_key=idempotency_key,
    )
    # Populate a couple sample stages
    run.stages[StageType.REPOSITORY_ANALYSIS] = WorkflowStage(
        stage_type=StageType.REPOSITORY_ANALYSIS,
        status=StageStatus.PENDING,
        order=1,
    )
    run.stages[StageType.ISSUE_ANALYSIS] = WorkflowStage(
        stage_type=StageType.ISSUE_ANALYSIS,
        status=StageStatus.PENDING,
        order=2,
    )
    return run


def _make_sample_context(
    workflow_id: str = "wf-persist-1",
    artifacts: WorkflowArtifactReferences | None = None,
    tmp_path: Path | None = None,
) -> WorkflowContext:
    """Build a deterministic sample WorkflowContext."""
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
async def test_sqlite_persistence_across_instances(tmp_path: Path) -> None:
    """Verify workflow run and context persist to SQLite and recover on new store."""
    db_file = tmp_path / "test_workflows.db"
    store1 = SqliteWorkflowStore(db_path=db_file)

    run = _make_sample_run("wf-sqlite-1", idempotency_key="key-sqlite-1")
    ctx = _make_sample_context("wf-sqlite-1")
    ctx.shared_data["custom_note"] = "persistence verified"

    await store1.save(run, ctx)
    assert await store1.exists("wf-sqlite-1")
    store1.close()

    # Open a completely new instance pointing to the same SQLite database
    store2 = SqliteWorkflowStore(db_path=db_file)
    try:
        assert await store2.exists("wf-sqlite-1")
        item = await store2.get("wf-sqlite-1")
        assert item is not None
        recovered_run, recovered_ctx = item

        assert recovered_run.workflow_id == "wf-sqlite-1"
        assert recovered_run.idempotency_key == "key-sqlite-1"
        assert recovered_run.version == 1
        assert StageType.REPOSITORY_ANALYSIS in recovered_run.stages
        assert recovered_ctx.shared_data["custom_note"] == "persistence verified"

        # Check get_run and get_context individual methods
        r_only = await store2.get_run("wf-sqlite-1")
        assert r_only is not None and r_only.workflow_id == "wf-sqlite-1"
        c_only = await store2.get_context("wf-sqlite-1")
        assert c_only is not None and c_only.workflow_id == "wf-sqlite-1"
    finally:
        store2.close()


@pytest.mark.anyio
async def test_active_workflow_recovery_after_simulated_crash(
    tmp_path: Path,
) -> None:
    """Verify in-flight crashed workflow is recovered cleanly after restart."""
    db_file = tmp_path / "crash_test.db"
    store1 = SqliteWorkflowStore(db_path=db_file)

    valid_hex_hash = "a" * 64
    artifacts = WorkflowArtifactReferences(
        repository_analysis_id="rep-001",
        repository_analysis_hash=valid_hex_hash,
    )

    run = _make_sample_run("wf-crash-1", status=WorkflowStatus.RUNNING)
    run.stages[StageType.REPOSITORY_ANALYSIS].status = StageStatus.COMPLETED
    run.stages[StageType.ISSUE_ANALYSIS].status = StageStatus.RUNNING
    context = _make_sample_context("wf-crash-1", artifacts=artifacts)

    await store1.save(run, context)
    store1.close()

    # Simulate process restart by creating a new store and recovery manager
    store2 = SqliteWorkflowStore(db_path=db_file)
    try:
        registry = StageRegistry()
        adapter = StubRecoveryStageAdapter(
            stage_type=StageType.ISSUE_ANALYSIS,
            output=StageOutput(
                stage_type=StageType.ISSUE_ANALYSIS,
                status=StageStatus.COMPLETED,
                produced_artifact_ids=["issue-art-1"],
            ),
        )
        stage_def = StageDefinition(
            stage_type=StageType.ISSUE_ANALYSIS,
            name="Issue Analysis",
            description="Analyze issue",
        )
        registry.register(stage_def, adapter)
        executor = WorkflowExecutor(stage_registry=registry)
        recovery_manager = WorkflowRecoveryManager(
            store=store2,
            stage_registry=registry,
            executor=executor,
        )

        recovered_run = await recovery_manager.recover("wf-crash-1")
        assert recovered_run.status == WorkflowStatus.COMPLETED
        assert (
            recovered_run.stages[StageType.ISSUE_ANALYSIS].status
            == StageStatus.COMPLETED
        )
    finally:
        store2.close()


@pytest.mark.anyio
async def test_sqlite_idempotency_persistence_and_conflict_across_instances(
    tmp_path: Path,
) -> None:
    """Verify idempotency keys persist in SQLite and detect duplicate submissions."""
    db_file = tmp_path / "idemp_test.db"
    store1 = SqliteWorkflowStore(db_path=db_file)

    run1 = _make_sample_run("wf-idemp-1", idempotency_key="idemp-shared-key")
    ctx1 = _make_sample_context("wf-idemp-1")
    await store1.save(run1, ctx1)
    store1.close()

    # Second store instance
    store2 = SqliteWorkflowStore(db_path=db_file)
    try:
        # Retrieve by key across store instance
        found_run = await store2.get_by_idempotency_key("idemp-shared-key")
        assert found_run is not None
        assert found_run.workflow_id == "wf-idemp-1"

        # Attempt to save a DIFFERENT workflow with the same key
        run2 = _make_sample_run("wf-idemp-2", idempotency_key="idemp-shared-key")
        ctx2 = _make_sample_context("wf-idemp-2")
        with pytest.raises(IdempotencyConflictError, match="is already associated"):
            await store2.save(run2, ctx2)
    finally:
        store2.close()


@pytest.mark.anyio
async def test_sqlite_lock_acquisition_and_release_across_instances(
    tmp_path: Path,
) -> None:
    """Verify exclusive lock coordination across different store instances."""
    db_file = tmp_path / "lock_test.db"
    store1 = SqliteWorkflowStore(db_path=db_file)
    store2 = SqliteWorkflowStore(db_path=db_file)

    try:
        # Store1 acquires lock
        async with store1.lock("wf-lock-1", timeout_seconds=2.0):
            # Store2 attempts to acquire same workflow lock -> should timeout and raise
            with pytest.raises(
                ConcurrencyConflictError, match="Timed out acquiring database lock"
            ):
                async with store2.lock("wf-lock-1", timeout_seconds=0.1):
                    pass

        # Now that store1 released, store2 must acquire successfully
        acquired = False
        async with store2.lock("wf-lock-1", timeout_seconds=1.0):
            acquired = True
        assert acquired is True
    finally:
        store1.close()
        store2.close()


@pytest.mark.anyio
async def test_sqlite_concurrency_conflict_on_version(tmp_path: Path) -> None:
    """Verify optimistic concurrency version conflict checking in SQLite store."""
    db_file = tmp_path / "version_test.db"
    store = SqliteWorkflowStore(db_path=db_file)

    try:
        run = _make_sample_run("wf-ver-1")
        ctx = _make_sample_context("wf-ver-1")
        await store.save(run, ctx)
        assert run.version == 1

        # Save with correct expected version
        await store.save(run, ctx, expected_version=1)
        assert run.version == 2

        # Save with stale expected version -> raises ConcurrencyConflictError
        with pytest.raises(
            ConcurrencyConflictError, match="expected version 1, but found 2"
        ):
            await store.save(run, ctx, expected_version=1)
    finally:
        store.close()


@pytest.mark.anyio
async def test_sqlite_list_count_and_delete(tmp_path: Path) -> None:
    """Verify list pagination, count, and delete operations in SQLite store."""
    db_file = tmp_path / "crud_test.db"
    store = SqliteWorkflowStore(db_path=db_file)

    try:
        run1 = _make_sample_run("wf-crud-1", status=WorkflowStatus.COMPLETED)
        run2 = _make_sample_run("wf-crud-2", status=WorkflowStatus.FAILED)
        ctx = _make_sample_context("wf-crud-1")

        await store.save(run1, ctx)
        await store.save(run2, ctx)

        assert await store.count() == 2
        assert await store.count(status=WorkflowStatus.COMPLETED) == 1
        assert await store.count(status=WorkflowStatus.FAILED) == 1

        listed = await store.list(limit=10)
        assert len(listed) == 2

        # Delete run1
        deleted = await store.delete("wf-crud-1")
        assert deleted is True
        assert not await store.exists("wf-crud-1")
        assert await store.count() == 1
    finally:
        store.close()


@pytest.mark.anyio
async def test_file_workflow_store_persistence_and_recovery(
    tmp_path: Path,
) -> None:
    """Verify FileWorkflowStore persists workflows and recovers across instances."""
    data_dir = tmp_path / "file_store_data"
    store1 = FileWorkflowStore(directory=data_dir)

    run = _make_sample_run("wf-file-1", idempotency_key="idemp-file-key")
    ctx = _make_sample_context("wf-file-1")
    ctx.shared_data["storage_type"] = "filesystem"

    await store1.save(run, ctx)
    assert await store1.exists("wf-file-1")

    # Second file store instance on same directory
    store2 = FileWorkflowStore(directory=data_dir)
    assert await store2.exists("wf-file-1")
    item = await store2.get("wf-file-1")
    assert item is not None
    recovered_run, recovered_ctx = item
    assert recovered_run.workflow_id == "wf-file-1"
    assert recovered_ctx.shared_data["storage_type"] == "filesystem"

    # Idempotency lookup
    idemp_run = await store2.get_by_idempotency_key("idemp-file-key")
    assert idemp_run is not None
    assert idemp_run.workflow_id == "wf-file-1"

    # Idempotency conflict
    run_dup = _make_sample_run("wf-file-2", idempotency_key="idemp-file-key")
    ctx_dup = _make_sample_context("wf-file-2")
    with pytest.raises(IdempotencyConflictError, match="is already associated"):
        await store2.save(run_dup, ctx_dup)

    # Listing and count
    assert await store2.count() == 1
    runs = await store2.list(limit=5)
    assert len(runs) == 1

    # Delete
    assert await store2.delete("wf-file-1") is True
    assert await store2.count() == 0


@pytest.mark.anyio
async def test_file_workflow_store_corrupted_file_error_handling(
    tmp_path: Path,
) -> None:
    """Verify FileWorkflowStore detects and reports corrupted files cleanly."""
    data_dir = tmp_path / "corrupt_data"
    store = FileWorkflowStore(directory=data_dir)

    # Write corrupt JSON directly to workflow file
    wf_file = data_dir / "wf-corrupted.json"
    wf_file.write_text("{invalid_json: true, broken", encoding="utf-8")

    with pytest.raises(ValueError, match="Failed to load corrupted workflow"):
        await store.get("wf-corrupted")


def test_create_workflow_store_factory(tmp_path: Path) -> None:
    """Verify create_workflow_store resolves memory, sqlite, and file backends."""
    mem_store = create_workflow_store("memory")
    assert isinstance(mem_store, InMemoryWorkflowStore)

    sqlite_store = create_workflow_store("sqlite", db_path=tmp_path / "factory.db")
    assert isinstance(sqlite_store, SqliteWorkflowStore)
    sqlite_store.close()

    file_store = create_workflow_store("file", directory=tmp_path / "wf_data")
    assert isinstance(file_store, FileWorkflowStore)

    with pytest.raises(ValueError, match="Unsupported storage backend"):
        create_workflow_store("redis-unsupported")
