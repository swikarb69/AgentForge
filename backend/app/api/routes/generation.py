"""API routes for code generation and reviewable patch retrieval."""

from fastapi import APIRouter, HTTPException, Response, status

from backend.app.generation.models import (
    CreateGenerationRequest,
    GenerationResult,
)
from backend.app.generation.service import GenerationService

router = APIRouter(prefix="/api/v1/generation", tags=["Generation"])
generation_service = GenerationService()


@router.post(
    "/create",
    response_model=GenerationResult,
    summary="Generate proposed code changes and patch artifact for a plan or issue",
    status_code=status.HTTP_200_OK,
)
async def create_generation(
    payload: CreateGenerationRequest,
) -> GenerationResult:
    """Generate structured code changes and reviewable patch for an authorized plan."""
    return await generation_service.generate_code(payload)


@router.get(
    "/{generation_id}",
    response_model=GenerationResult,
    summary="Get generation result details by ID",
    status_code=status.HTTP_200_OK,
)
def get_generation(generation_id: str) -> GenerationResult:
    """Retrieve full generation result, validation status, and changes."""
    result = generation_service.get_generation(generation_id)
    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Generation result '{generation_id}' not found.",
        )
    return result


@router.get(
    "/{generation_id}/patch",
    summary="Get unified diff patch artifact as plain text",
    status_code=status.HTTP_200_OK,
)
def get_generation_patch(generation_id: str) -> Response:
    """Retrieve raw unified diff patch text for the specified generation ID."""
    result = generation_service.get_generation(generation_id)
    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Generation result '{generation_id}' not found.",
        )
    return Response(
        content=result.patch_content,
        media_type="text/plain; charset=utf-8",
    )
