"""API routes for repository analysis and search."""

from pathlib import Path

from fastapi import APIRouter, HTTPException, status

from backend.app.repository.analyzer import RepositoryAnalyzer
from backend.app.repository.models import (
    AnalyzeRequest,
    AnalyzeResponse,
    SearchRequest,
    SearchResponse,
)

router = APIRouter(prefix="/api/v1/repositories", tags=["Repositories"])
_analyzer_instance = RepositoryAnalyzer()


@router.post(
    "/analyze",
    response_model=AnalyzeResponse,
    summary="Analyze Repository",
    status_code=status.HTTP_200_OK,
)
def analyze_repository(request: AnalyzeRequest) -> AnalyzeResponse:
    """Analyze target codebase path and return structured metadata summary."""
    target_path = Path(request.path)

    if not target_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository path does not exist: '{request.path}'",
        )
    if not target_path.is_dir():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Target path is not a directory: '{request.path}'",
        )

    try:
        info = _analyzer_instance.analyze(target_path)
        return AnalyzeResponse(
            repository_name=info.repository_name,
            root_path=info.root_path,
            total_files=info.total_files,
            total_source_files=info.total_source_files,
            total_test_files=info.total_test_files,
            language_counts=info.language_counts,
            files=info.files,
            symbols_count=len(info.symbols),
            chunks_count=len(info.chunks),
            parse_errors=info.parse_errors,
        )
    except ValueError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(err),
        ) from err
    except Exception as err:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Repository analysis failed: {err}",
        ) from err


@router.post(
    "/search",
    response_model=SearchResponse,
    summary="Search Repository Index",
    status_code=status.HTTP_200_OK,
)
def search_repository(request: SearchRequest) -> SearchResponse:
    """Search indexed repository code chunks using deterministic keyword matching."""
    target_path = Path(request.repository_path)

    if not target_path.exists() or not target_path.is_dir():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Valid repository path required: '{request.repository_path}'",
        )

    # Trigger analysis first if index empty or different repo
    _analyzer_instance.analyze(target_path)
    results = _analyzer_instance.search(query=request.query, limit=request.limit)

    return SearchResponse(
        query=request.query,
        total_matches=len(results),
        results=results,
    )
