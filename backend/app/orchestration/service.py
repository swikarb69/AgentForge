"""Central unified orchestration service facade coordinating workflows."""

import logging
from collections.abc import Sequence
from pathlib import Path

from backend.app.orchestration.context import WorkflowContext
from backend.app.orchestration.event_bus import WorkflowEventBus
from backend.app.orchestration.events import WorkflowEvent, WorkflowEventType
from backend.app.orchestration.exceptions import IdempotencyConflictError
from backend.app.orchestration.executor import WorkflowExecutor
from backend.app.orchestration.models import (
    WorkflowRequest,
    WorkflowRun,
)
from backend.app.orchestration.recovery import WorkflowRecoveryManager
from backend.app.orchestration.stage_registry import (
    StageRegistry,
    create_default_stage_registry,
)
from backend.app.orchestration.states import WorkflowStatus
from backend.app.orchestration.storage import (
    WorkflowStore,
    create_workflow_store,
)

logger = logging.getLogger(__name__)


class OrchestrationService:
    """Unified service coordinating workflow runs, stages, recovery, and events."""

    def __init__(
        self,
        store: WorkflowStore | None = None,
        stage_registry: StageRegistry | None = None,
        event_bus: WorkflowEventBus | None = None,
        executor: WorkflowExecutor | None = None,
        recovery_manager: WorkflowRecoveryManager | None = None,
    ) -> None:
        self.store = store if store is not None else create_workflow_store()
        self.stage_registry = stage_registry or create_default_stage_registry()
        self.event_bus = event_bus or WorkflowEventBus()
        self.executor = executor or WorkflowExecutor(
            stage_registry=self.stage_registry,
            event_bus=self.event_bus,
        )
        self.recovery = recovery_manager or WorkflowRecoveryManager(
            store=self.store,
            stage_registry=self.stage_registry,
            executor=self.executor,
            event_bus=self.event_bus,
        )

    async def start_workflow(self, request: WorkflowRequest) -> WorkflowRun:
        """Initiate or resume an autonomous engineering workflow run."""
        if request.idempotency_key:
            existing = await self.store.get_by_idempotency_key(request.idempotency_key)
            if existing is not None:
                if existing.workflow_id != request.workflow_id:
                    raise IdempotencyConflictError(
                        f"Idempotency key '{request.idempotency_key}' is already "
                        f"associated with workflow '{existing.workflow_id}'"
                    )
                logger.info(
                    "Returning existing workflow '%s' for idempotency key '%s'",
                    existing.workflow_id,
                    request.idempotency_key,
                )
                return existing

        config = request.configuration
        limits = request.execution_limits
        config.limits = limits

        repo_path: Path | None = None
        try:
            p = Path(request.repository_id)
            if p.exists():
                repo_path = p
        except (ValueError, OSError):
            repo_path = None

        run = WorkflowRun(
            workflow_id=request.workflow_id,
            status=WorkflowStatus.CREATED,
            requested_by=request.requested_by,
            repository_reference=request.repository_id,
            issue_reference=request.issue_reference,
            target_branch=request.target_branch,
            idempotency_key=request.idempotency_key,
            metadata=request.metadata,
        )
        context = WorkflowContext(
            workflow_id=request.workflow_id,
            repository_id=request.repository_id,
            issue_reference=request.issue_reference,
            target_branch=request.target_branch,
            configuration=config,
            repository_path=repo_path,
            shared_data=request.metadata,
        )

        await self.store.save(run, context)
        updated_run = await self.executor.execute(run, context)
        await self.store.save(updated_run, context)
        return updated_run

    async def get_workflow(self, workflow_id: str) -> WorkflowRun | None:
        """Retrieve workflow run metadata by identifier."""
        return await self.store.get_run(workflow_id)

    async def get_workflow_context(self, workflow_id: str) -> WorkflowContext | None:
        """Retrieve workflow context and artifact links by identifier."""
        return await self.store.get_context(workflow_id)

    async def list_workflows(
        self,
        limit: int = 50,
        offset: int = 0,
        status: WorkflowStatus | None = None,
    ) -> Sequence[WorkflowRun]:
        """List persisted workflow runs with optional filtering and pagination."""
        return await self.store.list(limit=limit, offset=offset, status=status)

    async def count_workflows(self, status: WorkflowStatus | None = None) -> int:
        """Return total count of workflow runs matching optional status."""
        return await self.store.count(status=status)

    async def pause_workflow(
        self,
        workflow_id: str,
        reason: str = "Workflow paused by operator",
        actor: str = "operator",
    ) -> WorkflowRun:
        """Pause execution of an active workflow run."""
        return await self.recovery.pause(
            workflow_id=workflow_id, reason=reason, actor=actor
        )

    async def resume_workflow(
        self, workflow_id: str, actor: str = "operator"
    ) -> WorkflowRun:
        """Resume execution of a paused or waiting workflow run."""
        return await self.recovery.resume(workflow_id=workflow_id, actor=actor)

    async def cancel_workflow(
        self,
        workflow_id: str,
        reason: str = "Workflow cancelled by operator",
        actor: str = "operator",
    ) -> WorkflowRun:
        """Cancel execution of a workflow run."""
        return await self.recovery.cancel(
            workflow_id=workflow_id, reason=reason, actor=actor
        )

    async def get_workflow_events(
        self,
        workflow_id: str | None = None,
        event_types: set[WorkflowEventType] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[WorkflowEvent]:
        """Query audit and telemetry events for a workflow or the entire system."""
        return await self.event_bus.get_events(
            workflow_id=workflow_id,
            event_types=event_types,
            limit=limit,
            offset=offset,
        )


_global_service: OrchestrationService | None = None


def get_orchestration_service() -> OrchestrationService:
    """Return the global OrchestrationService singleton instance."""
    global _global_service
    if _global_service is None:
        _global_service = OrchestrationService()
    return _global_service
