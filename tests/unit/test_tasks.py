"""Unit tests for implementation task generation and requirement traceability."""

from backend.app.issue.models import (
    AcceptanceCriterion,
    Issue,
    IssueAnalysis,
    IssueContext,
    IssueRequirement,
    RequirementType,
)
from backend.app.planning.impact import ImpactResult
from backend.app.planning.models import ChangeType, FileChange
from backend.app.planning.tasks import TaskGenerator


def test_generate_tasks_and_test_plan() -> None:
    """Verify task generation, test plan, and verification cases."""
    context = IssueContext(
        analysis=IssueAnalysis(
            issue=Issue(
                title="Fix verify_token in app/auth.py",
                description="Reject expired JWT tokens with 401 response",
            ),
            requirements=[
                IssueRequirement(
                    id="REQ-1",
                    description="Reject expired JWT tokens",
                    requirement_type=RequirementType.FUNCTIONAL,
                ),
                IssueRequirement(
                    id="REQ-2",
                    description="Return 401 Unauthorized",
                    requirement_type=RequirementType.FUNCTIONAL,
                ),
            ],
            acceptance_criteria=[
                AcceptanceCriterion(
                    id="AC-1",
                    description="Expired token returns 401 status code",
                )
            ],
            constraints=[],
            keywords=["token", "jwt", "auth"],
            references=[],
            endpoints=[],
        ),
        relevant_files=["app/auth.py", "tests/test_auth.py"],
        relevant_symbols=[],
        relevant_chunks=[],
        matches=[],
    )

    impact = ImpactResult(
        files_to_modify=[
            FileChange(
                path="app/auth.py",
                change_type=ChangeType.MODIFY,
                reason="Update verify_token function",
                affected_symbols=["verify_token"],
            )
        ],
        files_to_create=[],
        files_to_delete=[],
        symbol_changes=[],
        existing_tests=["tests/test_auth.py"],
        tests_to_modify=["tests/test_auth.py"],
        tests_to_add=[],
        direct_files=["app/auth.py"],
        indirect_files=["tests/test_auth.py"],
    )

    generator = TaskGenerator()
    tasks, test_plan = generator.generate_tasks(context, impact)

    assert len(tasks) >= 2
    assert len(test_plan.verification_cases) == 1
    assert test_plan.verification_cases[0].acceptance_criterion_id == "AC-1"

    # Verify task IDs and sequential order
    task_ids = [t.task_id for t in tasks]
    assert len(task_ids) == len(set(task_ids))
    assert [t.order for t in tasks] == list(range(1, len(tasks) + 1))

    # Verify implementation task exists
    impl_task = next(t for t in tasks if t.change_type == ChangeType.MODIFY)
    assert "app/auth.py" in impl_task.target_files

    # Verify test task exists
    test_task = next(
        t
        for t in tasks
        if t.change_type in (ChangeType.TEST, ChangeType.CREATE)
        and "tests/" in t.target_files[0]
    )
    assert "tests/test_auth.py" in test_task.target_files


def test_requirement_traceability() -> None:
    """Verify that all requirements and acceptance criteria are traced to tasks."""
    context = IssueContext(
        analysis=IssueAnalysis(
            issue=Issue(
                title="Add payment processing",
                description="Create payment service",
            ),
            requirements=[
                IssueRequirement(
                    id="REQ-101",
                    description="Process credit card transactions",
                    requirement_type=RequirementType.FUNCTIONAL,
                ),
                IssueRequirement(
                    id="REQ-102",
                    description="Send email receipts",
                    requirement_type=RequirementType.FUNCTIONAL,
                ),
            ],
            acceptance_criteria=[
                AcceptanceCriterion(
                    id="AC-101",
                    description="Transaction returns transaction ID",
                )
            ],
            constraints=[],
            keywords=[],
            references=[],
            endpoints=[],
        ),
        relevant_files=["app/payment.py"],
        relevant_symbols=[],
        relevant_chunks=[],
        matches=[],
    )

    impact = ImpactResult(
        files_to_create=[
            FileChange(
                path="app/payment.py",
                change_type=ChangeType.CREATE,
                reason="New payment service",
            ),
            FileChange(
                path="tests/test_payment.py",
                change_type=ChangeType.CREATE,
                reason="New payment tests",
            ),
        ],
        files_to_modify=[],
        files_to_delete=[],
        symbol_changes=[],
        existing_tests=[],
        tests_to_modify=[],
        tests_to_add=["tests/test_payment.py"],
        direct_files=["app/payment.py"],
        indirect_files=["tests/test_payment.py"],
    )

    generator = TaskGenerator()
    tasks, _ = generator.generate_tasks(context, impact)

    # Check all requirements are covered across tasks
    addressed_reqs = set()
    addressed_acs = set()
    for task in tasks:
        addressed_reqs.update(task.addressed_requirements)
        addressed_acs.update(task.addressed_acceptance_criteria)

    assert "REQ-101" in addressed_reqs
    assert "REQ-102" in addressed_reqs
    assert "AC-101" in addressed_acs


def test_task_generator_fallback_without_acceptance_criteria() -> None:
    """Verify fallback to requirements when acceptance criteria are empty."""
    context = IssueContext(
        analysis=IssueAnalysis(
            issue=Issue(
                title="Generic improvement",
                description="Refactor database helpers",
            ),
            requirements=[
                IssueRequirement(
                    id="REQ-1",
                    description="Refactor app/database.py connection pool",
                    requirement_type=RequirementType.FUNCTIONAL,
                )
            ],
            acceptance_criteria=[],
            constraints=[],
            keywords=[],
            references=[],
            endpoints=[],
        ),
        relevant_files=["app/database.py"],
        relevant_symbols=[],
        relevant_chunks=[],
        matches=[],
    )

    impact = ImpactResult(
        files_to_modify=[
            FileChange(
                path="app/database.py",
                change_type=ChangeType.MODIFY,
                reason="Refactor pool",
                affected_symbols=[],
            )
        ],
        files_to_create=[],
        files_to_delete=[],
        symbol_changes=[],
        existing_tests=[],
        tests_to_modify=[],
        tests_to_add=[],
        direct_files=["app/database.py"],
        indirect_files=[],
    )

    generator = TaskGenerator()
    tasks, test_plan = generator.generate_tasks(context, impact)

    # Verification cases should be generated from requirements
    assert len(test_plan.verification_cases) == 1
    assert "REQ-1" in test_plan.verification_cases[0].expected_behavior

    # Since no tests exist or are added, generic test task should be created
    assert any(t.title == "Add and verify regression tests" for t in tasks)


def test_task_generator_empty_impact_fallback() -> None:
    """Verify fallback task creation when impact contains no files."""
    context = IssueContext(
        analysis=IssueAnalysis(
            issue=Issue(
                title="Investigate issue",
                description="No specific file resolved",
            ),
            requirements=[
                IssueRequirement(
                    id="REQ-1",
                    description="Diagnose performance bottleneck",
                    requirement_type=RequirementType.NON_FUNCTIONAL,
                )
            ],
            acceptance_criteria=[],
            constraints=[],
            keywords=[],
            references=[],
            endpoints=[],
        ),
        relevant_files=[],
        relevant_symbols=[],
        relevant_chunks=[],
        matches=[],
    )

    impact = ImpactResult()

    generator = TaskGenerator()
    tasks, test_plan = generator.generate_tasks(context, impact)

    assert len(tasks) == 2  # General task + generic test task
    assert "Investigate issue" in tasks[0].title
    assert tasks[0].addressed_requirements == ["REQ-1"]
