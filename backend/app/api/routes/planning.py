"""API endpoints for implementation planning and plan validation."""

from fastapi import APIRouter, HTTPException, status

from backend.app.planning.models import (
    CreatePlanRequest,
    ImplementationPlan,
    PlanValidation,
    ValidatePlanRequest,
)
from backend.app.planning.service import PlanningEngine

router = APIRouter(prefix="/api/v1/planning", tags=["Planning"])
planning_engine = PlanningEngine()


@router.post(
    "/create",
    response_model=ImplementationPlan,
    summary="Generate a validated deterministic implementation plan for an issue",
    status_code=status.HTTP_200_OK,
)
def create_plan(payload: CreatePlanRequest) -> ImplementationPlan:
    """Generate an implementation plan from repository and issue context."""
    try:
        return planning_engine.create_plan(
            repository_path=payload.repository_path,
            title=payload.title,
            description=payload.description,
            issue_id=payload.id,
        )
    except ValueError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(err),
        ) from err
    except Exception as err:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate implementation plan: {err!s}",
        ) from err


@router.post(
    "/validate",
    response_model=PlanValidation,
    summary="Validate an existing implementation plan",
    status_code=status.HTTP_200_OK,
)
def validate_plan(payload: ValidatePlanRequest) -> PlanValidation:
    """Validate structural and dependency rules of an implementation plan."""
    try:
        return planning_engine.validate_plan(plan=payload.plan)
    except ValueError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(err),
        ) from err
    except Exception as err:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to validate plan: {err!s}",
        ) from err
