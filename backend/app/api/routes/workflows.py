"""FastAPI REST routes for central End-to-End Workflow Orchestration."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from backend.app.orchestration.context import WorkflowContext
from backend.app.orchestration.events import WorkflowEvent, WorkflowEventType
from backend.app.orchestration.exceptions import (
    ConcurrencyConflictError,
    IdempotencyConflictError,
    InvalidWorkflowStateTransitionError,
    WorkflowArtifactIntegrityError,
    WorkflowBlockedError,
    WorkflowNotFoundError,
    WorkflowValidationError,
)
from backend.app.orchestration.models import (
    WorkflowRequest,
    WorkflowRun,
)
from backend.app.orchestration.service import (
    OrchestrationService,
    get_orchestration_service,
)
from backend.app.orchestration.states import WorkflowStatus

router = APIRouter(prefix="/api/v1/workflows", tags=["Workflows"])


# ============================================================================
# Request / Response Schemas
# ============================================================================


class WorkflowActionRequest(BaseModel):
    """Payload for lifecycle actions like pause, resume, or cancel."""

    reason: str = Field(
        default="",
        description="Operational rationale for triggering the lifecycle action",
    )
    actor: str = Field(
        default="operator",
        description="Operator or agent identity executing the action",
    )


class WorkflowListResponse(BaseModel):
    """Paginated collection of workflow run records."""

    items: list[WorkflowRun] = Field(
        description="List of retrieved workflow runs"
    )
    total: int = Field(
        description="Total number of workflow runs matching criteria"
    )
    limit: int = Field(description="Applied pagination page limit")
    offset: int = Field(description="Applied pagination offset")


# ============================================================================
# Endpoints
# ============================================================================


@router.post(
    "",
    response_model=WorkflowRun,
    status_code=status.HTTP_201_CREATED,
    summary="Start Autonomous Engineering Workflow",
    description="Initiate or return an existing autonomous workflow run.",
)
async def start_workflow(
    request: WorkflowRequest,
    service: Annotated[
        OrchestrationService, Depends(get_orchestration_service)
    ],
) -> WorkflowRun:
    """Launch or idempotently retrieve an end-to-end workflow execution."""
    try:
        return await service.start_workflow(request)
    except IdempotencyConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Idempotency conflict: {exc}",
        ) from exc
    except (WorkflowValidationError, WorkflowBlockedError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.get(
    "",
    response_model=WorkflowListResponse,
    summary="List Workflows",
    description="Retrieve paginated list of workflow runs with optional status filter.",
)
async def list_workflows(
    service: Annotated[
        OrchestrationService, Depends(get_orchestration_service)
    ],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    status_filter: Annotated[
        WorkflowStatus | None, Query(alias="status")
    ] = None,
) -> WorkflowListResponse:
    """List workflow run records with pagination."""
    items = list(
        await service.list_workflows(
            limit=limit, offset=offset, status=status_filter
        )
    )
    total = await service.count_workflows(status=status_filter)
    return WorkflowListResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{workflow_id}",
    response_model=WorkflowRun,
    summary="Get Workflow Details",
    description="Fetch a specific workflow run record by its unique identifier.",
)
async def get_workflow(
    workflow_id: str,
    service: Annotated[
        OrchestrationService, Depends(get_orchestration_service)
    ],
) -> WorkflowRun:
    """Retrieve details and stage execution states for a specific workflow."""
    run = await service.get_workflow(workflow_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow run '{workflow_id}' not found",
        )
    return run


@router.get(
    "/{workflow_id}/context",
    response_model=WorkflowContext,
    summary="Get Workflow Context",
    description="Fetch workflow context, artifact references, and stage history.",
)
async def get_workflow_context(
    workflow_id: str,
    service: Annotated[
        OrchestrationService, Depends(get_orchestration_service)
    ],
) -> WorkflowContext:
    """Retrieve full execution context and artifact references for a workflow."""
    ctx = await service.get_workflow_context(workflow_id)
    if ctx is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow context for '{workflow_id}' not found",
        )
    return ctx


@router.post(
    "/{workflow_id}/pause",
    response_model=WorkflowRun,
    summary="Pause Workflow",
    description="Safely pause an active workflow run.",
)
async def pause_workflow(
    workflow_id: str,
    service: Annotated[
        OrchestrationService, Depends(get_orchestration_service)
    ],
    action: WorkflowActionRequest | None = None,
) -> WorkflowRun:
    """Pause execution of a running or queued workflow run."""
    act = action or WorkflowActionRequest()
    try:
        return await service.pause_workflow(
            workflow_id=workflow_id,
            reason=act.reason or "Paused by operator",
            actor=act.actor,
        )
    except WorkflowNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except InvalidWorkflowStateTransitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except ConcurrencyConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc


@router.post(
    "/{workflow_id}/resume",
    response_model=WorkflowRun,
    summary="Resume Workflow",
    description="Resume execution of a paused or waiting workflow run.",
)
async def resume_workflow(
    workflow_id: str,
    service: Annotated[
        OrchestrationService, Depends(get_orchestration_service)
    ],
    action: WorkflowActionRequest | None = None,
) -> WorkflowRun:
    """Resume execution of a paused or waiting workflow run."""
    act = action or WorkflowActionRequest()
    try:
        return await service.resume_workflow(
            workflow_id=workflow_id,
            actor=act.actor,
        )
    except WorkflowNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except InvalidWorkflowStateTransitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except ConcurrencyConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except WorkflowArtifactIntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Artifact integrity violation: {exc}",
        ) from exc


@router.post(
    "/{workflow_id}/cancel",
    response_model=WorkflowRun,
    summary="Cancel Workflow",
    description="Halt execution and cancel an in-flight workflow run.",
)
async def cancel_workflow(
    workflow_id: str,
    service: Annotated[
        OrchestrationService, Depends(get_orchestration_service)
    ],
    action: WorkflowActionRequest | None = None,
) -> WorkflowRun:
    """Cancel execution of an active or waiting workflow run."""
    act = action or WorkflowActionRequest()
    try:
        return await service.cancel_workflow(
            workflow_id=workflow_id,
            reason=act.reason or "Cancelled by operator",
            actor=act.actor,
        )
    except WorkflowNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except InvalidWorkflowStateTransitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except ConcurrencyConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc


@router.get(
    "/{workflow_id}/events",
    response_model=list[WorkflowEvent],
    summary="Get Workflow Events",
    description="Retrieve telemetry and audit event stream for a workflow.",
)
async def get_workflow_events(
    workflow_id: str,
    service: Annotated[
        OrchestrationService, Depends(get_orchestration_service)
    ],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    event_type: Annotated[WorkflowEventType | None, Query()] = None,
) -> list[WorkflowEvent]:
    """Retrieve audit and telemetry events for the specified workflow."""
    types = {event_type} if event_type else None
    events = await service.get_workflow_events(
        workflow_id=workflow_id,
        event_types=types,
        limit=limit,
        offset=offset,
    )
    return list(events)
