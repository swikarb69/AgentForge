"""Unit tests for implementation plan validator."""

from backend.app.issue.models import (
    AcceptanceCriterion,
    Issue,
    IssueAnalysis,
    IssueContext,
    IssueRequirement,
    RequirementType,
)
from backend.app.planning.models import (
    ChangeType,
    Dependency,
    FileChange,
    ImplementationPlan,
    PlanStatus,
    PlanTask,
    TestPlan,
    VerificationCase,
)
from backend.app.planning.validator import PlanValidator
from backend.app.repository.models import RepositoryFile, RepositoryInfo


def _build_valid_plan() -> tuple[ImplementationPlan, IssueContext, RepositoryInfo]:
    """Helper fixture building a valid plan, context, and repo_info."""
    plan = ImplementationPlan(
        id="PLAN-001",
        issue_id="ISSUE-001",
        objective="Fix token expiry",
        summary="Update verify_token in auth.py",
        files_to_modify=[
            FileChange(
                path="app/auth.py",
                change_type=ChangeType.MODIFY,
                reason="Update token logic",
                affected_symbols=["verify_token"],
            )
        ],
        tasks=[
            PlanTask(
                task_id="TASK-001",
                title="Update verify_token",
                description="Check expiry in verify_token",
                order=1,
                target_files=["app/auth.py"],
                target_symbols=["verify_token"],
                change_type=ChangeType.MODIFY,
                dependencies=[],
                verification="Check unit test",
                addressed_requirements=["REQ-1"],
                addressed_acceptance_criteria=["AC-1"],
            ),
            PlanTask(
                task_id="TASK-002",
                title="Update test_auth.py",
                description="Add expired token test",
                order=2,
                target_files=["tests/test_auth.py"],
                change_type=ChangeType.TEST,
                dependencies=["TASK-001"],
                verification="Pytest passes",
                addressed_requirements=["REQ-1"],
                addressed_acceptance_criteria=["AC-1"],
            ),
        ],
        test_plan=TestPlan(
            existing_tests=["tests/test_auth.py"],
            verification_cases=[
                VerificationCase(
                    id="VC-001",
                    description="Expired token returns 401",
                    expected_behavior="401 Unauthorized",
                    acceptance_criterion_id="AC-1",
                )
            ],
        ),
        dependencies=[
            Dependency(
                source_task_id="TASK-002",
                target_task_id="TASK-001",
                reason="Test depends on implementation",
            )
        ],
    )

    context = IssueContext(
        analysis=IssueAnalysis(
            issue=Issue(title="Fix auth", description="desc"),
            requirements=[
                IssueRequirement(
                    id="REQ-1",
                    description="Reject expired tokens",
                    requirement_type=RequirementType.FUNCTIONAL,
                )
            ],
            acceptance_criteria=[
                AcceptanceCriterion(
                    id="AC-1",
                    description="Expired token returns 401",
                )
            ],
            constraints=[],
            keywords=[],
            references=[],
            endpoints=[],
        ),
        relevant_files=["app/auth.py", "tests/test_auth.py"],
        relevant_symbols=[],
        relevant_chunks=[],
        matches=[],
    )

    repo_info = RepositoryInfo(
        repository_name="sample-project",
        root_path="/repo",
        total_files=2,
        total_source_files=1,
        total_test_files=1,
        language_counts={"Python": 2},
        files=[
            RepositoryFile(
                relative_path="app/auth.py",
                file_extension=".py",
                language="Python",
                size_bytes=100,
                line_count=10,
            ),
            RepositoryFile(
                relative_path="tests/test_auth.py",
                file_extension=".py",
                language="Python",
                size_bytes=100,
                line_count=10,
                is_test=True,
            ),
        ],
        symbols=[],
        chunks=[],
    )

    return plan, context, repo_info


def test_valid_plan_passes() -> None:
    """Verify that a well-formed plan passes validation with READY status."""
    plan, context, repo_info = _build_valid_plan()
    validator = PlanValidator()
    result = validator.validate(plan, context, repo_info)

    assert result.valid is True
    assert result.status == PlanStatus.READY
    assert len(result.errors) == 0


def test_missing_tasks_invalidates_plan() -> None:
    """Verify that plan with empty task list is invalid."""
    plan, context, repo_info = _build_valid_plan()
    plan.tasks = []

    validator = PlanValidator()
    result = validator.validate(plan, context, repo_info)

    assert result.valid is False
    assert result.status == PlanStatus.BLOCKED
    assert any("no implementation tasks" in err for err in result.errors)


def test_unmapped_requirement_invalidates_plan() -> None:
    """Verify that unmapped requirement triggers validation error."""
    plan, context, repo_info = _build_valid_plan()
    context.analysis.requirements.append(
        IssueRequirement(
            id="REQ-2",
            description="Unmapped requirement",
            requirement_type=RequirementType.FUNCTIONAL,
        )
    )

    validator = PlanValidator()
    result = validator.validate(plan, context, repo_info)

    assert result.valid is False
    assert any("REQ-2" in err for err in result.errors)


def test_unmapped_acceptance_criterion_invalidates_plan() -> None:
    """Verify that unmapped acceptance criterion triggers validation error."""
    plan, context, repo_info = _build_valid_plan()
    context.analysis.acceptance_criteria.append(
        AcceptanceCriterion(
            id="AC-999",
            description="Unverified condition",
        )
    )

    validator = PlanValidator()
    result = validator.validate(plan, context, repo_info)

    assert result.valid is False
    assert any("AC-999" in err for err in result.errors)


def test_circular_dependency_invalidates_plan() -> None:
    """Verify that circular dependency marks plan as BLOCKED and invalid."""
    plan, context, repo_info = _build_valid_plan()
    plan.dependencies.append(
        Dependency(
            source_task_id="TASK-001",
            target_task_id="TASK-002",
            reason="Creates cycle with TASK-002 -> TASK-001",
        )
    )

    validator = PlanValidator()
    result = validator.validate(plan, context, repo_info)

    assert result.valid is False
    assert result.status == PlanStatus.BLOCKED
    assert any("Cyclic task dependency" in err for err in result.errors)


def test_nonexistent_file_invalidates_plan() -> None:
    """Verify that modifying a non-existent file without creating it fails."""
    plan, context, repo_info = _build_valid_plan()
    plan.files_to_modify.append(
        FileChange(
            path="app/nonexistent.py",
            change_type=ChangeType.MODIFY,
            reason="Modify missing file",
        )
    )

    validator = PlanValidator()
    result = validator.validate(plan, context, repo_info)

    assert result.valid is False
    assert any("app/nonexistent.py" in err for err in result.errors)


def test_unresolved_critical_reference_invalidates_plan() -> None:
    """Verify that unresolved critical references trigger error."""
    plan, context, repo_info = _build_valid_plan()
    context.unresolved_references = ["app/ghost.py"]

    validator = PlanValidator()
    result = validator.validate(plan, context, repo_info)

    assert result.valid is False
    assert any("app/ghost.py" in err for err in result.errors)


def test_ambiguous_issue_status() -> None:
    """Verify that an ambiguous issue produces NEEDS_CLARIFICATION status."""
    plan, context, repo_info = _build_valid_plan()
    context.analysis.is_ambiguous = True
    context.analysis.ambiguity_reasons = ["Lacks technical details"]

    validator = PlanValidator()
    result = validator.validate(plan, context, repo_info)

    assert result.valid is True
    assert result.status == PlanStatus.NEEDS_CLARIFICATION
    assert len(result.warnings) > 0
