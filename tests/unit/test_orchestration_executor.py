"""Unit tests for the central WorkflowExecutor engine."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import anyio
import pytest

from backend.app.orchestration.context import WorkflowContext
from backend.app.orchestration.exceptions import WorkflowExecutionError
from backend.app.orchestration.executor import WorkflowExecutor
from backend.app.orchestration.models import (
    ExecutionLimits,
    WorkflowConfiguration,
    WorkflowRun,
)
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


class MockStageAdapter(WorkflowStageAdapter):
    """Configurable mock adapter for testing executor behaviors."""

    def __init__(
        self,
        output: StageOutput | None = None,
        exception: Exception | None = None,
        delay_seconds: float = 0.0,
    ) -> None:
        self.output = output or StageOutput(
            stage_type=StageType.REPOSITORY_ANALYSIS,
            status=StageStatus.COMPLETED,
            produced_artifact_ids=["art-1"],
        )
        self.exception = exception
        self.delay_seconds = delay_seconds
        self.call_count = 0

    async def execute(self, stage_input: StageInput) -> StageOutput:
        self.call_count += 1
        if self.delay_seconds > 0:
            await anyio.sleep(self.delay_seconds)
        if self.exception:
            raise self.exception
        return self.output


def _make_run(
    workflow_id: str = "wf-test",
    status: WorkflowStatus = WorkflowStatus.CREATED,
) -> WorkflowRun:
    return WorkflowRun(
        workflow_id=workflow_id,
        status=status,
        requested_by="tester",
        repository_reference="owner/repo",
        issue_reference="issue #1",
        target_branch="main",
    )


def _make_context(tmp_path: Path) -> WorkflowContext:
    return WorkflowContext(
        workflow_id="wf-test",
        repository_id="test-repo",
        repository_path=tmp_path,
        issue_reference="issue #1",
        target_branch="main",
        configuration=WorkflowConfiguration(
            limits=ExecutionLimits(
                stage_timeout_seconds=10,
                workflow_timeout_seconds=60,
            )
        ),
    )


@pytest.mark.anyio
async def test_executor_happy_path(tmp_path: Path) -> None:
    """Verify clean linear execution across all stages in registry."""
    registry = StageRegistry()
    adapter1 = MockStageAdapter(
        output=StageOutput(
            stage_type=StageType.REPOSITORY_ANALYSIS,
            status=StageStatus.COMPLETED,
            produced_artifact_ids=["repo_analysis_01"],
        )
    )
    adapter2 = MockStageAdapter(
        output=StageOutput(
            stage_type=StageType.ISSUE_ANALYSIS,
            status=StageStatus.COMPLETED,
            produced_artifact_ids=["issue_analysis_01"],
        )
    )
    registry.register(
        StageDefinition(
            stage_type=StageType.REPOSITORY_ANALYSIS,
            name="Repo Analysis",
            description="Scan",
            prerequisites=[],
        ),
        adapter1,
    )
    registry.register(
        StageDefinition(
            stage_type=StageType.ISSUE_ANALYSIS,
            name="Issue Analysis",
            description="Parse",
            prerequisites=[StageType.REPOSITORY_ANALYSIS],
        ),
        adapter2,
    )

    executor = WorkflowExecutor(stage_registry=registry)
    run = _make_run()
    context = _make_context(tmp_path)

    result_run = await executor.execute(run, context)

    assert result_run.status == WorkflowStatus.COMPLETED
    assert result_run.completed_stages == [
        StageType.REPOSITORY_ANALYSIS,
        StageType.ISSUE_ANALYSIS,
    ]
    assert adapter1.call_count == 1
    assert adapter2.call_count == 1
    stg1 = result_run.get_stage(StageType.REPOSITORY_ANALYSIS)
    assert stg1 is not None
    assert stg1.status == StageStatus.COMPLETED
    stg2 = result_run.get_stage(StageType.ISSUE_ANALYSIS)
    assert stg2 is not None
    assert stg2.status == StageStatus.COMPLETED


@pytest.mark.anyio
async def test_executor_skips_conditionally(tmp_path: Path) -> None:
    """Verify conditional skipping of a stage without failing workflow."""
    registry = StageRegistry()
    adapter1 = MockStageAdapter(
        output=StageOutput(
            stage_type=StageType.EXECUTION_VERIFICATION,
            status=StageStatus.COMPLETED,
            produced_artifact_ids=["exec_01"],
        )
    )
    adapter2 = MockStageAdapter(
        output=StageOutput(
            stage_type=StageType.AUTOMATIC_REPAIR,
            status=StageStatus.SKIPPED,
            should_skip=True,
            skip_reason="Tests verified cleanly",
        )
    )
    registry.register(
        StageDefinition(
            stage_type=StageType.EXECUTION_VERIFICATION,
            name="Execution",
            description="Exec",
            prerequisites=[],
        ),
        adapter1,
    )
    registry.register(
        StageDefinition(
            stage_type=StageType.AUTOMATIC_REPAIR,
            name="Repair",
            description="Fix",
            prerequisites=[StageType.EXECUTION_VERIFICATION],
        ),
        adapter2,
    )

    executor = WorkflowExecutor(stage_registry=registry)
    run = _make_run()
    context = _make_context(tmp_path)

    result_run = await executor.execute(run, context)

    assert result_run.status == WorkflowStatus.COMPLETED
    assert StageType.EXECUTION_VERIFICATION in result_run.completed_stages
    assert StageType.AUTOMATIC_REPAIR not in result_run.completed_stages
    stg_repair = result_run.get_stage(StageType.AUTOMATIC_REPAIR)
    assert stg_repair is not None
    assert stg_repair.status == StageStatus.SKIPPED


@pytest.mark.anyio
async def test_executor_pauses_for_human_approval(tmp_path: Path) -> None:
    """Verify workflow pauses cleanly when human authorization is required."""
    registry = StageRegistry()
    adapter1 = MockStageAdapter(
        output=StageOutput(
            stage_type=StageType.CODE_REVIEW,
            status=StageStatus.COMPLETED,
            produced_artifact_ids=["rev_01"],
        )
    )
    adapter2 = MockStageAdapter(
        output=StageOutput(
            stage_type=StageType.HUMAN_APPROVAL,
            status=StageStatus.WAITING,
            should_pause_for_human=True,
        )
    )
    adapter3 = MockStageAdapter(
        output=StageOutput(
            stage_type=StageType.CONTROLLED_RELEASE,
            status=StageStatus.COMPLETED,
        )
    )
    registry.register(
        StageDefinition(
            stage_type=StageType.CODE_REVIEW,
            name="Review",
            description="Review",
            prerequisites=[],
        ),
        adapter1,
    )
    registry.register(
        StageDefinition(
            stage_type=StageType.HUMAN_APPROVAL,
            name="Approval",
            description="Approve",
            prerequisites=[StageType.CODE_REVIEW],
        ),
        adapter2,
    )
    registry.register(
        StageDefinition(
            stage_type=StageType.CONTROLLED_RELEASE,
            name="Release",
            description="Ship",
            prerequisites=[StageType.HUMAN_APPROVAL],
        ),
        adapter3,
    )

    executor = WorkflowExecutor(stage_registry=registry)
    run = _make_run()
    context = _make_context(tmp_path)

    result_run = await executor.execute(run, context)

    assert result_run.status == WorkflowStatus.WAITING_FOR_APPROVAL
    assert result_run.current_stage == StageType.HUMAN_APPROVAL
    assert adapter3.call_count == 0  # Release must not execute before approval


@pytest.mark.anyio
async def test_executor_resumes_completed_stages(tmp_path: Path) -> None:
    """Verify resuming skips already completed stages without re-executing."""
    registry = StageRegistry()
    adapter1 = MockStageAdapter(
        output=StageOutput(
            stage_type=StageType.REPOSITORY_ANALYSIS,
            status=StageStatus.COMPLETED,
        )
    )
    adapter2 = MockStageAdapter(
        output=StageOutput(
            stage_type=StageType.ISSUE_ANALYSIS,
            status=StageStatus.COMPLETED,
        )
    )
    registry.register(
        StageDefinition(
            stage_type=StageType.REPOSITORY_ANALYSIS,
            name="Repo",
            description="Repo",
            prerequisites=[],
        ),
        adapter1,
    )
    registry.register(
        StageDefinition(
            stage_type=StageType.ISSUE_ANALYSIS,
            name="Issue",
            description="Issue",
            prerequisites=[StageType.REPOSITORY_ANALYSIS],
        ),
        adapter2,
    )

    executor = WorkflowExecutor(stage_registry=registry)
    run = _make_run(status=WorkflowStatus.PAUSED)
    # Stage 1 was already completed
    run.add_completed_stage(StageType.REPOSITORY_ANALYSIS)
    stage1 = run.get_or_create_stage(StageType.REPOSITORY_ANALYSIS)
    stage1.status = StageStatus.COMPLETED

    context = _make_context(tmp_path)
    result_run = await executor.execute(run, context)

    assert result_run.status == WorkflowStatus.COMPLETED
    assert adapter1.call_count == 0  # Was not re-executed
    assert adapter2.call_count == 1  # Was executed


@pytest.mark.anyio
async def test_executor_retries_transient_failure(tmp_path: Path) -> None:
    """Verify exponential backoff retry recovers from transient failures."""
    mock_adapter = AsyncMock()
    # 1st call fails, 2nd call succeeds
    mock_adapter.execute.side_effect = [
        StageOutput(
            stage_type=StageType.CODE_GENERATION,
            status=StageStatus.FAILED,
            error="Transient rate limit",
        ),
        StageOutput(
            stage_type=StageType.CODE_GENERATION,
            status=StageStatus.COMPLETED,
            produced_artifact_ids=["gen_01"],
        ),
    ]

    registry = StageRegistry()
    registry.register(
        StageDefinition(
            stage_type=StageType.CODE_GENERATION,
            name="Gen",
            description="Gen",
            prerequisites=[],
            retry_policy=RetryPolicy(
                max_attempts=2,
                initial_delay_seconds=0.01,
                backoff_multiplier=1.5,
                retryable_error_types=["rate limit"],
            ),
        ),
        mock_adapter,
    )

    executor = WorkflowExecutor(stage_registry=registry)
    run = _make_run()
    context = _make_context(tmp_path)

    result_run = await executor.execute(run, context)

    assert result_run.status == WorkflowStatus.COMPLETED
    assert mock_adapter.execute.call_count == 2
    stg_gen = result_run.get_stage(StageType.CODE_GENERATION)
    assert stg_gen is not None
    assert stg_gen.attempt_count == 2


@pytest.mark.anyio
async def test_executor_retry_exhaustion_fails(tmp_path: Path) -> None:
    """Verify stage failure marks workflow FAILED when retries are exhausted."""
    mock_adapter = AsyncMock()
    mock_adapter.execute.return_value = StageOutput(
        stage_type=StageType.CODE_GENERATION,
        status=StageStatus.FAILED,
        error="Persistent syntax violation",
    )

    registry = StageRegistry()
    registry.register(
        StageDefinition(
            stage_type=StageType.CODE_GENERATION,
            name="Gen",
            description="Gen",
            prerequisites=[],
            retry_policy=RetryPolicy(
                max_attempts=2,
                initial_delay_seconds=0.01,
                backoff_multiplier=1.0,
            ),
        ),
        mock_adapter,
    )

    executor = WorkflowExecutor(stage_registry=registry)
    run = _make_run()
    context = _make_context(tmp_path)

    result_run = await executor.execute(run, context)

    assert result_run.status == WorkflowStatus.FAILED
    assert result_run.failed_stage == StageType.CODE_GENERATION
    assert mock_adapter.execute.call_count == 3  # Initial + 2 retries
    assert "Persistent syntax violation" in str(result_run.error_summary)


@pytest.mark.anyio
async def test_executor_stage_timeout(tmp_path: Path) -> None:
    """Verify stage timeout halts execution and fails stage."""
    adapter = MockStageAdapter(delay_seconds=2.0)
    registry = StageRegistry()
    registry.register(
        StageDefinition(
            stage_type=StageType.REPOSITORY_ANALYSIS,
            name="Repo",
            description="Repo",
            prerequisites=[],
            timeout_seconds=1,  # Short timeout
        ),
        adapter,
    )

    executor = WorkflowExecutor(stage_registry=registry)
    run = _make_run()
    context = _make_context(tmp_path)

    result_run = await executor.execute(run, context)

    assert result_run.status == WorkflowStatus.FAILED
    assert result_run.failed_stage == StageType.REPOSITORY_ANALYSIS
    stage = result_run.get_stage(StageType.REPOSITORY_ANALYSIS)
    assert stage is not None
    assert stage.status == StageStatus.FAILED
    assert "timed out" in str(stage.error)


@pytest.mark.anyio
async def test_executor_workflow_timeout(tmp_path: Path) -> None:
    """Verify overall workflow timeout halts execution."""
    registry = StageRegistry()
    adapter = MockStageAdapter()
    registry.register(
        StageDefinition(
            stage_type=StageType.REPOSITORY_ANALYSIS,
            name="Repo",
            description="Repo",
            prerequisites=[],
        ),
        adapter,
    )

    executor = WorkflowExecutor(stage_registry=registry)
    run = _make_run()
    # Simulate workflow started 100 seconds ago with 30s limit
    run.started_at = datetime.now(UTC) - timedelta(seconds=100)
    context = _make_context(tmp_path)

    result_run = await executor.execute(run, context)

    assert result_run.status == WorkflowStatus.FAILED
    assert "Workflow exceeded maximum limit" in str(result_run.error_summary)


@pytest.mark.anyio
async def test_executor_cancellation(tmp_path: Path) -> None:
    """Verify cancelling workflow run transitions to CANCELLED cleanly."""
    registry = StageRegistry()
    adapter = MockStageAdapter()
    registry.register(
        StageDefinition(
            stage_type=StageType.REPOSITORY_ANALYSIS,
            name="Repo",
            description="Repo",
            prerequisites=[],
        ),
        adapter,
    )

    executor = WorkflowExecutor(stage_registry=registry)
    run = _make_run(status=WorkflowStatus.CANCELLING)
    run.cancellation_reason = "Cancelled by user test"
    context = _make_context(tmp_path)

    result_run = await executor.execute(run, context)

    assert result_run.status == WorkflowStatus.CANCELLED
    assert result_run.cancellation_reason == "Cancelled by user test"
    assert adapter.call_count == 0


@pytest.mark.anyio
async def test_executor_cannot_execute_terminal() -> None:
    """Verify executing terminal workflow raises WorkflowExecutionError."""
    executor = WorkflowExecutor()
    run = _make_run(status=WorkflowStatus.COMPLETED)
    context = WorkflowContext(
        workflow_id="wf-test",
        repository_id="test",
        issue_reference="issue #1",
    )

    with pytest.raises(WorkflowExecutionError, match="terminal state"):
        await executor.execute(run, context)
