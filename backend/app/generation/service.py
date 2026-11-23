"""High-level service coordinating code generation and patch artifact creation."""

import logging
import uuid
from typing import Any

from backend.app.generation.context import SourceContextBuilder
from backend.app.generation.generator import CodeGenerator
from backend.app.generation.models import (
    CreateGenerationRequest,
    GenerationResult,
    GenerationStatus,
    PatchValidation,
)
from backend.app.generation.patch import PatchBuilder
from backend.app.generation.provider import (
    CodeGenerationProvider,
    get_provider,
)
from backend.app.generation.validator import PatchValidator
from backend.app.planning.models import ImplementationPlan
from backend.app.planning.service import PlanningEngine
from backend.app.planning.validator import PlanValidator

logger = logging.getLogger(__name__)


class GenerationService:
    """End-to-end service coordinating code generation and patch validation."""

    def __init__(
        self,
        provider: CodeGenerationProvider | None = None,
        context_builder: SourceContextBuilder | None = None,
        generator: CodeGenerator | None = None,
        validator: PatchValidator | None = None,
        patch_builder: PatchBuilder | None = None,
        planning_engine: PlanningEngine | None = None,
        plan_validator: PlanValidator | None = None,
    ) -> None:
        """Initialize all pipeline components for the generation service."""
        self.context_builder = context_builder or SourceContextBuilder()
        self.generator = generator or CodeGenerator(provider=provider)
        self.validator = validator or PatchValidator()
        self.patch_builder = patch_builder or PatchBuilder()
        self.planning_engine = planning_engine or PlanningEngine()
        self.plan_validator = plan_validator or PlanValidator()
        self._history: dict[str, GenerationResult] = {}

    async def generate_code(
        self,
        payload: CreateGenerationRequest,
    ) -> GenerationResult:
        """Execute end-to-end code generation and return auditable result artifact."""
        generation_id = f"GEN-{uuid.uuid4().hex[:8].upper()}"
        active_provider_type = (
            payload.provider_type or self.generator.provider.provider_type
        )

        # 1. Resolve or create implementation plan
        plan: ImplementationPlan
        if payload.plan is not None:
            plan = payload.plan
            plan_val = self.plan_validator.validate(plan)
            if not plan_val.valid:
                logger.warning(
                    "Generation rejected: input plan has validation errors: %s",
                    plan_val.errors,
                )
                validation_report = PatchValidation(
                    valid=False,
                    status=GenerationStatus.REJECTED,
                    errors=[f"Input plan is invalid: {e}" for e in plan_val.errors],
                    warnings=plan_val.warnings,
                )
                result = GenerationResult(
                    generation_id=generation_id,
                    status=GenerationStatus.REJECTED,
                    changes=[],
                    validation=validation_report,
                    provider_type=active_provider_type,
                    errors=validation_report.errors,
                    warnings=validation_report.warnings,
                )
                self._history[generation_id] = result
                return result
        else:
            if not payload.title:
                validation_report = PatchValidation(
                    valid=False,
                    status=GenerationStatus.FAILED,
                    errors=["Issue title is required when plan is not provided."],
                )
                result = GenerationResult(
                    generation_id=generation_id,
                    status=GenerationStatus.FAILED,
                    changes=[],
                    validation=validation_report,
                    provider_type=active_provider_type,
                    errors=validation_report.errors,
                )
                self._history[generation_id] = result
                return result

            try:
                plan = self.planning_engine.create_plan(
                    repository_path=payload.repository_path,
                    title=payload.title,
                    description=payload.description or "",
                    issue_id=payload.issue_id,
                )
            except Exception as err:
                logger.exception("Failed to build implementation plan on the fly.")
                validation_report = PatchValidation(
                    valid=False,
                    status=GenerationStatus.FAILED,
                    errors=[f"Plan creation failed: {err}"],
                )
                result = GenerationResult(
                    generation_id=generation_id,
                    status=GenerationStatus.FAILED,
                    changes=[],
                    validation=validation_report,
                    provider_type=active_provider_type,
                    errors=validation_report.errors,
                )
                self._history[generation_id] = result
                return result

        # 2. Extract and bound source context
        context = self.context_builder.build_context(payload.repository_path, plan)
        if not context.is_valid:
            logger.warning(
                "Generation rejected: source context has errors: %s",
                context.errors,
            )
            validation_report = PatchValidation(
                valid=False,
                status=GenerationStatus.REJECTED,
                errors=list(context.errors),
                warnings=list(context.warnings),
            )
            result = GenerationResult(
                generation_id=generation_id,
                status=GenerationStatus.REJECTED,
                changes=[],
                validation=validation_report,
                provider_type=active_provider_type,
                errors=context.errors,
                warnings=context.warnings,
            )
            self._history[generation_id] = result
            return result

        # 3. Setup generator (support custom provider override in request)
        if payload.provider_type:
            generator = CodeGenerator(provider=get_provider(payload.provider_type))
        else:
            generator = self.generator

        # 4. Generate proposed file changes
        try:
            changes = await generator.generate_changes(
                plan, context, generation_id=generation_id
            )
        except Exception as err:
            logger.exception("Code generation failed during execution.")
            validation_report = PatchValidation(
                valid=False,
                status=GenerationStatus.FAILED,
                errors=[f"Generation execution error: {err}"],
            )
            result = GenerationResult(
                generation_id=generation_id,
                status=GenerationStatus.FAILED,
                changes=[],
                validation=validation_report,
                provider_type=generator.provider.provider_type,
                errors=validation_report.errors,
            )
            self._history[generation_id] = result
            return result

        # 5. Validate proposed changes against plan and security bounds
        validation = self.validator.validate(changes, plan, context)

        # 6. Build unified diff patch and persist artifact
        patch_content = self.patch_builder.build_patch(
            generation_id=generation_id,
            plan_id=plan.id,
            changes=changes,
            context=context,
        )
        patch_file_path: str | None = None
        try:
            artifact_path = self.patch_builder.save_artifact(
                generation_id=generation_id,
                patch_content=patch_content,
            )
            patch_file_path = str(artifact_path)
        except Exception as err:
            logger.warning("Failed saving patch artifact: %s", err)
            validation.warnings.append(f"Failed to persist patch artifact: {err}")

        # 7. Assemble final GenerationResult
        status = validation.status
        all_warnings = list(context.warnings) + list(validation.warnings)
        usage: dict[str, Any] = {
            "target_files_count": len(changes),
            "context_bytes": context.total_bytes,
        }
        if (
            hasattr(generator.provider, "last_usage")
            and generator.provider.last_usage is not None
        ):
            usage["tokens"] = dict(generator.provider.last_usage)

        result = GenerationResult(
            generation_id=generation_id,
            status=status,
            changes=changes,
            validation=validation,
            patch_content=patch_content,
            patch_file_path=patch_file_path,
            provider_type=generator.provider.provider_type,
            errors=validation.errors,
            warnings=all_warnings,
            usage_metadata=usage,
        )

        self._history[generation_id] = result
        return result

    def get_generation(self, generation_id: str) -> GenerationResult | None:
        """Retrieve a stored generation result by ID."""
        return self._history.get(generation_id)
