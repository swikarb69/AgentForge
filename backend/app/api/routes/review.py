"""API routes for code review and quality assurance engine."""

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from backend.app.review.exceptions import ReviewError
from backend.app.review.models import (
    FindingCategory,
    FindingSeverity,
    ReviewFinding,
    ReviewReport,
    ReviewRequest,
)
from backend.app.review.registry import get_default_registry
from backend.app.review.rules import ReviewRule
from backend.app.review.service import ReviewService

router = APIRouter(prefix="/api/v1/reviews", tags=["Review"])
review_service = ReviewService()


class MarkdownReportResponse(BaseModel):
    """Schema for formatted markdown review report response."""

    review_id: str = Field(description="Unique review session identifier")
    format: str = Field(default="markdown", description="Report format")
    content: str = Field(description="Formatted markdown review report text")


@router.post(
    "/create",
    response_model=ReviewReport,
    summary="Execute comprehensive code review and return auditable report",
    status_code=status.HTTP_200_OK,
)
@router.post(
    "",
    response_model=ReviewReport,
    summary="Execute code review (alias)",
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
async def create_review(payload: ReviewRequest) -> ReviewReport:
    """Execute static, security, architecture, and regression review on code."""
    try:
        return await review_service.create_review(payload)
    except ValueError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(err),
        ) from err
    except ReviewError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(err),
        ) from err
    except Exception as err:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Code review execution failed: {err!s}",
        ) from err


@router.get(
    "",
    response_model=list[ReviewReport],
    summary="List all stored review session reports",
    status_code=status.HTTP_200_OK,
)
def list_reviews() -> list[ReviewReport]:
    """Retrieve all stored review reports."""
    return review_service.list_reviews()


@router.get(
    "/rules",
    response_model=list[ReviewRule],
    summary="List all registered review rules in the catalog",
    status_code=status.HTTP_200_OK,
)
def list_rules(
    category: Annotated[
        FindingCategory | None,
        Query(description="Filter rules by finding category"),
    ] = None,
    severity: Annotated[
        FindingSeverity | None,
        Query(description="Filter rules by default severity"),
    ] = None,
) -> list[ReviewRule]:
    """Retrieve registered review rules with metadata and descriptions."""
    registry = get_default_registry()
    rules = registry.list_rules()
    if category is not None:
        rules = [r for r in rules if r.category == category]
    if severity is not None:
        rules = [r for r in rules if r.severity == severity]
    return rules


@router.get(
    "/{review_id}",
    response_model=ReviewReport,
    summary="Get complete code review report by ID",
    status_code=status.HTTP_200_OK,
)
def get_review(review_id: str) -> ReviewReport:
    """Retrieve full review report and gating outcome by review ID."""
    report = review_service.get_review(review_id)
    if not report:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Review report '{review_id}' not found.",
        )
    return report


@router.get(
    "/{review_id}/findings",
    response_model=list[ReviewFinding],
    summary="Get filtered review findings for a review run",
    status_code=status.HTTP_200_OK,
)
def get_review_findings(
    review_id: str,
    severity: Annotated[
        FindingSeverity | None,
        Query(description="Filter by finding severity"),
    ] = None,
    category: Annotated[
        FindingCategory | None,
        Query(description="Filter by finding category"),
    ] = None,
) -> list[ReviewFinding]:
    """Retrieve findings for a specific review with optional filtering."""
    report = review_service.get_review(review_id)
    if not report:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Review report '{review_id}' not found.",
        )
    findings = report.findings
    if severity is not None:
        findings = [f for f in findings if f.severity == severity]
    if category is not None:
        findings = [f for f in findings if f.category == category]
    return findings


@router.get(
    "/{review_id}/report",
    response_model=Any,
    summary="Get formatted review report (JSON or Markdown)",
    status_code=status.HTTP_200_OK,
)
def get_formatted_report(
    review_id: str,
    report_format: Annotated[
        str,
        Query(
            alias="format",
            description="Format of report output: 'json' or 'markdown'",
        ),
    ] = "json",
) -> Any:
    """Retrieve review report in JSON or Markdown format."""
    report = review_service.get_review(review_id)
    if not report:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Review report '{review_id}' not found.",
        )
    if report_format.lower() == "markdown":
        markdown_text = review_service.format_markdown_report(report)
        return MarkdownReportResponse(
            review_id=review_id,
            content=markdown_text,
        )
    return report
