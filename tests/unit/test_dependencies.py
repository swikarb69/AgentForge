"""Unit tests for task dependency analysis and cycle detection."""

import pytest

from backend.app.planning.dependencies import DependencyAnalyzer
from backend.app.planning.models import ChangeType, Dependency, PlanTask


def test_linear_task_dependencies() -> None:
    """Verify linear dependency chain from creation to implementation to tests."""
    tasks = [
        PlanTask(
            task_id="TASK-001",
            title="Create app/helper.py",
            description="Create helper file",
            order=1,
            target_files=["app/helper.py"],
            change_type=ChangeType.CREATE,
            verification="Check file created",
        ),
        PlanTask(
            task_id="TASK-002",
            title="Modify app/helper.py",
            description="Add function to helper",
            order=2,
            target_files=["app/helper.py"],
            change_type=ChangeType.MODIFY,
            verification="Check function exists",
        ),
        PlanTask(
            task_id="TASK-003",
            title="Test helper",
            description="Run tests for helper",
            order=3,
            target_files=["tests/test_helper.py"],
            change_type=ChangeType.TEST,
            verification="Pytest passes",
        ),
    ]

    analyzer = DependencyAnalyzer()
    dependencies = analyzer.analyze_dependencies(tasks)

    assert len(dependencies) >= 2
    # TASK-002 depends on TASK-001
    assert any(
        d.source_task_id == "TASK-002" and d.target_task_id == "TASK-001"
        for d in dependencies
    )
    # TASK-003 depends on TASK-002 or TASK-001
    assert any(
        d.source_task_id == "TASK-003" and d.target_task_id == "TASK-002"
        for d in dependencies
    )

    # Check PlanTask dependencies list synced
    t2 = next(t for t in tasks if t.task_id == "TASK-002")
    assert "TASK-001" in t2.dependencies

    assert not analyzer.has_cycle(dependencies)
    sorted_order = analyzer.topological_sort(tasks, dependencies)
    assert sorted_order == ["TASK-001", "TASK-002", "TASK-003"]


def test_branching_dependencies() -> None:
    """Verify diamond / branching dependency resolution."""
    tasks = [
        PlanTask(
            task_id="TASK-001",
            title="Base model",
            description="Define base model",
            order=1,
            target_files=["app/models.py"],
            change_type=ChangeType.CREATE,
            verification="Model valid",
        ),
        PlanTask(
            task_id="TASK-002",
            title="User service",
            description="Define user service",
            order=2,
            target_files=["app/user_service.py"],
            change_type=ChangeType.MODIFY,
            verification="Service valid",
        ),
        PlanTask(
            task_id="TASK-003",
            title="Auth service",
            description="Define auth service",
            order=3,
            target_files=["app/auth_service.py"],
            change_type=ChangeType.MODIFY,
            verification="Auth valid",
        ),
        PlanTask(
            task_id="TASK-004",
            title="Integration tests",
            description="Test user and auth services",
            order=4,
            target_files=["tests/test_integration.py"],
            change_type=ChangeType.TEST,
            verification="All tests pass",
        ),
    ]

    deps = [
        Dependency(
            source_task_id="TASK-002",
            target_task_id="TASK-001",
            reason="Requires model",
        ),
        Dependency(
            source_task_id="TASK-003",
            target_task_id="TASK-001",
            reason="Requires model",
        ),
        Dependency(
            source_task_id="TASK-004",
            target_task_id="TASK-002",
            reason="Test user",
        ),
        Dependency(
            source_task_id="TASK-004",
            target_task_id="TASK-003",
            reason="Test auth",
        ),
    ]

    analyzer = DependencyAnalyzer()
    assert not analyzer.has_cycle(deps)

    sorted_ids = analyzer.topological_sort(tasks, deps)
    assert sorted_ids.index("TASK-001") < sorted_ids.index("TASK-002")
    assert sorted_ids.index("TASK-001") < sorted_ids.index("TASK-003")
    assert sorted_ids.index("TASK-002") < sorted_ids.index("TASK-004")
    assert sorted_ids.index("TASK-003") < sorted_ids.index("TASK-004")


def test_cycle_detection() -> None:
    """Verify detection of cyclic dependencies."""
    deps = [
        Dependency(
            source_task_id="TASK-001",
            target_task_id="TASK-002",
            reason="A depends on B",
        ),
        Dependency(
            source_task_id="TASK-002",
            target_task_id="TASK-003",
            reason="B depends on C",
        ),
        Dependency(
            source_task_id="TASK-003",
            target_task_id="TASK-001",
            reason="C depends on A",
        ),
    ]

    analyzer = DependencyAnalyzer()
    assert analyzer.has_cycle(deps) is True
    cycle = analyzer.find_cycle(deps)
    assert cycle is not None
    assert len(cycle) >= 3

    tasks = [
        PlanTask(
            task_id=f"TASK-00{i}",
            title=f"Task {i}",
            description="desc",
            order=i,
            target_files=[],
            change_type=ChangeType.MODIFY,
            verification="ver",
        )
        for i in range(1, 4)
    ]

    with pytest.raises(ValueError, match="Cyclic dependency detected"):
        analyzer.topological_sort(tasks, deps)
