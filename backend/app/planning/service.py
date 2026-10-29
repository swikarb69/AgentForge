"""Planning engine service orchestrating end-to-end plan generation."""

from pathlib import Path

from backend.app.issue.models import IssueContext
from backend.app.issue.service import IssueContextBuilder
from backend.app.planning.dependencies import DependencyAnalyzer
from backend.app.planning.impact import ImpactAnalyzer
from backend.app.planning.models import (
    ImplementationPlan,
    PlanStatus,
    PlanValidation,
)
from backend.app.planning.risks import RiskAnalyzer
from backend.app.planning.tasks import TaskGenerator
from backend.app.planning.validator import PlanValidator
from backend.app.repository.models import RepositoryInfo


class PlanningEngine:
    """Deterministic planning engine converting IssueContext to ImplementationPlan."""

    def __init__(self) -> None:
        self.context_builder = IssueContextBuilder()
        self.impact_analyzer = ImpactAnalyzer()
        self.task_generator = TaskGenerator()
        self.dependency_analyzer = DependencyAnalyzer()
        self.risk_analyzer = RiskAnalyzer()
        self.validator = PlanValidator()

    def create_plan(
        self,
        repository_path: str | Path,
        title: str,
        description: str = "",
        issue_id: str | None = None,
    ) -> ImplementationPlan:
        """Execute end-to-end deterministic planning pipeline.

        Args:
            repository_path: Path to repository root.
            title: Issue title.
            description: Issue description.
            issue_id: Optional issue identifier.

        Returns:
            Validated ImplementationPlan ready for downstream agents.
        """
        repo_path = Path(repository_path).resolve()
        if not repo_path.exists() or not repo_path.is_dir():
            raise ValueError(f"Repository directory does not exist: {repository_path}")

        # 1. Build IssueContext via Sprint 2 Issue Intelligence
        context = self.context_builder.build_context(
            repository_path=repo_path,
            title=title,
            description=description,
            issue_id=issue_id,
        )

        repo_info = self.context_builder.repo_analyzer.analyze(repo_path)

        # 2. Change Impact Analysis
        impact = self.impact_analyzer.analyze(context, repo_info)

        # 3. Implementation Task Generation & Requirements Traceability
        tasks, test_plan = self.task_generator.generate_tasks(context, impact)

        # 4. Dependency Graph Construction & Ordering
        dependencies = self.dependency_analyzer.analyze_dependencies(tasks, impact)

        # 5. Risk, Grounded Assumptions & Unresolved Questions Analysis
        risks = self.risk_analyzer.analyze_risks(context, impact)
        assumptions = self.risk_analyzer.extract_assumptions(context, impact)
        unresolved_questions = self.risk_analyzer.extract_unresolved_questions(context)

        # Determine concise summary and objective
        objective = context.analysis.issue.title
        summary = (
            f"Deterministic plan addressing '{context.analysis.issue.title}' "
            f"touching {len(impact.direct_files)} implementation files and "
            f"{len(impact.existing_tests) + len(impact.tests_to_add)} test targets."
        )

        # 6. Assemble ImplementationPlan
        plan = ImplementationPlan(
            id=f"PLAN-{context.analysis.issue.id}",
            issue_id=context.analysis.issue.id,
            objective=objective,
            summary=summary,
            status=PlanStatus.READY,
            assumptions=assumptions,
            files_to_modify=impact.files_to_modify,
            files_to_create=impact.files_to_create,
            files_to_delete=impact.files_to_delete,
            symbol_changes=impact.symbol_changes,
            tasks=tasks,
            test_plan=test_plan,
            dependencies=dependencies,
            risks=risks,
            unresolved_questions=unresolved_questions,
            validation=PlanValidation(valid=True, status=PlanStatus.READY),
        )

        # 7. Validate Plan
        validation = self.validator.validate(plan, context, repo_info)
        plan.validation = validation
        plan.status = validation.status

        return plan

    def validate_plan(
        self,
        plan: ImplementationPlan,
        context: IssueContext | None = None,
        repo_info: RepositoryInfo | None = None,
    ) -> PlanValidation:
        """Validate an existing implementation plan."""
        return self.validator.validate(plan, context, repo_info)
