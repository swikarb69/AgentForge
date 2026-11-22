"""API routes for automated debugging and repair engine."""

from fastapi import APIRouter, HTTPException, status

from backend.app.debugging.models import (
    RepairAttempt,
    RepairRequest,
    RepairResult,
)
from backend.app.debugging.service import DebuggingService

router = APIRouter(prefix="/api/v1/debugging", tags=["Debugging"])
debugging_service = DebuggingService()


@router.post(
    "/repair",
    response_model=RepairResult,
    summary="Initiate bounded automated repair session for a failed execution",
    status_code=status.HTTP_200_OK,
)
async def create_repair_session(payload: RepairRequest) -> RepairResult:
    """Classify failures, diagnose root causes, generate bounded repairs, and verify."""
    try:
        return await debugging_service.start_repair(payload)
    except ValueError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(err),
        ) from err
    except Exception as err:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Automated repair execution failed: {err!s}",
        ) from err


@router.get(
    "/repairs",
    response_model=list[RepairResult],
    summary="List all automated repair sessions",
    status_code=status.HTTP_200_OK,
)
def list_repairs() -> list[RepairResult]:
    """Retrieve all stored repair session results."""
    return debugging_service.list_repairs()


@router.get(
    "/repair/{repair_id}",
    response_model=RepairResult,
    summary="Get automated repair session details by ID",
    status_code=status.HTTP_200_OK,
)
def get_repair(repair_id: str) -> RepairResult:
    """Retrieve complete repair session result, verdict, and current state."""
    repair = debugging_service.get_repair(repair_id)
    if not repair:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repair session '{repair_id}' not found.",
        )
    return repair


@router.get(
    "/repair/{repair_id}/history",
    response_model=list[RepairAttempt],
    summary="Get attempt history and state transitions for a repair session",
    status_code=status.HTTP_200_OK,
)
def get_repair_history(repair_id: str) -> list[RepairAttempt]:
    """Retrieve chronological attempts and state transitions for a repair session."""
    history = debugging_service.get_repair_history(repair_id)
    if history is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repair session '{repair_id}' not found.",
        )
    return history
