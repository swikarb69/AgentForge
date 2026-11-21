"""API routes for sandboxed execution and verification."""

from fastapi import APIRouter, HTTPException, status

from backend.app.execution.models import (
    CreateExecutionRequest,
    VerificationReport,
)
from backend.app.execution.service import ExecutionService

router = APIRouter(prefix="/api/v1/execution", tags=["Execution"])
execution_service = ExecutionService()


@router.post(
    "/create",
    response_model=VerificationReport,
    summary="Execute patch in Docker sandbox and return verification report",
    status_code=status.HTTP_200_OK,
)
async def create_execution(
    payload: CreateExecutionRequest,
) -> VerificationReport:
    """Apply patch to an isolated workspace and verify inside container sandbox."""
    return await execution_service.execute_and_verify(payload)


@router.get(
    "/reports",
    response_model=list[VerificationReport],
    summary="List all sandbox verification reports",
    status_code=status.HTTP_200_OK,
)
def list_execution_reports() -> list[VerificationReport]:
    """Retrieve all stored verification reports."""
    return execution_service.list_reports()


@router.get(
    "/reports/{execution_id}",
    response_model=VerificationReport,
    summary="Get verification report by execution ID",
    status_code=status.HTTP_200_OK,
)
def get_execution_report(execution_id: str) -> VerificationReport:
    """Retrieve full verification report for an execution run."""
    report = execution_service.get_report(execution_id)
    if not report:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Verification report '{execution_id}' not found.",
        )
    return report
