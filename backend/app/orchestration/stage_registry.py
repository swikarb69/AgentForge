"""Central registry managing workflow stage definitions, adapters, and DAG ordering."""

from backend.app.orchestration.adapters import (
    AutomaticRepairAdapter,
    CodeGenerationAdapter,
    CodeReviewAdapter,
    ControlledReleaseAdapter,
    ExecutionVerificationAdapter,
    HumanApprovalAdapter,
    ImplementationPlanningAdapter,
    IssueAnalysisAdapter,
    RepositoryAnalysisAdapter,
)
from backend.app.orchestration.exceptions import OrchestrationError
from backend.app.orchestration.stages import (
    RetryPolicy,
    StageDefinition,
    WorkflowStageAdapter,
)
from backend.app.orchestration.states import StageType


class StageRegistry:
    """Registry coordinating stage specifications, adapters, and DAG ordering."""

    def __init__(self) -> None:
        self._stages: dict[StageType, StageDefinition] = {}
        self._adapters: dict[StageType, WorkflowStageAdapter] = {}

    def register(
        self,
        definition: StageDefinition,
        adapter: WorkflowStageAdapter,
        allow_override: bool = False,
    ) -> None:
        """Register a stage definition and its execution adapter."""
        stage_type = definition.stage_type

        if stage_type in self._stages and not allow_override:
            raise OrchestrationError(
                f"Stage '{stage_type.value}' is already registered."
            )

        self._stages[stage_type] = definition
        self._adapters[stage_type] = adapter

    def get_definition(self, stage_type: StageType) -> StageDefinition:
        """Retrieve definition specification for a registered stage."""
        if stage_type not in self._stages:
            raise OrchestrationError(f"Stage '{stage_type.value}' is not registered.")
        return self._stages[stage_type]

    def get_adapter(self, stage_type: StageType) -> WorkflowStageAdapter:
        """Retrieve execution adapter for a registered stage."""
        if stage_type not in self._adapters:
            raise OrchestrationError(
                f"Stage adapter for '{stage_type.value}' is not registered."
            )
        return self._adapters[stage_type]

    def list_stages(self) -> list[StageDefinition]:
        """Return all registered stage definitions in topological order."""
        order = self.get_execution_order()
        return [self._stages[st] for st in order]

    def validate_dependencies(self) -> None:
        """Verify that all prerequisite stages are present and no cycles exist."""
        # 1. Check prerequisite existence
        for stage_type, defn in self._stages.items():
            for prereq in defn.prerequisites:
                if prereq not in self._stages:
                    raise OrchestrationError(
                        f"Stage '{stage_type.value}' requires unregistered "
                        f"prerequisite '{prereq.value}'."
                    )

        # 2. Check for circular dependencies via DFS cycle detection
        visited: dict[StageType, int] = {}  # 0: visiting, 1: visited

        def dfs(current: StageType) -> None:
            visited[current] = 0
            for neighbor in self._stages[current].prerequisites:
                if neighbor not in self._stages:
                    continue
                state = visited.get(neighbor)
                if state == 0:
                    raise OrchestrationError(
                        f"Circular dependency detected involving "
                        f"'{current.value}' and '{neighbor.value}'."
                    )
                if state is None:
                    dfs(neighbor)
            visited[current] = 1

        for stage_key in self._stages:
            if stage_key not in visited:
                dfs(stage_key)

    def get_execution_order(self) -> list[StageType]:
        """Compute valid topological execution order respecting prerequisites."""
        self.validate_dependencies()

        order: list[StageType] = []
        visited: set[StageType] = set()

        def visit(node: StageType) -> None:
            if node in visited:
                return
            for prereq in self._stages[node].prerequisites:
                if prereq in self._stages:
                    visit(prereq)
            visited.add(node)
            order.append(node)

        for st in self._stages:
            visit(st)

        return order


def create_default_stage_registry() -> StageRegistry:
    """Factory creating and populating the default 9-stage engineering registry."""
    registry = StageRegistry()

    # Stage 1: Repository Analysis
    registry.register(
        StageDefinition(
            stage_type=StageType.REPOSITORY_ANALYSIS,
            name="Repository Analysis",
            description="Scan repository intelligence and construct symbol index",
            required=True,
            prerequisites=[],
            timeout_seconds=300,
            retry_policy=RetryPolicy(max_attempts=2),
        ),
        RepositoryAnalysisAdapter(),
    )

    # Stage 2: Issue Analysis
    registry.register(
        StageDefinition(
            stage_type=StageType.ISSUE_ANALYSIS,
            name="Issue Analysis",
            description="Extract requirements and map code references to repo context",
            required=True,
            prerequisites=[StageType.REPOSITORY_ANALYSIS],
            timeout_seconds=300,
            retry_policy=RetryPolicy(max_attempts=2),
        ),
        IssueAnalysisAdapter(),
    )

    # Stage 3: Implementation Planning
    registry.register(
        StageDefinition(
            stage_type=StageType.IMPLEMENTATION_PLANNING,
            name="Implementation Planning",
            description="Synthesize structured tasks and risk mitigation strategy",
            required=True,
            prerequisites=[StageType.ISSUE_ANALYSIS],
            timeout_seconds=300,
            retry_policy=RetryPolicy(max_attempts=2),
        ),
        ImplementationPlanningAdapter(),
    )

    # Stage 4: Code Generation
    registry.register(
        StageDefinition(
            stage_type=StageType.CODE_GENERATION,
            name="Code Generation",
            description="Generate source changes and synthesize validated unified diff",
            required=True,
            prerequisites=[StageType.IMPLEMENTATION_PLANNING],
            timeout_seconds=600,
            retry_policy=RetryPolicy(max_attempts=2),
        ),
        CodeGenerationAdapter(),
    )

    # Stage 5: Execution Verification
    registry.register(
        StageDefinition(
            stage_type=StageType.EXECUTION_VERIFICATION,
            name="Execution Verification",
            description="Apply patch in sandbox and execute test verification suite",
            required=True,
            prerequisites=[StageType.CODE_GENERATION],
            timeout_seconds=600,
            retry_policy=RetryPolicy(max_attempts=1),
        ),
        ExecutionVerificationAdapter(),
    )

    # Stage 6: Automatic Repair
    registry.register(
        StageDefinition(
            stage_type=StageType.AUTOMATIC_REPAIR,
            name="Automatic Repair",
            description="Diagnostic repair loop triggered on test verification failure",
            required=False,
            prerequisites=[StageType.EXECUTION_VERIFICATION],
            timeout_seconds=900,
            retry_policy=RetryPolicy(max_attempts=1),
        ),
        AutomaticRepairAdapter(),
    )

    # Stage 7: Code Review
    registry.register(
        StageDefinition(
            stage_type=StageType.CODE_REVIEW,
            name="Code Review & Quality Assurance",
            description="Static analysis, security scans, and regression checks",
            required=True,
            prerequisites=[StageType.EXECUTION_VERIFICATION],
            timeout_seconds=300,
            retry_policy=RetryPolicy(max_attempts=2),
        ),
        CodeReviewAdapter(),
    )

    # Stage 8: Human Approval
    registry.register(
        StageDefinition(
            stage_type=StageType.HUMAN_APPROVAL,
            name="Human Approval",
            description="Enforce authorization boundary and await human decision",
            required=True,
            prerequisites=[StageType.CODE_REVIEW],
            timeout_seconds=3600,
            retry_policy=RetryPolicy(max_attempts=0),
        ),
        HumanApprovalAdapter(),
    )

    # Stage 9: Controlled Release
    registry.register(
        StageDefinition(
            stage_type=StageType.CONTROLLED_RELEASE,
            name="Controlled Release",
            description="Validate 13-point readiness criteria and generate release PR",
            required=True,
            prerequisites=[StageType.HUMAN_APPROVAL],
            timeout_seconds=300,
            retry_policy=RetryPolicy(max_attempts=1),
        ),
        ControlledReleaseAdapter(),
    )

    return registry
