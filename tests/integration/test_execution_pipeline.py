"""Integration tests for execution sandbox pipeline, API, and repo immutability."""

import hashlib
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.api.routes import execution as execution_route
from backend.app.execution.docker_runner import DockerRunner, MockDockerClient
from backend.app.execution.models import (
    CreateExecutionRequest,
    VerificationProfile,
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


def test_scenario_1_end_to_end_verification_pass() -> None:
    """Scenario 1: End-to-end patch application and container verification pass."""
    before_hashes = _get_tree_hashes(FIXTURE_PATH)

    mock_client = MockDockerClient(
        available=True,
        simulated_exit_code=0,
        simulated_stdout="=== 5 passed in 0.12s ===\n",
    )
    runner = DockerRunner(docker_client=mock_client)
    service = ExecutionService(docker_runner=runner)

    diff_text = (
        "--- a/app/main.py\n"
        "+++ b/app/main.py\n"
        "@@ -4,3 +4,3 @@\n"
        " def main() -> None:\n"
        '-    print("Sample Project Running")\n'
        '+    print("Sample Project Running Verified")\n'
    )

    request = CreateExecutionRequest(
        repository_path=str(FIXTURE_PATH),
        patch_content=diff_text,
        verification_profile=VerificationProfile.PYTHON_UNIT_TESTS,
    )

    import anyio

    report = anyio.run(service.execute_and_verify, request)

    # Assert verification succeeded
    assert report.verification_passed is True
    assert report.execution_success is True
    assert report.patch_applied is True
    assert report.tests_executed is True
    assert report.failures == []
    assert report.execution_result is not None
    assert report.execution_result.test_summary is not None
    assert report.execution_result.test_summary.total == 5
    assert report.execution_result.test_summary.passed == 5

    # STRICT ASSERTION: Original repository must remain completely unchanged!
    after_hashes = _get_tree_hashes(FIXTURE_PATH)
    assert after_hashes == before_hashes

    # Assert workspace was cleaned up
    ws_meta = report.execution_result.workspace_metadata
    assert ws_meta is not None
    assert not Path(ws_meta.workspace_path).exists()


def test_scenario_2_end_to_end_test_failure() -> None:
    """Scenario 2: Container execution reports test failures and synthesizes failure."""
    before_hashes = _get_tree_hashes(FIXTURE_PATH)

    mock_client = MockDockerClient(
        available=True,
        simulated_exit_code=1,
        simulated_stdout="=== 2 failed, 3 passed in 0.25s ===\n",
    )
    runner = DockerRunner(docker_client=mock_client)
    service = ExecutionService(docker_runner=runner)

    request = CreateExecutionRequest(
        repository_path=str(FIXTURE_PATH),
        patch_content=(
            "--- a/app/main.py\n"
            "+++ b/app/main.py\n"
            "@@ -4,3 +4,3 @@\n"
            " def main() -> None:\n"
            '-    print("Sample Project Running")\n'
            '+    print("Sample Project Failure")\n'
        ),
        verification_profile=VerificationProfile.PYTHON_UNIT_TESTS,
    )

    import anyio

    report = anyio.run(service.execute_and_verify, request)

    assert report.verification_passed is False
    assert report.execution_success is False
    assert report.patch_applied is True
    assert report.tests_executed is True
    assert any("2 test(s) failed" in f for f in report.failures)

    # Immutability check
    after_hashes = _get_tree_hashes(FIXTURE_PATH)
    assert after_hashes == before_hashes


def test_scenario_3_api_endpoints() -> None:
    """Scenario 3: Verify REST API endpoints (/create, /reports, /reports/{id})."""
    # Temporarily attach mock docker client to route's execution_service
    mock_client = MockDockerClient(
        available=True,
        simulated_exit_code=0,
        simulated_stdout="=== 1 passed in 0.05s ===\n",
    )
    orig_runner = execution_route.execution_service.docker_runner
    execution_route.execution_service.docker_runner = DockerRunner(
        docker_client=mock_client
    )
    execution_route.execution_service.test_executor = (
        execution_route.execution_service.test_executor.__class__(
            docker_runner=execution_route.execution_service.docker_runner
        )
    )

    try:
        # POST /api/v1/execution/create
        payload = {
            "repository_path": str(FIXTURE_PATH),
            "patch_content": (
                "--- a/app/main.py\n"
                "+++ b/app/main.py\n"
                "@@ -4,3 +4,3 @@\n"
                " def main() -> None:\n"
                '-    print("Sample Project Running")\n'
                '+    print("Sample Project Running Verified")\n'
            ),
            "verification_profile": "python-unit-tests",
        }
        res = client.post("/api/v1/execution/create", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert "execution_id" in data
        assert data["verification_passed"] is True
        exec_id = data["execution_id"]

        # GET /api/v1/execution/reports/{execution_id}
        get_res = client.get(f"/api/v1/execution/reports/{exec_id}")
        assert get_res.status_code == 200
        report_data = get_res.json()
        assert report_data["execution_id"] == exec_id
        assert report_data["verification_passed"] is True

        # GET /api/v1/execution/reports
        list_res = client.get("/api/v1/execution/reports")
        assert list_res.status_code == 200
        reports_list = list_res.json()
        assert len(reports_list) > 0
        assert any(r["execution_id"] == exec_id for r in reports_list)

        # 404 for nonexistent report
        not_found = client.get("/api/v1/execution/reports/nonexistent-id-999")
        assert not_found.status_code == 404

    finally:
        execution_route.execution_service.docker_runner = orig_runner
