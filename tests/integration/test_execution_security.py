"""Integration tests for execution sandbox security boundaries and defenses."""

from pathlib import Path

import anyio

from backend.app.execution.docker_runner import DockerRunner, MockDockerClient
from backend.app.execution.models import (
    CreateExecutionRequest,
    ExecutionPolicy,
    VerificationProfile,
)
from backend.app.execution.service import ExecutionService
from backend.app.execution.workspace import WorkspaceManager

FIXTURE_PATH = Path("agentforge-fixtures/sample-project").resolve()


def test_security_docker_unavailable_prohibits_host_fallback() -> None:
    """Verify that Docker unavailability blocks execution without running on host."""
    mock_client = MockDockerClient(available=False)
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
            '+    print("Sample Project Running Verified")\n'
        ),
        verification_profile=VerificationProfile.PYTHON_UNIT_TESTS,
    )

    report = anyio.run(service.execute_and_verify, request)

    assert report.verification_passed is False
    assert report.execution_success is False
    assert report.patch_applied is True
    assert any(
        "Host execution fallback is strictly prohibited" in f for f in report.failures
    )
    assert len(mock_client.history) == 0


def test_security_path_traversal_patch_blocked() -> None:
    """Verify patch with directory traversal is rejected and cannot escape workspace."""
    mock_client = MockDockerClient(available=True)
    runner = DockerRunner(docker_client=mock_client)
    service = ExecutionService(docker_runner=runner)

    traversal_patch = (
        "--- /dev/null\n"
        "+++ b/../../escape_test.py\n"
        "@@ -0,0 +1,1 @@\n"
        "+print('escaped')\n"
    )

    request = CreateExecutionRequest(
        repository_path=str(FIXTURE_PATH),
        patch_content=traversal_patch,
        verification_profile=VerificationProfile.PYTHON_UNIT_TESTS,
    )

    report = anyio.run(service.execute_and_verify, request)

    assert report.verification_passed is False
    assert report.patch_applied is False
    assert any("Path traversal" in f for f in report.failures)
    assert len(mock_client.history) == 0


def test_security_protected_files_modification_blocked() -> None:
    """Verify modification of protected paths (.env, id_rsa, etc.) is blocked."""
    mock_client = MockDockerClient(available=True)
    runner = DockerRunner(docker_client=mock_client)
    service = ExecutionService(docker_runner=runner)

    protected_patch = "--- /dev/null\n+++ b/.env\n@@ -0,0 +1,1 @@\n+EXPLOIT=true\n"

    request = CreateExecutionRequest(
        repository_path=str(FIXTURE_PATH),
        patch_content=protected_patch,
        verification_profile=VerificationProfile.PYTHON_UNIT_TESTS,
    )

    report = anyio.run(service.execute_and_verify, request)

    assert report.verification_passed is False
    assert report.patch_applied is False
    assert any("protected path is forbidden" in f for f in report.failures)
    assert len(mock_client.history) == 0


def test_security_policy_bounds_validation() -> None:
    """Verify invalid policy resource bounds are rejected before execution."""
    service = ExecutionService()

    invalid_policy = ExecutionPolicy()
    # Force out of bounds value
    object.__setattr__(invalid_policy, "timeout_seconds", 999)

    request = CreateExecutionRequest(
        repository_path=str(FIXTURE_PATH),
        patch_content="--- a/x.py\n+++ b/x.py\n",
        policy=invalid_policy,
    )

    report = anyio.run(service.execute_and_verify, request)

    assert report.verification_passed is False
    assert any("Timeout must be between 1 and 300" in f for f in report.failures)


def test_security_workspace_size_limit_enforced(tmp_path: Path) -> None:
    """Verify workspace size limit prevents filling up host disk."""
    ws_mgr = WorkspaceManager(
        workspace_root=tmp_path / "workspaces", size_limit_bytes=500
    )
    service = ExecutionService(workspace_manager=ws_mgr)

    request = CreateExecutionRequest(
        repository_path=str(FIXTURE_PATH),
        patch_content="--- a/x.py\n+++ b/x.py\n",
    )

    report = anyio.run(service.execute_and_verify, request)

    assert report.verification_passed is False
    assert any("Workspace size exceeds limit" in f for f in report.failures)
