"""Integration tests for the Debugging and Automatic Repair Engine pipeline and API."""

import hashlib
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.api.routes import debugging as debugging_route
from backend.app.debugging.models import (
    RepairConfiguration,
    RepairEvaluationVerdict,
    RepairRequest,
    RepairState,
)
from backend.app.debugging.repair_controller import BoundedRepairController
from backend.app.debugging.service import DebuggingService
from backend.app.execution.docker_runner import DockerRunner, MockDockerClient
from backend.app.execution.models import (
    ExecutionResult,
    ExecutionStatus,
    VerificationReport,
)
from backend.app.execution.service import ExecutionService
from backend.app.main import app

client = TestClient(app)
FIXTURE_PATH = Path("agentforge-fixtures/sample-project").resolve()


def _get_tree_hashes(root: Path) -> dict[str, str]:
    """Compute sha256 hashes of all files to verify immutability."""
    tree_hashes = {}
    for p in root.rglob("*"):
        if p.is_file():
            rel = str(p.relative_to(root)).replace("\\", "/")
            tree_hashes[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return tree_hashes


def _make_failing_report(execution_id: str = "exec-init-fail") -> VerificationReport:
    """Create a mock failing report."""
    return VerificationReport(
        execution_id=execution_id,
        execution_success=False,
        patch_applied=True,
        tests_executed=True,
        verification_passed=False,
        failures=["tests/test_auth.py::test_login failed"],
        execution_result=ExecutionResult(
            execution_id=execution_id,
            status=ExecutionStatus.FAILED,
            exit_code=1,
            stdout=(
                "FAILED tests/test_auth.py::test_login - AssertionError: False != True"
            ),
            stderr="",
            errors=[],
        ),
    )


def test_scenario_1_end_to_end_repair_and_repository_immutability() -> None:
    """Scenario 1: End-to-end repair with verification and repository immutability."""
    before_hashes = _get_tree_hashes(FIXTURE_PATH)

    mock_docker = MockDockerClient(
        available=True,
        simulated_exit_code=0,
        simulated_stdout="=== 1 passed in 0.05s ===\n",
    )
    runner = DockerRunner(docker_client=mock_docker)
    exec_service = ExecutionService(docker_runner=runner)

    initial_report = _make_failing_report("exec-fail-1")
    exec_service._reports["exec-fail-1"] = initial_report

    controller = BoundedRepairController(execution_service=exec_service)
    service = DebuggingService(controller=controller, execution_service=exec_service)

    req = RepairRequest(
        repository_path=str(FIXTURE_PATH),
        execution_id="exec-fail-1",
        config=RepairConfiguration(max_attempts=3),
    )

    import anyio

    result = anyio.run(service.start_repair, req)

    # 1. State machine reached SUCCEEDED
    assert result.current_state == RepairState.SUCCEEDED
    assert result.final_verdict == RepairEvaluationVerdict.FIXED
    assert result.total_attempts >= 1
    assert result.final_patch is not None

    # 2. Audit history preserved
    assert len(result.attempts) >= 1
    first_attempt = result.attempts[0]
    assert first_attempt.classification is not None
    assert first_attempt.root_cause is not None
    assert first_attempt.strategy is not None
    assert first_attempt.proposal is not None
    assert first_attempt.execution_report is not None
    assert first_attempt.evaluation is not None
    assert len(first_attempt.state_transitions) > 0

    # 3. Repository immutability check: target repository is pristine
    after_hashes = _get_tree_hashes(FIXTURE_PATH)
    assert before_hashes == after_hashes


def test_scenario_2_environment_defect_safeguard() -> None:
    """Scenario 2: Environment defect halts cleanly without mutating repo."""
    before_hashes = _get_tree_hashes(FIXTURE_PATH)

    exec_service = ExecutionService()
    env_failed_report = VerificationReport(
        execution_id="exec-env-fail",
        execution_success=False,
        patch_applied=True,
        tests_executed=False,
        verification_passed=False,
        failures=["Docker daemon not running"],
        execution_result=ExecutionResult(
            execution_id="exec-env-fail",
            status=ExecutionStatus.FAILED,
            exit_code=1,
            stdout="",
            stderr="Cannot connect to the Docker daemon at unix:///var/run/docker.sock",
            errors=["Docker daemon not running"],
        ),
    )
    exec_service._reports["exec-env-fail"] = env_failed_report

    service = DebuggingService(execution_service=exec_service)
    req = RepairRequest(
        repository_path=str(FIXTURE_PATH),
        execution_id="exec-env-fail",
    )

    import anyio

    result = anyio.run(service.start_repair, req)

    assert result.current_state == RepairState.NEEDS_HUMAN_REVIEW
    assert result.human_intervention_required is True
    assert "Docker" in (result.human_intervention_reason or "")

    after_hashes = _get_tree_hashes(FIXTURE_PATH)
    assert before_hashes == after_hashes


def test_scenario_3_api_endpoints() -> None:
    """Scenario 3: Verify /api/v1/debugging REST endpoints."""
    # Wire initial report into debugging_route.debugging_service
    initial_report = _make_failing_report("exec-api-test")
    debugging_route.debugging_service.execution_service._reports["exec-api-test"] = (
        initial_report
    )

    # 1. POST /api/v1/debugging/repair
    payload = {
        "repository_path": str(FIXTURE_PATH),
        "execution_id": "exec-api-test",
        "config": {
            "max_attempts": 2,
            "auto_execute_repairs": False,  # Stop after generation for quick test
        },
    }
    response = client.post("/api/v1/debugging/repair", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "repair_id" in data
    repair_id = data["repair_id"]
    assert data["initial_execution_id"] == "exec-api-test"

    # 2. GET /api/v1/debugging/repairs
    list_resp = client.get("/api/v1/debugging/repairs")
    assert list_resp.status_code == 200
    repairs = list_resp.json()
    assert any(r["repair_id"] == repair_id for r in repairs)

    # 3. GET /api/v1/debugging/repair/{repair_id}
    get_resp = client.get(f"/api/v1/debugging/repair/{repair_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["repair_id"] == repair_id

    # 4. GET /api/v1/debugging/repair/{repair_id}/history
    hist_resp = client.get(f"/api/v1/debugging/repair/{repair_id}/history")
    assert hist_resp.status_code == 200
    assert isinstance(hist_resp.json(), list)

    # 5. GET 404 on nonexistent repair
    not_found = client.get("/api/v1/debugging/repair/repair-nonexistent")
    assert not_found.status_code == 404
