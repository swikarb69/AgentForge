"""Integration test for end-to-end planning engine pipeline and API routes."""

from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.planning.models import (
    ChangeType,
    Dependency,
    ImplementationPlan,
    PlanStatus,
    PlanTask,
)
from backend.app.planning.service import PlanningEngine

client = TestClient(app)
FIXTURE_PATH = Path("agentforge-fixtures/sample-project").resolve()


def test_scenario_1_straightforward_bug_fix() -> None:
    """Scenario 1: Bug fix with explicit requirements and acceptance criteria."""
    engine = PlanningEngine()
    plan = engine.create_plan(
        repository_path=FIXTURE_PATH,
        title="Fix verify_token in app/auth.py",
        description=(
            "### Requirements\n"
            "- Reject expired JWT tokens in `verify_token`.\n"
            "- Return HTTP 401.\n"
            "### Acceptance Criteria\n"
            "- Expired tokens return 401 status code."
        ),
        issue_id="ISSUE-SCENARIO-1",
    )

    assert plan.status == PlanStatus.READY
    assert plan.validation.valid is True
    assert len(plan.validation.errors) == 0

    # Verify direct impact target
    assert any(fc.path == "app/auth.py" for fc in plan.files_to_modify)

    # Verify tasks and ordering
    assert len(plan.tasks) >= 2
    assert [t.order for t in plan.tasks] == list(range(1, len(plan.tasks) + 1))

    # Verify test plan and verification cases
    assert len(plan.test_plan.verification_cases) >= 1
    assert any(
        "401" in vc.description or "401" in vc.expected_behavior
        for vc in plan.test_plan.verification_cases
    )

    # Verify dependencies exist and are acyclic
    assert len(plan.dependencies) >= 1
    assert not engine.dependency_analyzer.has_cycle(plan.dependencies)


def test_scenario_2_multi_file_change() -> None:
    """Scenario 2: Multi-file change spanning auth and database modules."""
    engine = PlanningEngine()
    plan = engine.create_plan(
        repository_path=FIXTURE_PATH,
        title="Refactor auth service and database session",
        description=(
            "### Requirements\n"
            "- Update `AuthService.verify_token` in `app/auth.py`.\n"
            "- Update `get_db_connection` in `app/database.py`.\n"
            "### Acceptance Criteria\n"
            "- Database connection is reused.\n"
            "- Auth verification works with database session."
        ),
        issue_id="ISSUE-SCENARIO-2",
    )

    assert plan.validation.valid is True
    assert len(plan.tasks) >= 3

    # Verify both implementation files are targeted
    modified_paths = {fc.path for fc in plan.files_to_modify}
    assert "app/auth.py" in modified_paths
    assert "app/database.py" in modified_paths

    # Verify multi-file blast radius risk is identified
    assert any("blast radius" in r.risk.lower() for r in plan.risks)


def test_scenario_3_ambiguous_issue() -> None:
    """Scenario 3: Ambiguous issue triggering NEEDS_CLARIFICATION status."""
    engine = PlanningEngine()
    plan = engine.create_plan(
        repository_path=FIXTURE_PATH,
        title="Login issue",
        description="it fails",
        issue_id="ISSUE-SCENARIO-3",
    )

    assert plan.status == PlanStatus.NEEDS_CLARIFICATION
    assert len(plan.unresolved_questions) > 0
    assert any("ambiguous" in r.risk.lower() for r in plan.risks)


def test_scenario_completely_ambiguous_blocked() -> None:
    """Verify completely ambiguous issue with no targets produces BLOCKED status."""
    engine = PlanningEngine()
    plan = engine.create_plan(
        repository_path=FIXTURE_PATH,
        title="Broken thing",
        description="it fails",
        issue_id="ISSUE-BLOCKED",
    )

    assert plan.validation.valid is False
    assert plan.status == PlanStatus.BLOCKED
    assert any("no target files or symbols" in err for err in plan.validation.errors)


def test_scenario_4_unresolved_reference() -> None:
    """Scenario 4: Issue referencing missing symbols/files results in blocked plan."""
    engine = PlanningEngine()
    plan = engine.create_plan(
        repository_path=FIXTURE_PATH,
        title="Fix MissingService.fake_method in app/ghost.py",
        description="Refactor `fake_method` in `app/ghost.py`.",
        issue_id="ISSUE-SCENARIO-4",
    )

    assert plan.validation.valid is False
    assert plan.status == PlanStatus.BLOCKED
    assert any("ghost.py" in err for err in plan.validation.errors)


def test_scenario_5_circular_dependency_validation() -> None:
    """Scenario 5: Plan with cyclic dependency fails validation."""
    cyclic_plan = ImplementationPlan(
        id="PLAN-CYCLE",
        issue_id="ISSUE-CYCLE",
        objective="Cyclic task test",
        summary="Plan with cyclic dependencies",
        status=PlanStatus.READY,
        tasks=[
            PlanTask(
                task_id="TASK-001",
                title="Task 1",
                description="desc 1",
                order=1,
                target_files=["app/auth.py"],
                change_type=ChangeType.MODIFY,
                dependencies=["TASK-002"],
                verification="v1",
            ),
            PlanTask(
                task_id="TASK-002",
                title="Task 2",
                description="desc 2",
                order=2,
                target_files=["app/auth.py"],
                change_type=ChangeType.MODIFY,
                dependencies=["TASK-001"],
                verification="v2",
            ),
        ],
        dependencies=[
            Dependency(
                source_task_id="TASK-001",
                target_task_id="TASK-002",
                reason="Task 1 depends on Task 2",
            ),
            Dependency(
                source_task_id="TASK-002",
                target_task_id="TASK-001",
                reason="Task 2 depends on Task 1",
            ),
        ],
    )

    response = client.post(
        "/api/v1/planning/validate",
        json={"plan": cyclic_plan.model_dump()},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["valid"] is False
    assert data["status"] == PlanStatus.BLOCKED
    assert any("Cyclic task dependency" in err for err in data["errors"])


def test_api_planning_create_endpoint() -> None:
    """Verify POST /api/v1/planning/create endpoint returns structured plan."""
    response = client.post(
        "/api/v1/planning/create",
        json={
            "repository_path": str(FIXTURE_PATH),
            "title": "Fix verify_token in app/auth.py",
            "description": (
                "### Requirements\n"
                "- Reject expired JWT tokens.\n"
                "### Acceptance Criteria\n"
                "- Expired tokens return 401."
            ),
            "id": "ISSUE-API-1",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "PLAN-ISSUE-API-1"
    assert data["status"] == "ready"
    assert len(data["tasks"]) >= 2
    assert data["validation"]["valid"] is True


def test_api_planning_create_invalid_repo() -> None:
    """Verify POST /api/v1/planning/create with non-existent repo returns 400."""
    response = client.post(
        "/api/v1/planning/create",
        json={
            "repository_path": "/invalid/nonexistent/repo",
            "title": "Fix something",
            "description": "desc",
        },
    )

    assert response.status_code == 400
    assert "does not exist" in response.json()["detail"]
