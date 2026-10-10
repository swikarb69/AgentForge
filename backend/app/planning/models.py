"""Data models for deterministic implementation planning."""

from enum import StrEnum

from pydantic import BaseModel, Field

from backend.app.repository.models import SymbolType


class ChangeType(StrEnum):
    """Classification of expected file or symbol changes."""

    MODIFY = "modify"
    CREATE = "create"
    DELETE = "delete"
    TEST = "test"


class PlanStatus(StrEnum):
    """Overall status and readiness of an implementation plan."""

    READY = "ready"
    NEEDS_CLARIFICATION = "needs_clarification"
    BLOCKED = "blocked"


class RiskSeverity(StrEnum):
    """Severity rating for engineering or architectural risks."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FileChange(BaseModel):
    """Targeted file change within the repository."""

    path: str = Field(description="Relative path of file to change")
    change_type: ChangeType = Field(description="Type of file change operation")
    reason: str = Field(description="Engineering rationale for change")
    affected_symbols: list[str] = Field(
        default_factory=list, description="Symbols affected in this file"
    )


class SymbolChange(BaseModel):
    """Targeted symbol change within a repository file."""

    file_path: str = Field(description="Relative file path containing symbol")
    symbol_name: str = Field(description="Identifier name of affected symbol")
    symbol_type: SymbolType | str = Field(description="Category type of symbol")
    change_type: ChangeType = Field(description="Type of symbol change")
    reason: str = Field(description="Engineering rationale for change")


class VerificationCase(BaseModel):
    """Concrete verification case testing expected behavior."""

    id: str = Field(description="Unique verification case identifier")
    description: str = Field(description="Description of behavior to verify")
    expected_behavior: str = Field(description="Expected outcome or assertion")
    acceptance_criterion_id: str | None = Field(
        default=None, description="Linked acceptance criterion ID if applicable"
    )


class TestPlan(BaseModel):
    """Structured plan for test modifications, additions, and verification."""

    __test__ = False

    existing_tests: list[str] = Field(
        default_factory=list, description="Existing relevant test files"
    )
    tests_to_modify: list[str] = Field(
        default_factory=list, description="Test files needing modifications"
    )
    tests_to_add: list[str] = Field(
        default_factory=list, description="New test files to create"
    )
    verification_cases: list[VerificationCase] = Field(
        default_factory=list, description="Derived verification test cases"
    )


class PlanTask(BaseModel):
    """Actionable implementation step for future code generation."""

    task_id: str = Field(description="Unique task identifier e.g. TASK-001")
    title: str = Field(description="Concise task title")
    description: str = Field(description="Detailed engineering task instructions")
    order: int = Field(description="Execution sequence order index (1-based)")
    target_files: list[str] = Field(
        default_factory=list, description="Target file paths touched by task"
    )
    target_symbols: list[str] = Field(
        default_factory=list, description="Target symbol names touched by task"
    )
    change_type: ChangeType = Field(description="Primary change type of task")
    dependencies: list[str] = Field(
        default_factory=list, description="Task IDs this task depends upon"
    )
    verification: str = Field(description="Verification condition for completion")
    addressed_requirements: list[str] = Field(
        default_factory=list, description="Linked requirement IDs addressed"
    )
    addressed_acceptance_criteria: list[str] = Field(
        default_factory=list, description="Linked acceptance criteria addressed"
    )


class Dependency(BaseModel):
    """Explicit dependency link between implementation tasks."""

    source_task_id: str = Field(description="Task that depends on target")
    target_task_id: str = Field(description="Prerequisite task ID")
    reason: str = Field(description="Rationale for prerequisite order")


class Risk(BaseModel):
    """Identified engineering or architectural risk."""

    risk: str = Field(description="Summary of the potential risk")
    severity: RiskSeverity = Field(description="Risk severity level")
    reason: str = Field(description="Underlying cause or trigger")
    mitigation: str = Field(description="Actionable mitigation strategy")


class Assumption(BaseModel):
    """Explicit architectural or implementation assumption."""

    assumption: str = Field(description="Statement of assumption")
    reason: str = Field(description="Grounding context or rationale")


class PlanValidation(BaseModel):
    """Validation report detailing structural and logical integrity of plan."""

    valid: bool = Field(description="True if plan satisfies all quality checks")
    status: PlanStatus = Field(description="Calculated readiness status")
    errors: list[str] = Field(
        default_factory=list, description="Blocking validation error messages"
    )
    warnings: list[str] = Field(
        default_factory=list, description="Non-blocking warning notices"
    )


class ImplementationPlan(BaseModel):
    """Complete, validated implementation plan ready for coding agents."""

    id: str = Field(default="PLAN-1", description="Plan unique identifier")
    issue_id: str = Field(default="ISSUE-LOCAL", description="Target issue ID")
    objective: str = Field(description="Primary objective of implementation")
    summary: str = Field(description="High-level engineering approach summary")
    status: PlanStatus = Field(
        default=PlanStatus.READY, description="Overall plan status"
    )
    assumptions: list[Assumption] = Field(
        default_factory=list, description="Explicit assumptions"
    )
    files_to_modify: list[FileChange] = Field(
        default_factory=list, description="Files requiring modification"
    )
    files_to_create: list[FileChange] = Field(
        default_factory=list, description="New files to create"
    )
    files_to_delete: list[FileChange] = Field(
        default_factory=list, description="Files to delete"
    )
    symbol_changes: list[SymbolChange] = Field(
        default_factory=list, description="Granular symbol-level changes"
    )
    tasks: list[PlanTask] = Field(
        default_factory=list, description="Ordered implementation tasks"
    )
    test_plan: TestPlan = Field(
        default_factory=TestPlan, description="Test and verification strategy"
    )
    dependencies: list[Dependency] = Field(
        default_factory=list, description="Task dependency relations"
    )
    risks: list[Risk] = Field(
        default_factory=list, description="Identified risks and mitigations"
    )
    unresolved_questions: list[str] = Field(
        default_factory=list, description="Ambiguities needing clarification"
    )
    validation: PlanValidation = Field(
        default_factory=lambda: PlanValidation(valid=True, status=PlanStatus.READY),
        description="Plan validation findings",
    )


class CreatePlanRequest(BaseModel):
    """API request model for generating an implementation plan."""

    repository_path: str = Field(description="Filesystem path to repository root")
    title: str = Field(description="Issue title or headline")
    description: str = Field(default="", description="Detailed issue description")
    id: str | None = Field(default=None, description="Optional issue ID")


class ValidatePlanRequest(BaseModel):
    """API request model for validating an implementation plan."""

    plan: ImplementationPlan = Field(description="Implementation plan to validate")
