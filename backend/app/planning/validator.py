"""Implementation plan validation engine enforcing structural and domain rules."""

from backend.app.issue.models import IssueContext
from backend.app.planning.dependencies import DependencyAnalyzer
from backend.app.planning.models import (
    ChangeType,
    ImplementationPlan,
    PlanStatus,
    PlanValidation,
)
from backend.app.repository.models import RepositoryInfo


class PlanValidator:
    """Validates structural integrity, traceability, dependencies, and repo bounds."""

    def __init__(self) -> None:
        self.dep_analyzer = DependencyAnalyzer()

    def validate(
        self,
        plan: ImplementationPlan,
        context: IssueContext | None = None,
        repo_info: RepositoryInfo | None = None,
    ) -> PlanValidation:
        """Run comprehensive validation checks across plan.

        Args:
            plan: The ImplementationPlan to validate.
            context: Optional IssueContext to verify requirement traceability.
            repo_info: Optional RepositoryInfo to verify file/symbol existence.

        Returns:
            PlanValidation report containing valid flag, status, errors, and warnings.
        """
        errors: list[str] = []
        warnings: list[str] = []

        # 1. Structural Checks: Tasks presence and uniqueness
        if not plan.tasks:
            errors.append("Plan contains no implementation tasks.")

        task_ids = [t.task_id for t in plan.tasks]
        if len(task_ids) != len(set(task_ids)):
            seen: set[str] = set()
            duplicates = [x for x in task_ids if x in seen or seen.add(x)]  # type: ignore[func-returns-value]
            errors.append(f"Duplicate task IDs found: {', '.join(duplicates)}")

        expected_orders = list(range(1, len(plan.tasks) + 1))
        actual_orders = [t.order for t in plan.tasks]
        if actual_orders != expected_orders:
            errors.append("Task orders must be sequential starting from 1.")

        # Check tasks have targets
        for t in plan.tasks:
            if not t.target_files and not t.target_symbols:
                if t.change_type != ChangeType.TEST:
                    errors.append(f"Task {t.task_id} has no target files or symbols.")
                else:
                    warnings.append(
                        f"Test task {t.task_id} does not specify target test files."
                    )

        # 2. Dependency Integrity and Cycles
        for t in plan.tasks:
            for dep_id in t.dependencies:
                if dep_id not in task_ids:
                    errors.append(
                        f"Task {t.task_id} references non-existent dependency: {dep_id}"
                    )
                if dep_id == t.task_id:
                    errors.append(f"Task {t.task_id} cannot depend on itself.")

        for d in plan.dependencies:
            if d.source_task_id not in task_ids or d.target_task_id not in task_ids:
                errors.append(
                    f"Dependency links non-existent task: "
                    f"{d.source_task_id} -> {d.target_task_id}"
                )
            if d.source_task_id == d.target_task_id:
                errors.append(
                    f"Dependency cannot be self-referential: {d.source_task_id}"
                )

        cycle = self.dep_analyzer.find_cycle(plan.dependencies)
        if cycle:
            errors.append(f"Cyclic task dependency detected: {' -> '.join(cycle)}")

        # 3. Requirement and Acceptance Criteria Traceability
        if context:
            # Check requirements mapping
            covered_reqs: set[str] = set()
            for t in plan.tasks:
                covered_reqs.update(t.addressed_requirements)

            for req in context.analysis.requirements:
                if req.id not in covered_reqs:
                    errors.append(
                        f"Requirement {req.id} ('{req.description}') is not "
                        "addressed by any task."
                    )

            # Check acceptance criteria verification
            verified_ac_ids: set[str] = set()
            for vc in plan.test_plan.verification_cases:
                if vc.acceptance_criterion_id:
                    verified_ac_ids.add(vc.acceptance_criterion_id)

            for ac in context.analysis.acceptance_criteria:
                if ac.id not in verified_ac_ids:
                    # Check if description matches any verification case
                    if not any(
                        ac.description.lower() in vc.description.lower()
                        or ac.description.lower() in vc.expected_behavior.lower()
                        for vc in plan.test_plan.verification_cases
                    ):
                        errors.append(
                            f"Acceptance criterion {ac.id} has no verification case "
                            "in test plan."
                        )

            # Check completely ambiguous scope
            if (
                context.analysis.is_ambiguous
                and not context.relevant_files
                and not context.relevant_symbols
                and not context.analysis.requirements
            ):
                errors.append(
                    "Implementation scope is completely ambiguous; cannot produce "
                    "a valid plan without technical clarification."
                )

            # Check unresolved critical references
            if context.unresolved_references:
                created_paths = {fc.path for fc in plan.files_to_create}
                for ref in context.unresolved_references:
                    if ref not in created_paths:
                        errors.append(
                            f"Unresolved critical entity reference in issue: {ref}"
                        )

        # 4. Repository Bounds Checks
        if repo_info:
            existing_repo_files = {f.relative_path for f in repo_info.files}
            for fc in plan.files_to_modify:
                if fc.path not in existing_repo_files:
                    errors.append(
                        f"Target file to modify does not exist in repository: {fc.path}"
                    )

            existing_symbols = {(s.file_path, s.name) for s in repo_info.symbols}
            for sc in plan.symbol_changes:
                if sc.change_type == ChangeType.MODIFY:
                    if (sc.file_path, sc.symbol_name) not in existing_symbols:
                        warnings.append(
                            f"Target symbol '{sc.symbol_name}' in {sc.file_path} "
                            "not found in repository index."
                        )

        # 5. Determine Overall Status & Result
        valid = len(errors) == 0

        if not valid:
            status = PlanStatus.BLOCKED
        elif context and context.analysis.is_ambiguous:
            status = PlanStatus.NEEDS_CLARIFICATION
            warnings.append(
                "Issue contains ambiguity warnings requiring human clarification."
            )
        elif plan.unresolved_questions:
            status = PlanStatus.NEEDS_CLARIFICATION
        else:
            status = PlanStatus.READY

        return PlanValidation(
            valid=valid,
            status=status,
            errors=errors,
            warnings=warnings,
        )
