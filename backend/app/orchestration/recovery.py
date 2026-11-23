"""Workflow recovery, checkpoint integrity, pause, resume, and cancellation."""

import hashlib
import logging
import re
from pathlib import Path
from typing import Any

from backend.app.orchestration.context import WorkflowContext
from backend.app.orchestration.event_bus import WorkflowEventBus
from backend.app.orchestration.events import WorkflowEvent, WorkflowEventType
from backend.app.orchestration.exceptions import (
    ConcurrencyConflictError,
    InvalidWorkflowStateTransitionError,
    WorkflowArtifactIntegrityError,
    WorkflowNotFoundError,
)
from backend.app.orchestration.executor import WorkflowExecutor
from backend.app.orchestration.models import WorkflowRun
from backend.app.orchestration.stage_registry import (
    StageRegistry,
    create_default_stage_registry,
)
from backend.app.orchestration.states import (
    StageStatus,
    StageType,
    WorkflowStatus,
)
from backend.app.orchestration.storage import WorkflowStore
from backend.app.orchestration.transitions import (
    StageStateMachine,
    WorkflowStateMachine,
)

logger = logging.getLogger(__name__)

_HEX_HASH_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


def verify_artifact_integrity(run: WorkflowRun, context: WorkflowContext) -> None:
    """Verify artifact presence and cryptographic integrity for completed stages.

    Raises:
        WorkflowArtifactIntegrityError: If required artifacts are missing,
            malformed, or do not match disk sha256 checksums.
    """
    artifacts = context.artifacts

    for stage_type in run.completed_stages:
        if stage_type == StageType.REPOSITORY_ANALYSIS:
            if not artifacts.repository_analysis_id:
                raise WorkflowArtifactIntegrityError(
                    f"Workflow '{run.workflow_id}' completed "
                    f"'{stage_type.value}' but repository_analysis_id is missing"
                )
            if artifacts.repository_analysis_hash and not _HEX_HASH_PATTERN.match(
                artifacts.repository_analysis_hash
            ):
                raise WorkflowArtifactIntegrityError(
                    f"Corrupted repository_analysis_hash in '{run.workflow_id}'"
                )

        elif stage_type == StageType.ISSUE_ANALYSIS:
            if not artifacts.issue_analysis_id:
                raise WorkflowArtifactIntegrityError(
                    f"Workflow '{run.workflow_id}' completed "
                    f"'{stage_type.value}' but issue_analysis_id is missing"
                )
            if artifacts.issue_analysis_hash and not _HEX_HASH_PATTERN.match(
                artifacts.issue_analysis_hash
            ):
                raise WorkflowArtifactIntegrityError(
                    f"Corrupted issue_analysis_hash in '{run.workflow_id}'"
                )

        elif stage_type == StageType.IMPLEMENTATION_PLANNING:
            if not artifacts.implementation_plan_id:
                raise WorkflowArtifactIntegrityError(
                    f"Workflow '{run.workflow_id}' completed "
                    f"'{stage_type.value}' but implementation_plan_id is missing"
                )
            if artifacts.implementation_plan_hash and not _HEX_HASH_PATTERN.match(
                artifacts.implementation_plan_hash
            ):
                raise WorkflowArtifactIntegrityError(
                    f"Corrupted implementation_plan_hash in '{run.workflow_id}'"
                )

        elif stage_type == StageType.CODE_GENERATION:
            if not artifacts.generation_id:
                raise WorkflowArtifactIntegrityError(
                    f"Workflow '{run.workflow_id}' completed "
                    f"'{stage_type.value}' but generation_id is missing"
                )
            patch_hash = artifacts.generation_patch_hash
            if not patch_hash or not _HEX_HASH_PATTERN.match(patch_hash):
                raise WorkflowArtifactIntegrityError(
                    f"Invalid patch hash in workflow '{run.workflow_id}' "
                    f"for stage '{stage_type.value}'"
                )
            patch_file_path = context.shared_data.get("patch_path")
            if patch_file_path:
                file_path = Path(patch_file_path)
                if file_path.is_file():
                    actual_hash = hashlib.sha256(file_path.read_bytes()).hexdigest()
                    if actual_hash.lower() != patch_hash.lower():
                        raise WorkflowArtifactIntegrityError(
                            f"Artifact hash mismatch on disk for "
                            f"'{stage_type.value}': expected "
                            f"{patch_hash}, found {actual_hash}"
                        )

        elif stage_type == StageType.EXECUTION_VERIFICATION:
            if not artifacts.execution_id:
                raise WorkflowArtifactIntegrityError(
                    f"Workflow '{run.workflow_id}' completed "
                    f"'{stage_type.value}' but execution_id is missing"
                )

        elif stage_type == StageType.AUTOMATIC_REPAIR:
            if artifacts.repair_id:
                repair_hash = artifacts.repair_patch_hash
                if not repair_hash or not _HEX_HASH_PATTERN.match(repair_hash):
                    raise WorkflowArtifactIntegrityError(
                        f"Invalid repair patch hash in workflow '{run.workflow_id}'"
                    )

        elif stage_type == StageType.CODE_REVIEW:
            if not artifacts.review_id:
                raise WorkflowArtifactIntegrityError(
                    f"Workflow '{run.workflow_id}' completed "
                    f"'{stage_type.value}' but review_id is missing"
                )

        elif stage_type == StageType.HUMAN_APPROVAL:
            if not artifacts.approval_id:
                raise WorkflowArtifactIntegrityError(
                    f"Workflow '{run.workflow_id}' completed "
                    f"'{stage_type.value}' but approval_id is missing"
                )


class WorkflowRecoveryManager:
    """Coordinates lifecycle control operations: pause, resume, cancel, recover."""

    def __init__(
        self,
        store: WorkflowStore,
        stage_registry: StageRegistry | None = None,
        executor: WorkflowExecutor | None = None,
        workflow_state_machine: type[WorkflowStateMachine] = WorkflowStateMachine,
        stage_state_machine: type[StageStateMachine] = StageStateMachine,
        event_bus: WorkflowEventBus | None = None,
    ) -> None:
        self.store = store
        self.stage_registry = stage_registry or create_default_stage_registry()
        self.event_bus = event_bus
        self.executor = executor or WorkflowExecutor(
            stage_registry=self.stage_registry,
            workflow_state_machine=workflow_state_machine,
            stage_state_machine=stage_state_machine,
            event_bus=self.event_bus,
        )
        self.workflow_sm = workflow_state_machine
        self.stage_sm = stage_state_machine

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

    async def pause(
        self,
        workflow_id: str,
        reason: str = "Workflow paused by operator",
        actor: str = "operator",
    ) -> WorkflowRun:
        """Safely pause an active workflow run."""
        async with self.store.lock(workflow_id):
            item = await self.store.get(workflow_id)
            if item is None:
                raise WorkflowNotFoundError(f"Workflow '{workflow_id}' not found")
            run, context = item

            if run.is_terminal:
                raise InvalidWorkflowStateTransitionError(
                    f"Cannot pause workflow '{workflow_id}' in terminal state "
                    f"'{run.status.value}'"
                )
            if run.status == WorkflowStatus.PAUSED:
                return run

            self.workflow_sm.transition(
                run,
                WorkflowStatus.PAUSED,
                reason=reason,
                actor=actor,
            )
            await self.store.save(run, context)
            await self._emit(
                WorkflowEventType.WORKFLOW_PAUSED,
                workflow_id,
                actor=actor,
                payload={"reason": reason},
            )
            logger.info("Paused workflow '%s': %s", workflow_id, reason)
            return run

    async def cancel(
        self,
        workflow_id: str,
        reason: str = "Workflow cancelled by operator",
        actor: str = "operator",
    ) -> WorkflowRun:
        """Cancel an in-flight or waiting workflow run."""
        async with self.store.lock(workflow_id):
            item = await self.store.get(workflow_id)
            if item is None:
                raise WorkflowNotFoundError(f"Workflow '{workflow_id}' not found")
            run, context = item

            if run.status == WorkflowStatus.CANCELLED:
                return run
            if run.is_terminal:
                raise InvalidWorkflowStateTransitionError(
                    f"Cannot cancel workflow '{workflow_id}' in terminal state "
                    f"'{run.status.value}'"
                )

            run.cancellation_reason = reason
            if run.status in (
                WorkflowStatus.RUNNING,
                WorkflowStatus.WAITING_FOR_APPROVAL,
            ):
                self.workflow_sm.transition(
                    run,
                    WorkflowStatus.CANCELLING,
                    reason=reason,
                    actor=actor,
                )

            self.workflow_sm.transition(
                run,
                WorkflowStatus.CANCELLED,
                reason=reason,
                actor=actor,
            )

            # Cancel any pending or running stages
            for stage in run.stages.values():
                if stage.status in (
                    StageStatus.PENDING,
                    StageStatus.RUNNING,
                    StageStatus.WAITING,
                ):
                    self.stage_sm.transition(stage, StageStatus.CANCELLED)

            await self.store.save(run, context)
            await self._emit(
                WorkflowEventType.WORKFLOW_CANCELLED,
                workflow_id,
                actor=actor,
                payload={"reason": reason},
            )
            logger.info("Cancelled workflow '%s': %s", workflow_id, reason)
            return run

    async def resume(
        self,
        workflow_id: str,
        actor: str = "operator",
    ) -> WorkflowRun:
        """Resume a paused, queued, or waiting workflow run after integrity checks."""
        async with self.store.lock(workflow_id):
            item = await self.store.get(workflow_id)
            if item is None:
                raise WorkflowNotFoundError(f"Workflow '{workflow_id}' not found")
            run, context = item

            if run.is_terminal:
                raise InvalidWorkflowStateTransitionError(
                    f"Cannot resume workflow '{workflow_id}' in terminal state "
                    f"'{run.status.value}'"
                )
            if run.status in (WorkflowStatus.RUNNING, WorkflowStatus.CANCELLING):
                raise ConcurrencyConflictError(
                    f"Workflow '{workflow_id}' is currently active in state "
                    f"'{run.status.value}'"
                )

            # 1. Verify artifact integrity before resuming
            verify_artifact_integrity(run, context)

            # 2. Transition to RUNNING if PAUSED or WAITING_FOR_APPROVAL
            if run.status in (
                WorkflowStatus.PAUSED,
                WorkflowStatus.WAITING_FOR_APPROVAL,
            ):
                self.workflow_sm.transition(
                    run,
                    WorkflowStatus.RUNNING,
                    reason="Workflow resumed",
                    actor=actor,
                )
                await self.store.save(run, context)
                await self._emit(
                    WorkflowEventType.WORKFLOW_RESUMED,
                    workflow_id,
                    actor=actor,
                )

            # 3. Execute remaining stages
            updated_run = await self.executor.execute(run, context)
            await self.store.save(updated_run, context)
            logger.info(
                "Resumed workflow '%s', now in state '%s'",
                workflow_id,
                updated_run.status.value,
            )
            return updated_run

    async def recover(
        self,
        workflow_id: str,
        actor: str = "recovery_worker",
    ) -> WorkflowRun:
        """Recover an interrupted or crashed workflow run."""
        async with self.store.lock(workflow_id):
            item = await self.store.get(workflow_id)
            if item is None:
                raise WorkflowNotFoundError(f"Workflow '{workflow_id}' not found")
            run, context = item

            if run.is_terminal:
                return run

            # 1. Verify artifact integrity
            verify_artifact_integrity(run, context)

            # 2. Reset interrupted in-flight stage back to PENDING for clean re-run
            for stage in run.stages.values():
                if stage.status == StageStatus.RUNNING:
                    stage.status = StageStatus.PENDING

            # 3. Execute remaining stages
            updated_run = await self.executor.execute(run, context)
            await self.store.save(updated_run, context)
            logger.info(
                "Recovered workflow '%s', concluded in state '%s'",
                workflow_id,
                updated_run.status.value,
            )
            return updated_run
