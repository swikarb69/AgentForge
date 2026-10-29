"""Implementation task generation and requirement traceability engine."""

from backend.app.issue.models import IssueContext
from backend.app.planning.impact import ImpactResult
from backend.app.planning.models import (
    ChangeType,
    PlanTask,
    TestPlan,
    VerificationCase,
)


class TaskGenerator:
    """Generates concrete implementation tasks with requirement traceability."""

    def generate_tasks(
        self,
        context: IssueContext,
        impact: ImpactResult,
    ) -> tuple[list[PlanTask], TestPlan]:
        """Convert IssueContext and ImpactResult into ordered tasks and TestPlan.

        Returns:
            Tuple of (list of PlanTask, TestPlan).
        """
        # 1. Build Verification Cases and TestPlan
        verification_cases: list[VerificationCase] = []
        for ac in context.analysis.acceptance_criteria:
            vc_id = f"VC-{len(verification_cases) + 1:03d}"
            verification_cases.append(
                VerificationCase(
                    id=vc_id,
                    description=ac.description,
                    expected_behavior=f"Satisfy acceptance criterion: {ac.description}",
                    acceptance_criterion_id=ac.id,
                )
            )

        if not verification_cases and context.analysis.requirements:
            for req in context.analysis.requirements:
                vc_id = f"VC-{len(verification_cases) + 1:03d}"
                verification_cases.append(
                    VerificationCase(
                        id=vc_id,
                        description=f"Verify requirement: {req.description}",
                        expected_behavior=f"Satisfy requirement {req.id}",
                        acceptance_criterion_id=None,
                    )
                )

        test_plan = TestPlan(
            existing_tests=impact.existing_tests,
            tests_to_modify=impact.tests_to_modify,
            tests_to_add=impact.tests_to_add,
            verification_cases=verification_cases,
        )

        # 2. Build Implementation Tasks
        tasks: list[PlanTask] = []
        req_ids = [r.id for r in context.analysis.requirements]
        ac_ids = [ac.id for ac in context.analysis.acceptance_criteria]

        assigned_reqs: set[str] = set()
        assigned_acs: set[str] = set()

        # Step 2a: New file creation tasks (Foundation)
        for fc in impact.files_to_create:
            if not self._is_test_file(fc.path):
                task_id = f"TASK-{len(tasks) + 1:03d}"
                tasks.append(
                    PlanTask(
                        task_id=task_id,
                        title=f"Create file {fc.path}",
                        description=fc.reason,
                        order=len(tasks) + 1,
                        target_files=[fc.path],
                        target_symbols=[],
                        change_type=ChangeType.CREATE,
                        dependencies=[],
                        verification=(
                            f"Confirm file {fc.path} is created with valid syntax"
                        ),
                        addressed_requirements=req_ids[:1] if req_ids else [],
                        addressed_acceptance_criteria=[],
                    )
                )
                if req_ids:
                    assigned_reqs.add(req_ids[0])

        # Step 2b: Implementation modification tasks (Core Changes)
        for fc in impact.files_to_modify:
            if not self._is_test_file(fc.path):
                task_id = f"TASK-{len(tasks) + 1:03d}"
                symbols_desc = (
                    f" for symbols: {', '.join(fc.affected_symbols)}"
                    if fc.affected_symbols
                    else ""
                )
                title = f"Modify {fc.path}{symbols_desc}"

                # Match requirements mentioning this file or symbols
                matching_reqs = self._match_requirements_for_file(
                    fc.path, fc.affected_symbols, context
                )
                if not matching_reqs:
                    matching_reqs = req_ids

                for rid in matching_reqs:
                    assigned_reqs.add(rid)

                tasks.append(
                    PlanTask(
                        task_id=task_id,
                        title=title,
                        description=(
                            f"Update implementation in {fc.path}. {fc.reason}."
                        ),
                        order=len(tasks) + 1,
                        target_files=[fc.path],
                        target_symbols=fc.affected_symbols,
                        change_type=ChangeType.MODIFY,
                        dependencies=[],
                        verification=(
                            f"Verify changes in {fc.path} conform to AST "
                            f"and syntax specifications"
                        ),
                        addressed_requirements=matching_reqs,
                        addressed_acceptance_criteria=ac_ids,
                    )
                )
                for acid in ac_ids:
                    assigned_acs.add(acid)

        # Fallback if no files to modify or create exist (e.g. general task)
        if not tasks:
            task_id = f"TASK-{len(tasks) + 1:03d}"
            tasks.append(
                PlanTask(
                    task_id=task_id,
                    title=f"Implement changes for: {context.analysis.issue.title}",
                    description=context.analysis.issue.description
                    or "Implement issue requirements",
                    order=len(tasks) + 1,
                    target_files=list(impact.direct_files),
                    target_symbols=[s.symbol_name for s in impact.symbol_changes],
                    change_type=ChangeType.MODIFY,
                    dependencies=[],
                    verification="Verify implementation matches requirements",
                    addressed_requirements=req_ids,
                    addressed_acceptance_criteria=ac_ids,
                )
            )
            for rid in req_ids:
                assigned_reqs.add(rid)
            for acid in ac_ids:
                assigned_acs.add(acid)

        # Ensure all requirements are addressed
        unaddressed_reqs = [r for r in req_ids if r not in assigned_reqs]
        if unaddressed_reqs and tasks:
            # Assign remaining requirements to the first implementation task
            tasks[0].addressed_requirements = list(
                dict.fromkeys(tasks[0].addressed_requirements + unaddressed_reqs)
            )

        # Step 2c: Test tasks (Tests & Verification)
        all_test_targets = impact.tests_to_modify + impact.tests_to_add
        if not all_test_targets and impact.existing_tests:
            all_test_targets = impact.existing_tests[:1]

        if all_test_targets:
            for test_file in all_test_targets:
                task_id = f"TASK-{len(tasks) + 1:03d}"
                is_new = test_file in impact.tests_to_add
                change_type = ChangeType.CREATE if is_new else ChangeType.TEST
                action_verb = "Create" if is_new else "Update"

                vc_summary = ", ".join(vc.id for vc in test_plan.verification_cases)
                tasks.append(
                    PlanTask(
                        task_id=task_id,
                        title=f"{action_verb} tests in {test_file}",
                        description=(
                            f"{action_verb} unit and regression test suite in "
                            f"{test_file} to validate acceptance criteria "
                            f"and avoid regressions."
                        ),
                        order=len(tasks) + 1,
                        target_files=[test_file],
                        target_symbols=[],
                        change_type=change_type,
                        dependencies=[],
                        verification=(
                            f"Execute tests in {test_file} and verify: {vc_summary}"
                            if vc_summary
                            else f"Ensure tests in {test_file} pass successfully"
                        ),
                        addressed_requirements=req_ids,
                        addressed_acceptance_criteria=ac_ids,
                    )
                )
        else:
            # Generic test task if no specific test file detected
            task_id = f"TASK-{len(tasks) + 1:03d}"
            tasks.append(
                PlanTask(
                    task_id=task_id,
                    title="Add and verify regression tests",
                    description=(
                        "Write test cases covering all requirements and "
                        "acceptance criteria"
                    ),
                    order=len(tasks) + 1,
                    target_files=[],
                    target_symbols=[],
                    change_type=ChangeType.TEST,
                    dependencies=[],
                    verification="Execute automated test runner to ensure tests pass",
                    addressed_requirements=req_ids,
                    addressed_acceptance_criteria=ac_ids,
                )
            )

        return tasks, test_plan

    @staticmethod
    def _is_test_file(path: str) -> bool:
        """Check if relative path is a test file."""
        low = path.lower()
        return low.startswith("tests/") or "test_" in low or "_test.py" in low

    @staticmethod
    def _match_requirements_for_file(
        file_path: str,
        symbols: list[str],
        context: IssueContext,
    ) -> list[str]:
        """Match requirement IDs relevant to a specific file or symbols."""
        matched_ids: list[str] = []
        file_base = file_path.lower()

        for req in context.analysis.requirements:
            desc_lower = req.description.lower()
            if any(sym.lower() in desc_lower for sym in symbols):
                matched_ids.append(req.id)
            elif file_base in desc_lower:
                matched_ids.append(req.id)

        return matched_ids
