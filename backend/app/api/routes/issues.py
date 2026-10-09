"""API endpoints for issue analysis and repository issue mapping."""

from fastapi import APIRouter, HTTPException, status

from backend.app.issue.models import (
    AnalyzeIssueRequest,
    IssueAnalysis,
    IssueContext,
    IssueContextRequest,
)
from backend.app.issue.service import IssueContextBuilder

router = APIRouter(prefix="/api/v1/issues", tags=["Issues"])
context_builder = IssueContextBuilder()


@router.post(
    "/analyze",
    response_model=IssueAnalysis,
    summary="Analyze software issue text into structured requirements",
    status_code=status.HTTP_200_OK,
)
def analyze_issue(payload: AnalyzeIssueRequest) -> IssueAnalysis:
    """Analyze issue title and description into engineering requirements."""
    try:
        return context_builder.analyze_issue(
            title=payload.title,
            description=payload.description,
            issue_id=payload.id,
        )
    except ValueError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(err),
        ) from err


@router.post(
    "/context",
    response_model=IssueContext,
    summary="Map software issue to repository context and relevance matches",
    status_code=status.HTTP_200_OK,
)
def get_issue_context(payload: IssueContextRequest) -> IssueContext:
    """Map software issue to repository intelligence, symbols, and code chunks."""
    try:
        return context_builder.build_context(
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
            detail=f"Failed to build issue context: {err!s}",
        ) from err
