"""Structured code generator orchestrating context and provider."""

import logging
import uuid

from backend.app.core.config import settings
from backend.app.generation.models import (
    GeneratedFileChange,
    GenerationRequest,
    SourceContext,
)
from backend.app.generation.prompts import build_generation_prompt
from backend.app.generation.provider import (
    CodeGenerationProvider,
    get_provider,
)
from backend.app.planning.models import ImplementationPlan

logger = logging.getLogger(__name__)


class CodeGenerator:
    """Coordinates prompt building, provider invocation, and change normalization."""

    def __init__(
        self,
        provider: CodeGenerationProvider | None = None,
    ) -> None:
        """Initialize code generator with optional provider override."""
        self.provider = (
            provider
            if provider is not None
            else get_provider(settings.effective_ai_provider)
        )

    async def generate_changes(
        self,
        plan: ImplementationPlan,
        context: SourceContext,
        generation_id: str | None = None,
        timeout_seconds: int | None = None,
    ) -> list[GeneratedFileChange]:
        """Execute code generation against authorized target files in context."""
        if not context.is_valid:
            error_msg = "; ".join(context.errors)
            logger.error("Cannot generate code with invalid context: %s", error_msg)
            raise ValueError(f"Invalid source context for generation: {error_msg}")

        gen_id = generation_id or f"GEN-{uuid.uuid4().hex[:8].upper()}"
        permitted_files = sorted(context.target_files.keys())

        # Construct prompt ensuring prompt formatting functions without error
        prompt = build_generation_prompt(plan, context)

        request = GenerationRequest(
            generation_id=gen_id,
            plan=plan,
            repository_path=context.repository_path,
            permitted_files=permitted_files,
            max_files=len(permitted_files) or settings.MAX_FILES_PER_REQUEST,
            max_output_bytes=settings.MAX_GENERATED_OUTPUT,
            timeout_seconds=timeout_seconds or settings.GENERATION_TIMEOUT,
            provider_type=self.provider.provider_type,
        )

        logger.info(
            "Executing code generation [%s] using provider '%s' for %d target files",
            gen_id,
            self.provider.provider_type,
            len(permitted_files),
        )

        context_payload = {
            "prompt": prompt,
            "file_contents": {
                path: fctx.original_content
                for path, fctx in context.target_files.items()
            },
            "reference_files": dict(context.reference_files),
            "symbols": {
                path: fctx.target_symbols for path, fctx in context.target_files.items()
            },
        }

        raw_changes = await self.provider.generate(request, context_payload)

        # Post-process, map tasks, and enforce authorization boundary
        validated_changes: list[GeneratedFileChange] = []
        for change in raw_changes:
            norm_path = change.path.strip().replace("\\", "/").lstrip("/")

            # Reject changes outside authorized plan target files
            if norm_path not in permitted_files:
                raise ValueError(
                    f"Provider proposed change to unauthorized file: '{change.path}'. "
                    f"Authorized files: {permitted_files}"
                )

            # Reject empty content
            if not change.content.strip():
                raise ValueError(
                    f"Provider generated empty content for file: '{change.path}'"
                )

            # Correlate tasks and requirements if omitted
            task_ids = list(change.task_ids)
            req_ids = list(change.requirement_ids)

            if not task_ids:
                for task in plan.tasks:
                    norm_targets = [
                        t.strip().replace("\\", "/").lstrip("/")
                        for t in task.target_files
                    ]
                    if norm_path in norm_targets:
                        task_ids.append(task.task_id)
                        req_ids.extend(task.addressed_requirements)

            validated_changes.append(
                GeneratedFileChange(
                    path=norm_path,
                    change_type=change.change_type,
                    content=change.content,
                    task_ids=sorted(set(task_ids)),
                    requirement_ids=sorted(set(req_ids)),
                    metadata=dict(change.metadata),
                )
            )

        return validated_changes
