"""Central workflow execution engine coordinating sequential stage execution."""

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio

from backend.app.orchestration.context import WorkflowContext
from backend.app.orchestration.event_bus import WorkflowEventBus
from backend.app.orchestration.events import WorkflowEvent, WorkflowEventType
from backend.app.orchestration.exceptions import (
    WorkflowExecutionError,
)
from backend.app.orchestration.models import (
    WorkflowRun,
    WorkflowStage,
)
from backend.app.orchestration.stage_registry import (
    StageRegistry,
    create_default_stage_registry,
)
from backend.app.orchestration.stages import (
    RetryPolicy,
    StageDefinition,
    StageInput,
    StageOutput,
    WorkflowStageAdapter,
)
from backend.app.orchestration.states import (
    TERMINAL_STAGE_STATES,
    StageStatus,
    StageType,
    WorkflowStatus,
)
from backend.app.orchestration.transitions import (
    StageStateMachine,
    WorkflowStateMachine,
)

logger = logging.getLogger(__name__)


def _is_retryable_error(error_message: str | None, retry_policy: RetryPolicy) -> bool:
    """Check if error message matches configured retryable patterns."""
    if not retry_policy.retryable_exceptions:
        return True
    if not error_message:
        return False
    lower_err = error_message.lower()
    return any(exc.lower() in lower_err for exc in retry_policy.retryable_exceptions)


class WorkflowExecutor:
    """Central engine executing workflow stages in DAG order with retries."""

    def __init__(
        self,
        stage_registry: StageRegistry | None = None,
        workflow_state_machine: type[WorkflowStateMachine] = WorkflowStateMachine,
        stage_state_machine: type[StageStateMachine] = StageStateMachine,
        event_bus: WorkflowEventBus | None = None,
    ) -> None:
        self.stage_registry = stage_registry or create_default_stage_registry()
        self.workflow_sm = workflow_state_machine
        self.stage_sm = stage_state_machine
        self.event_bus = event_bus

    async def _emit(
        self,
        event_type: WorkflowEventType,
        workflow_id: str,
        stage_type: StageType | None = None,
        duration_ms: float | None = None,
        actor: str | None = None,
        payload: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        """Publish workflow event if event bus is configured."""
        if self.event_bus is None:
            return
        await self.event_bus.publish(
            WorkflowEvent(
                event_type=event_type,
                workflow_id=workflow_id,
                stage_type=stage_type,
                duration_ms=duration_ms,
                actor=actor,
                payload=payload or {},
                error=error,
            )
        )

    async def execute(self, run: WorkflowRun, context: WorkflowContext) -> WorkflowRun:
        """Execute workflow run sequentially across all stages in DAG order."""
        if run.is_terminal:
            raise WorkflowExecutionError(
                f"Cannot execute workflow '{run.workflow_id}' in terminal "
                f"state: {run.status.value}"
            )

        # 1. State initialization
        if run.status.value == WorkflowStatus.CREATED.value:
            self.workflow_sm.transition(
                run, WorkflowStatus.QUEUED, reason="Workflow queued for execution"
            )

        if run.status.value in (
            WorkflowStatus.QUEUED.value,
            WorkflowStatus.PAUSED.value,
            WorkflowStatus.WAITING_FOR_APPROVAL.value,
        ):
            self.workflow_sm.transition(
                run, WorkflowStatus.RUNNING, reason="Starting workflow execution"
            )
            await self._emit(
                WorkflowEventType.WORKFLOW_STARTED,
                run.workflow_id,
                actor=run.requested_by,
                payload={
                    "repository_reference": run.repository_reference,
                    "issue_reference": run.issue_reference,
                },
            )

        execution_order = self.stage_registry.get_execution_order()

        # 2. Sequential execution loop
        for stage_type in execution_order:
            # Check for cancellation or terminal state before starting next stage
            if run.status in (
                WorkflowStatus.CANCELLING,
                WorkflowStatus.CANCELLED,
            ):
                if run.status == WorkflowStatus.CANCELLING:
                    self.workflow_sm.transition(
                        run,
                        WorkflowStatus.CANCELLED,
                        reason=run.cancellation_reason or "Cancelled by user",
                    )
                await self._emit(
                    WorkflowEventType.WORKFLOW_CANCELLED,
                    run.workflow_id,
                    error=run.cancellation_reason,
                )
                return run

            # Check overall workflow timeout
            if run.started_at:
                elapsed = (datetime.now(UTC) - run.started_at).total_seconds()
                max_wf_timeout = context.configuration.limits.workflow_timeout_seconds
                if elapsed >= max_wf_timeout:
                    timeout_msg = (
                        f"Workflow exceeded maximum limit of {max_wf_timeout}s"
                    )
                    run.failed_stage = stage_type
                    self.workflow_sm.transition(
                        run, WorkflowStatus.FAILED, reason=timeout_msg
                    )
                    await self._emit(
                        WorkflowEventType.WORKFLOW_FAILED,
                        run.workflow_id,
                        stage_type=stage_type,
                        error=timeout_msg,
                    )
                    return run

            # Skip stage if already completed or skipped previously (e.g. on resume)
            existing_stage = run.get_stage(stage_type)
            if stage_type in run.completed_stages or (
                existing_stage is not None
                and existing_stage.status
                in (StageStatus.COMPLETED, StageStatus.SKIPPED)
            ):
                logger.info(
                    "Skipping already completed/skipped stage '%s' for workflow '%s'",
                    stage_type.value,
                    run.workflow_id,
                )
                continue

            stage = run.get_or_create_stage(stage_type)
            run.current_stage = stage_type

            stage_def, adapter = self.stage_registry.get(stage_type)

            stage_start_time = datetime.now(UTC)
            await self._emit(
                WorkflowEventType.STAGE_STARTED,
                run.workflow_id,
                stage_type=stage_type,
            )

            output = await self._execute_stage_with_retries(
                run=run,
                stage=stage,
                stage_def=stage_def,
                adapter=adapter,
                context=context,
            )
            duration_ms = (
                datetime.now(UTC) - stage_start_time
            ).total_seconds() * 1000.0

            # 3. Handle stage outcome
            if output.should_skip or output.status == StageStatus.SKIPPED:
                if stage.status not in TERMINAL_STAGE_STATES:
                    self.stage_sm.transition(stage, StageStatus.SKIPPED)
                stage.metadata["skip_reason"] = (
                    output.skip_reason or "Skipped conditionally"
                )
                logger.info(
                    "Stage '%s' skipped for workflow '%s': %s",
                    stage_type.value,
                    run.workflow_id,
                    output.skip_reason,
                )
                await self._emit(
                    WorkflowEventType.STAGE_SKIPPED,
                    run.workflow_id,
                    stage_type=stage_type,
                    duration_ms=duration_ms,
                    payload={"skip_reason": output.skip_reason},
                )
                continue

            if output.status == StageStatus.COMPLETED:
                if stage.status not in TERMINAL_STAGE_STATES:
                    self.stage_sm.transition(stage, StageStatus.COMPLETED)
                stage.output_artifact_ids = output.produced_artifact_ids
                run.add_completed_stage(stage_type)
                logger.info(
                    "Stage '%s' completed successfully for workflow '%s'",
                    stage_type.value,
                    run.workflow_id,
                )
                await self._emit(
                    WorkflowEventType.STAGE_COMPLETED,
                    run.workflow_id,
                    stage_type=stage_type,
                    duration_ms=duration_ms,
                    payload={"artifacts": output.produced_artifact_ids},
                )
                continue

            if output.should_pause_for_human or output.status == StageStatus.WAITING:
                if stage.status not in TERMINAL_STAGE_STATES:
                    self.stage_sm.transition(stage, StageStatus.WAITING)
                self.workflow_sm.transition(
                    run,
                    WorkflowStatus.WAITING_FOR_APPROVAL,
                    reason="Awaiting explicit human signoff and authorization",
                )
                logger.info(
                    "Workflow '%s' paused at stage '%s' awaiting human signoff",
                    run.workflow_id,
                    stage_type.value,
                )
                await self._emit(
                    WorkflowEventType.APPROVAL_REQUESTED,
                    run.workflow_id,
                    stage_type=stage_type,
                    duration_ms=duration_ms,
                )
                return run

            if output.status == StageStatus.FAILED:
                if stage.status not in TERMINAL_STAGE_STATES:
                    self.stage_sm.transition(
                        stage, StageStatus.FAILED, error=output.error
                    )
                run.failed_stage = stage_type
                err = output.error or f"Stage '{stage_type.value}' failed execution"
                self.workflow_sm.transition(run, WorkflowStatus.FAILED, reason=err)
                logger.error(
                    "Workflow '%s' failed at stage '%s': %s",
                    run.workflow_id,
                    stage_type.value,
                    err,
                )
                await self._emit(
                    WorkflowEventType.STAGE_FAILED,
                    run.workflow_id,
                    stage_type=stage_type,
                    duration_ms=duration_ms,
                    error=err,
                )
                await self._emit(
                    WorkflowEventType.WORKFLOW_FAILED,
                    run.workflow_id,
                    stage_type=stage_type,
                    error=err,
                )
                return run

        # 4. Final completion
        if not run.failed_stage:
            run.current_stage = None
            self.workflow_sm.transition(
                run,
                WorkflowStatus.COMPLETED,
                reason="All pipeline stages concluded successfully",
            )
            logger.info("Workflow '%s' completed end-to-end", run.workflow_id)
            await self._emit(
                WorkflowEventType.WORKFLOW_COMPLETED,
                run.workflow_id,
            )

        return run

    async def _execute_stage_with_retries(
        self,
        run: WorkflowRun,
        stage: WorkflowStage,
        stage_def: StageDefinition,
        adapter: WorkflowStageAdapter,
        context: WorkflowContext,
    ) -> StageOutput:
        """Execute stage adapter with configured bounded retries and timeouts."""
        stage_input = StageInput(
            workflow_id=run.workflow_id,
            stage=stage,
            context=context,
            repository_path=context.repository_path or Path("."),
            target_branch=run.target_branch,
            issue_reference=run.issue_reference,
            configuration=context.configuration,
        )

        if stage.status not in (StageStatus.RUNNING, StageStatus.WAITING):
            self.stage_sm.transition(stage, StageStatus.RUNNING)

        retry_policy = stage_def.retry_policy
        timeout_sec = min(
            stage_def.timeout_seconds,
            context.configuration.limits.stage_timeout_seconds,
        )

        attempt = 0
        max_attempts = 1 + retry_policy.max_retries

        while attempt < max_attempts:
            attempt += 1
            stage.attempt_count = attempt

            try:
                with anyio.fail_after(float(timeout_sec)):
                    output: StageOutput = await adapter.execute(stage_input)

                if output.status == StageStatus.FAILED:
                    if attempt < max_attempts and _is_retryable_error(
                        output.error, retry_policy
                    ):
                        delay = min(
                            retry_policy.initial_delay_seconds
                            * (retry_policy.backoff_factor ** (attempt - 1)),
                            retry_policy.max_delay_seconds,
                        )
                        logger.warning(
                            "Stage '%s' attempt %d failed: %s. Retrying in %.2fs",
                            stage.stage_type.value,
                            attempt,
                            output.error,
                            delay,
                        )
                        if run.status != WorkflowStatus.RETRYING:
                            self.workflow_sm.transition(
                                run,
                                WorkflowStatus.RETRYING,
                                reason=f"Retrying stage {stage.stage_type.value}",
                            )
                        await self._emit(
                            WorkflowEventType.STAGE_RETRY,
                            run.workflow_id,
                            stage_type=stage.stage_type,
                            payload={
                                "attempt": attempt,
                                "delay_seconds": delay,
                                "error": output.error,
                            },
                        )
                        await anyio.sleep(delay)
                        if run.status == WorkflowStatus.RETRYING:
                            self.workflow_sm.transition(
                                run,
                                WorkflowStatus.RUNNING,
                                reason="Resuming after retry backoff",
                            )
                        continue

                return output

            except TimeoutError:
                err_msg = (
                    f"Stage '{stage.stage_type.value}' timed out after {timeout_sec}s"
                )
                logger.error(err_msg)
                if attempt < max_attempts and _is_retryable_error(
                    err_msg, retry_policy
                ):
                    delay = min(
                        retry_policy.initial_delay_seconds
                        * (retry_policy.backoff_factor ** (attempt - 1)),
                        retry_policy.max_delay_seconds,
                    )
                    await anyio.sleep(delay)
                    continue
                return StageOutput(
                    stage_type=stage.stage_type,
                    status=StageStatus.FAILED,
                    error=err_msg,
                )

            except Exception as exc:
                err_msg = (
                    f"Stage '{stage.stage_type.value}' raised unexpected "
                    f"exception: {exc}"
                )
                logger.exception(err_msg)
                if attempt < max_attempts and _is_retryable_error(
                    str(exc), retry_policy
                ):
                    delay = min(
                        retry_policy.initial_delay_seconds
                        * (retry_policy.backoff_factor ** (attempt - 1)),
                        retry_policy.max_delay_seconds,
                    )
                    await anyio.sleep(delay)
                    continue
                return StageOutput(
                    stage_type=stage.stage_type,
                    status=StageStatus.FAILED,
                    error=err_msg,
                )

        return StageOutput(
            stage_type=stage.stage_type,
            status=StageStatus.FAILED,
            error=f"Stage '{stage.stage_type.value}' exhausted max retries.",
        )
