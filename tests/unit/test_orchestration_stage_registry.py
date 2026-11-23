"""Unit tests for StageRegistry and stage dependency validation."""

import pytest

from backend.app.orchestration.exceptions import OrchestrationError
from backend.app.orchestration.stage_registry import (
    StageRegistry,
    create_default_stage_registry,
)
from backend.app.orchestration.stages import (
    StageDefinition,
    StageInput,
    StageOutput,
    WorkflowStageAdapter,
)
from backend.app.orchestration.states import StageStatus, StageType


class DummyAdapter(WorkflowStageAdapter):
    """Stub adapter for testing registry behavior."""

    async def execute(self, stage_input: StageInput) -> StageOutput:
        return StageOutput(
            stage_type=stage_input.stage.stage_type,
            status=StageStatus.COMPLETED,
        )


def test_default_stage_registry_completeness_and_order() -> None:
    """Verify default registry contains all 9 stages in valid topological order."""
    registry = create_default_stage_registry()
    stages = registry.list_stages()
    assert len(stages) == 9

    expected_order = [
        StageType.REPOSITORY_ANALYSIS,
        StageType.ISSUE_ANALYSIS,
        StageType.IMPLEMENTATION_PLANNING,
        StageType.CODE_GENERATION,
        StageType.EXECUTION_VERIFICATION,
        StageType.AUTOMATIC_REPAIR,
        StageType.CODE_REVIEW,
        StageType.HUMAN_APPROVAL,
        StageType.CONTROLLED_RELEASE,
    ]

    actual_order = registry.get_execution_order()
    assert len(actual_order) == len(expected_order)
    # Repository analysis must precede issue analysis, planning, generation, etc.
    assert actual_order.index(StageType.REPOSITORY_ANALYSIS) < actual_order.index(
        StageType.ISSUE_ANALYSIS
    )
    assert actual_order.index(StageType.ISSUE_ANALYSIS) < actual_order.index(
        StageType.IMPLEMENTATION_PLANNING
    )
    assert actual_order.index(StageType.IMPLEMENTATION_PLANNING) < actual_order.index(
        StageType.CODE_GENERATION
    )
    assert actual_order.index(StageType.CODE_GENERATION) < actual_order.index(
        StageType.EXECUTION_VERIFICATION
    )
    assert actual_order.index(StageType.CODE_REVIEW) < actual_order.index(
        StageType.HUMAN_APPROVAL
    )
    assert actual_order.index(StageType.HUMAN_APPROVAL) < actual_order.index(
        StageType.CONTROLLED_RELEASE
    )


def test_stage_registry_duplicate_registration_rejected() -> None:
    """Verify duplicate registration raises OrchestrationError unless overridden."""
    registry = StageRegistry()
    defn = StageDefinition(
        stage_type=StageType.REPOSITORY_ANALYSIS,
        name="Repo Analysis",
        description="Scan codebase",
        prerequisites=[],
    )
    adapter = DummyAdapter()
    registry.register(defn, adapter)

    # Duplicate without override raises
    with pytest.raises(OrchestrationError) as exc_info:
        registry.register(defn, adapter, allow_override=False)
    assert "already registered" in str(exc_info.value)

    # Allowed with override
    registry.register(defn, adapter, allow_override=True)
    assert (
        registry.get_definition(StageType.REPOSITORY_ANALYSIS).name == "Repo Analysis"
    )


def test_stage_registry_missing_lookup_raises() -> None:
    """Verify unregistered stage lookup raises OrchestrationError."""
    registry = StageRegistry()
    with pytest.raises(OrchestrationError) as exc_info:
        registry.get_definition(StageType.CODE_REVIEW)
    assert "not registered" in str(exc_info.value)

    with pytest.raises(OrchestrationError):
        registry.get_adapter(StageType.CODE_REVIEW)


def test_stage_registry_missing_prerequisite_detection() -> None:
    """Verify registry raises if a stage depends on an unregistered prerequisite."""
    registry = StageRegistry()
    defn = StageDefinition(
        stage_type=StageType.ISSUE_ANALYSIS,
        name="Issue Analysis",
        description="Analyze issue",
        prerequisites=[StageType.REPOSITORY_ANALYSIS],
    )
    registry.register(defn, DummyAdapter())

    with pytest.raises(OrchestrationError) as exc_info:
        registry.validate_dependencies()
    assert "requires unregistered prerequisite" in str(exc_info.value)


def test_stage_registry_circular_dependency_detection() -> None:
    """Verify registry detects circular dependencies between stages."""
    registry = StageRegistry()
    defn_a = StageDefinition(
        stage_type=StageType.CODE_GENERATION,
        name="Gen",
        description="Gen",
        prerequisites=[StageType.EXECUTION_VERIFICATION],
    )
    defn_b = StageDefinition(
        stage_type=StageType.EXECUTION_VERIFICATION,
        name="Exec",
        description="Exec",
        prerequisites=[StageType.CODE_GENERATION],
    )
    registry.register(defn_a, DummyAdapter())
    registry.register(defn_b, DummyAdapter())

    with pytest.raises(OrchestrationError) as exc_info:
        registry.validate_dependencies()
    assert "Circular dependency detected" in str(exc_info.value)
