"""Bounded repair proposal generation engine reusing Sprint 4 code generation."""

from datetime import UTC, datetime
from typing import Any

from backend.app.debugging.models import (
    DiagnosticEvidence,
    RepairProposal,
    RepairStrategy,
    RootCauseAnalysis,
)
from backend.app.generation.models import (
    GeneratedFileChange,
    GenerationRequest,
    GenerationStatus,
    PatchValidation,
    SourceContext,
)
from backend.app.generation.patch import PatchBuilder
from backend.app.generation.provider import (
    CodeGenerationProvider,
    MockCodeGenerationProvider,
)
from backend.app.generation.validator import PatchValidator
from backend.app.planning.models import ImplementationPlan


class RepairProposalEngine:
    """Generates bounded, validated repair proposals for diagnosed failures."""

    def __init__(
        self,
        provider: CodeGenerationProvider | None = None,
        validator: PatchValidator | None = None,
        patch_builder: PatchBuilder | None = None,
    ) -> None:
        self.provider = provider or MockCodeGenerationProvider()
        self.validator = validator or PatchValidator()
        self.patch_builder = patch_builder or PatchBuilder()

    async def generate_proposal(
        self,
        repair_id: str,
        attempt_number: int,
        strategy: RepairStrategy,
        root_cause: RootCauseAnalysis,
        evidence: DiagnosticEvidence,
        plan: ImplementationPlan,
        source_context: SourceContext,
    ) -> RepairProposal:
        """Produce a validated RepairProposal according to strategy and bounds."""
        now_str = datetime.now(UTC).isoformat()

        # 1. If strategy prohibits automated code generation
        if not strategy.code_generation_permitted:
            return RepairProposal(
                repair_id=repair_id,
                attempt_number=attempt_number,
                strategy=strategy,
                root_cause=root_cause,
                file_changes=[],
                patch_content="",
                explanation=(
                    f"Code generation blocked for strategy "
                    f"'{strategy.strategy_type.value}'. Reason: {strategy.reason}"
                ),
                expected_behavior="Human review or environment intervention required.",
                verification_criteria=evidence.acceptance_criteria
                or ["Manual verification"],
                validation=PatchValidation(
                    valid=False,
                    status=GenerationStatus.REJECTED,
                    errors=[
                        "Code generation is not permitted for this repair strategy."
                    ],
                    warnings=[],
                    files_checked=[],
                    plan_scope_violations=[],
                    path_safety_findings=[],
                ),
                created_at=now_str,
            )

        # 2. Build generation request and contextualized repair payload
        gen_id = f"repair-{repair_id}-attempt-{attempt_number}"
        gen_request = GenerationRequest(
            generation_id=gen_id,
            plan=plan,
            repository_path=source_context.repository_path,
        )

        repair_context: dict[str, Any] = {
            "mode": "repair",
            "repair_id": repair_id,
            "attempt_number": attempt_number,
            "strategy": strategy.model_dump(),
            "root_cause": root_cause.model_dump(),
            "target_files": strategy.target_files,
            "target_symbols": strategy.target_symbols,
            "error_messages": evidence.error_messages[:5],
            "failed_test_names": evidence.failed_test_names[:5],
            "acceptance_criteria": evidence.acceptance_criteria,
            "instructions": [
                "Only modify authorized target files.",
                "Do not introduce unrelated code refactoring.",
                "Ensure syntax is fully valid Python code.",
                f"Address primary root cause: {root_cause.primary_cause.value}",
            ],
        }

        # 3. Call generation provider to produce proposed file changes
        file_changes: list[GeneratedFileChange] = await self.provider.generate(
            gen_request, repair_context
        )

        # 4. Validate proposed changes against plan scope, path safety, and syntax
        validation: PatchValidation = self.validator.validate(
            file_changes, plan, source_context
        )

        # 5. Build unified diff patch text
        patch_content = ""
        if validation.valid and file_changes:
            patch_content = self.patch_builder.build_patch(
                generation_id=gen_id,
                plan_id=plan.id,
                changes=file_changes,
                context=source_context,
            )

        explanation = (
            f"Repair proposal targeting {len(file_changes)} file(s) for "
            f"root cause '{root_cause.primary_cause.value}' using "
            f"strategy '{strategy.strategy_type.value}'."
        )

        target_summary = (
            ", ".join(evidence.failed_test_names[:3])
            if evidence.failed_test_names
            else "All tests pass"
        )
        return RepairProposal(
            repair_id=repair_id,
            attempt_number=attempt_number,
            strategy=strategy,
            root_cause=root_cause,
            file_changes=file_changes,
            patch_content=patch_content,
            explanation=explanation,
            expected_behavior=(
                f"Remediate failure and satisfy verification criteria: {target_summary}"
            ),
            verification_criteria=evidence.acceptance_criteria,
            validation=validation,
            created_at=now_str,
        )
