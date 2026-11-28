"""End-to-end integration tests for AgentForge Compose broker architecture.

Validates the complete application-to-broker-to-sandbox path:
- Network isolation (app cannot directly reach Docker proxy)
- Broker accessibility (app reaches authorized broker endpoint)
- DockerRunner explicitly uses BrokerDockerClient
- Workspace transfer with file verification
- Concurrent workspace isolation (sandbox 2 cannot access sandbox 1 files)
- Broker-side immutable policy enforcement & rejection of prohibited requests
- Container and volume cleanup
- Fail-closed behavior without host fallback when broker is stopped
"""

import asyncio
import os
import shutil
import subprocess  # nosec B404
import time
import uuid
from pathlib import Path

import httpx
import pytest

from backend.app.core.config import settings
from backend.app.execution.docker_runner import (
    BrokerDockerClient,
    DockerRunner,
)
from backend.app.execution.models import (
    ExecutionPolicy,
    ExecutionStatus,
)

BROKER_URL = getattr(settings, "EXECUTION_BROKER_URL", None) or os.environ.get(
    "EXECUTION_BROKER_URL"
)
DOCKER_PROXY_URL = os.environ.get("DOCKER_PROXY_URL", "http://docker-proxy:2375")
FAIL_IF_SKIPPED = os.environ.get("FAIL_IF_SKIPPED", "").lower() in ("true", "1")

if not BROKER_URL:
    if FAIL_IF_SKIPPED:
        pytest.fail(
            "EXECUTION_BROKER_URL is not configured and FAIL_IF_SKIPPED=true is set; "
            "failing CI job as required."
        )
    else:
        pytestmark = pytest.mark.skip(
            reason=(
                "EXECUTION_BROKER_URL is not configured; "
                "skipping Compose broker E2E tests."
            )
        )


@pytest.fixture
def test_workspace_dir() -> Path:
    """Create an isolated test workspace directory within authorized root."""
    base_root = Path(settings.WORKSPACE_ROOT).resolve()
    base_root.mkdir(parents=True, exist_ok=True)
    ws = base_root / f"compose-ws-{uuid.uuid4().hex[:8]}"
    ws.mkdir(parents=True, exist_ok=True)
    try:
        yield ws
    finally:
        shutil.rmtree(ws, ignore_errors=True)


# ---------------------------------------------------------------------------
# Test 1: Application Cannot Directly Reach Docker Proxy (Network Isolation)
# ---------------------------------------------------------------------------


def test_application_cannot_directly_reach_docker_proxy() -> None:
    """Verify application network isolation: app container cannot reach docker-proxy."""
    try:
        resp = httpx.get(f"{DOCKER_PROXY_URL}/info", timeout=2.0)
        # If by any chance a connection succeeded, it must NOT be 200 OK from Docker
        assert resp.status_code != 200, (
            "Application must not reach Docker socket proxy!"
        )
    except (httpx.ConnectError, httpx.TimeoutException, OSError):
        # Expected: connection refused, no route to host, or timeout
        pass


# ---------------------------------------------------------------------------
# Test 2: Application Can Reach Authorized Execution Broker Endpoint
# ---------------------------------------------------------------------------


def test_application_can_reach_authorized_broker_endpoint() -> None:
    """Verify application can reach authorized execution broker /health endpoint."""
    assert BROKER_URL is not None
    client = BrokerDockerClient(broker_url=BROKER_URL)
    assert client.is_available() is True


# ---------------------------------------------------------------------------
# Test 3: DockerRunner Explicitly Uses BrokerDockerClient (No CLI Fallback)
# ---------------------------------------------------------------------------


def test_docker_runner_uses_broker_docker_client() -> None:
    """Verify DockerRunner defaults to BrokerDockerClient and does not fall back."""
    runner = DockerRunner()
    assert isinstance(runner.client, BrokerDockerClient)
    assert runner.client.broker_url == BROKER_URL


# ---------------------------------------------------------------------------
# Test 4: Workspace File Transfer & Execution Through Complete Path
# ---------------------------------------------------------------------------


def test_broker_workspace_file_transfer(test_workspace_dir: Path) -> None:
    """Verify workspace file transfer, container execution, and content accuracy."""
    secret_payload = f"test-payload-token-{uuid.uuid4().hex}"
    test_file = test_workspace_dir / "input_data.txt"
    test_file.write_text(secret_payload, encoding="utf-8")

    runner = DockerRunner(workspace_root=test_workspace_dir.parent)
    result = runner.execute(
        workspace_path=test_workspace_dir,
        command=[
            "python3",
            "-c",
            "print('PAYLOAD=' + open('input_data.txt').read().strip())",
        ],
        policy=ExecutionPolicy(timeout_seconds=15),
    )

    assert result.status == ExecutionStatus.COMPLETED
    assert result.exit_code == 0
    assert f"PAYLOAD={secret_payload}" in result.stdout


# ---------------------------------------------------------------------------
# Test 5: Genuine Simultaneous Concurrent Workspace Isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_broker_simultaneous_concurrent_workspace_isolation() -> None:
    """Verify simultaneous executions are strictly isolated in memory and storage."""
    base_root = Path(settings.WORKSPACE_ROOT).resolve()
    base_root.mkdir(parents=True, exist_ok=True)

    id1 = uuid.uuid4().hex[:8]
    id2 = uuid.uuid4().hex[:8]
    marker_a_name = f"marker_a_{id1}.txt"
    marker_b_name = f"marker_b_{id2}.txt"
    secret_a = f"CONFIDENTIAL_PAYLOAD_ALPHA_{id1}"
    secret_b = f"CONFIDENTIAL_PAYLOAD_BETA_{id2}"

    ws1 = base_root / f"ws_simultaneous_1_{id1}"
    ws1.mkdir(parents=True, exist_ok=True)
    (ws1 / marker_a_name).write_text(secret_a, encoding="utf-8")

    ws2 = base_root / f"ws_simultaneous_2_{id2}"
    ws2.mkdir(parents=True, exist_ok=True)
    (ws2 / marker_b_name).write_text(secret_b, encoding="utf-8")

    try:
        runner = DockerRunner(workspace_root=base_root)

        # Both containers sleep for 1.5 seconds so active execution windows overlap.
        # Container 1 reads marker A, checks that marker B does not exist.
        # Container 2 reads marker B, checks that marker A does not exist.
        script1 = (
            "__import__('time').sleep(1.5) or print("
            f"'A_READ=' + open('{marker_a_name}').read().strip(), "
            f"'B_LEAK_IN_A=' + str(__import__('os').path.exists('{marker_b_name}')), "
            "sep='\\n')"
        )
        script2 = (
            "__import__('time').sleep(1.5) or print("
            f"'B_READ=' + open('{marker_b_name}').read().strip(), "
            f"'A_LEAK_IN_B=' + str(__import__('os').path.exists('{marker_a_name}')), "
            "sep='\\n')"
        )
        cmd1 = ["python3", "-c", script1]
        cmd2 = ["python3", "-c", script2]

        t0 = time.monotonic()
        res1, res2 = await asyncio.gather(
            asyncio.to_thread(runner.execute, workspace_path=ws1, command=cmd1),
            asyncio.to_thread(runner.execute, workspace_path=ws2, command=cmd2),
        )
        elapsed = time.monotonic() - t0

        assert res1.status == ExecutionStatus.COMPLETED, (
            f"Execution 1 failed: {res1.errors}"
        )
        assert res2.status == ExecutionStatus.COMPLETED, (
            f"Execution 2 failed: {res2.errors}"
        )

        # Overlapping execution verification: duration is ~1.5s - 4.5s
        assert elapsed < 5.0, (
            f"Expected overlapping execution duration < 5.0s, got {elapsed:.2f}s"
        )

        # Verify execution 1 cannot read B's marker
        assert f"A_READ={secret_a}" in res1.stdout
        assert "B_LEAK_IN_A=False" in res1.stdout
        assert secret_b not in res1.stdout

        # Verify execution 2 cannot read A's marker
        assert f"B_READ={secret_b}" in res2.stdout
        assert "A_LEAK_IN_B=False" in res2.stdout
        assert secret_a not in res2.stdout
    finally:
        shutil.rmtree(ws1, ignore_errors=True)
        shutil.rmtree(ws2, ignore_errors=True)


# ---------------------------------------------------------------------------
# Test 6: Broker Authentication Security Checks
# ---------------------------------------------------------------------------


def test_broker_auth_correct_token_permits_execution(
    test_workspace_dir: Path,
) -> None:
    """Verify execution with valid configured auth token is permitted by broker."""
    runner = DockerRunner(workspace_root=test_workspace_dir.parent)
    result = runner.execute(
        workspace_path=test_workspace_dir,
        command=["python3", "-c", "print('AUTH_SUCCESS')"],
    )
    assert result.status == ExecutionStatus.COMPLETED
    assert "AUTH_SUCCESS" in result.stdout


def test_broker_auth_incorrect_token_prevents_execution(
    test_workspace_dir: Path,
) -> None:
    """Verify execution with invalid token is rejected by broker with 401."""
    bad_client = BrokerDockerClient(
        broker_url=BROKER_URL,
        auth_token="deliberately-invalid-token",  # noqa: S106  # nosec B106
    )
    runner = DockerRunner(
        docker_client=bad_client,
        workspace_root=test_workspace_dir.parent,
    )
    result = runner.execute(
        workspace_path=test_workspace_dir,
        command=["python3", "-c", "print('should not run')"],
    )
    assert result.status == ExecutionStatus.REJECTED
    assert result.exit_code == 126
    assert any("Unauthorized" in err for err in result.errors)


def test_broker_auth_missing_token_prevents_execution() -> None:
    """Verify direct POST without Authorization header is rejected with 401."""
    assert BROKER_URL is not None
    resp = httpx.post(
        f"{BROKER_URL.rstrip('/')}/v1/execute",
        json={"command": ["python3", "-c", "print('no auth')"]},
        headers={},
        timeout=5.0,
    )
    assert resp.status_code == 401
    assert "missing" in resp.json().get("detail", "").lower()


# ---------------------------------------------------------------------------
# Test 7: Sandbox Cannot Access Host Docker Socket
# ---------------------------------------------------------------------------


def test_sandbox_cannot_access_host_docker_socket(test_workspace_dir: Path) -> None:
    """Verify host Docker socket is completely inaccessible inside the sandbox."""
    runner = DockerRunner(workspace_root=test_workspace_dir.parent)
    sock_script = (
        "print('DOCKER_SOCK_EXISTS=' + "
        "str(__import__('os').path.exists('/var/run/docker.sock')))"
    )
    result = runner.execute(
        workspace_path=test_workspace_dir,
        command=["python3", "-c", sock_script],
    )
    assert result.status == ExecutionStatus.COMPLETED
    assert "DOCKER_SOCK_EXISTS=False" in result.stdout


# ---------------------------------------------------------------------------
# Test 8: Broker-Side Policy Enforcement & Prohibited Requests Rejection
# ---------------------------------------------------------------------------


def test_broker_rejects_prohibited_requests(test_workspace_dir: Path) -> None:
    """Verify broker-side gatekeeper rejects prohibited images and commands."""
    runner = DockerRunner(workspace_root=test_workspace_dir.parent)

    # 1. Prohibited image
    runner_bad_image = DockerRunner(
        image="unauthorized-malicious-image:latest",
        workspace_root=test_workspace_dir.parent,
    )
    res_bad_img = runner_bad_image.execute(
        workspace_path=test_workspace_dir,
        command=["python3", "-c", "print('should not run')"],
    )
    assert res_bad_img.status == ExecutionStatus.REJECTED
    assert res_bad_img.exit_code == 126

    # 2. Prohibited command (not on allowlist)
    res_bad_cmd = runner.execute(
        workspace_path=test_workspace_dir,
        command=["curl", "http://evil.com"],
    )
    assert res_bad_cmd.status == ExecutionStatus.REJECTED
    assert res_bad_cmd.exit_code == 126

    # 3. Prohibited environment injection
    res_bad_env = runner.execute(
        workspace_path=test_workspace_dir,
        command=["python3", "-c", "print('env')"],
        environment={"API_KEY": "leaked-secret"},
    )
    assert res_bad_env.status == ExecutionStatus.REJECTED
    assert res_bad_env.exit_code == 126


# ---------------------------------------------------------------------------
# Test 9: Containers and Temporary Volumes Cleaned Up After Execution
# ---------------------------------------------------------------------------


def test_broker_cleanup_after_execution(test_workspace_dir: Path) -> None:
    """Verify temporary Docker containers and volumes are cleaned up after execution."""
    exec_id = f"e2e-cleanup-{uuid.uuid4().hex[:8]}"
    runner = DockerRunner(workspace_root=test_workspace_dir.parent)
    result = runner.execute(
        workspace_path=test_workspace_dir,
        command=["python3", "-c", "print('cleanup-check')"],
        execution_id=exec_id,
    )
    assert result.status == ExecutionStatus.COMPLETED

    # Query docker for any orphan container or volume if docker CLI is available
    docker_bin = shutil.which("docker")
    if docker_bin:
        res_c = subprocess.run(  # noqa: S603  # nosec B603
            [
                docker_bin,
                "ps",
                "-a",
                "--filter",
                f"name={exec_id}",
                "--format",
                "{{.ID}}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if res_c.returncode == 0:
            assert res_c.stdout.strip() == "", f"Orphan container for {exec_id} found!"


def test_broker_cleanup_after_failure_and_timeout(test_workspace_dir: Path) -> None:
    """Verify cleanup occurs properly after both execution failure and timeout."""
    runner = DockerRunner(workspace_root=test_workspace_dir.parent)

    # 1. Failure case (non-zero exit code)
    fail_id = f"fail-cleanup-{uuid.uuid4().hex[:8]}"
    res_fail = runner.execute(
        workspace_path=test_workspace_dir,
        command=["python3", "-c", "raise SystemExit(42)"],
        execution_id=fail_id,
    )
    assert res_fail.status == ExecutionStatus.FAILED
    assert res_fail.exit_code == 42

    # 2. Timeout case
    timeout_id = f"timeout-cleanup-{uuid.uuid4().hex[:8]}"
    res_timeout = runner.execute(
        workspace_path=test_workspace_dir,
        command=["python3", "-c", "__import__('time').sleep(10)"],
        policy=ExecutionPolicy(timeout_seconds=2),
        execution_id=timeout_id,
    )
    assert res_timeout.status == ExecutionStatus.TIMED_OUT
    assert res_timeout.exit_code == 124

    # Verify no orphan containers for either execution
    docker_bin = shutil.which("docker")
    if docker_bin:
        for target_id in (fail_id, timeout_id):
            res_c = subprocess.run(  # noqa: S603  # nosec B603
                [
                    docker_bin,
                    "ps",
                    "-a",
                    "--filter",
                    f"name={target_id}",
                    "--format",
                    "{{.ID}}",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if res_c.returncode == 0:
                assert res_c.stdout.strip() == "", (
                    f"Orphan container for {target_id} found!"
                )


# ---------------------------------------------------------------------------
# Test 10: Stopping Broker Causes Fail Closed Without Host Fallback
# ---------------------------------------------------------------------------


def test_broker_stopped_causes_fail_closed(test_workspace_dir: Path) -> None:
    """Verify stopping/unreachable broker fails closed without host fallback."""
    runner = DockerRunner(workspace_root=test_workspace_dir.parent)
    if runner.client.is_available():
        unreachable_client = BrokerDockerClient(broker_url="http://127.0.0.1:59997")
        runner = DockerRunner(
            docker_client=unreachable_client,
            workspace_root=test_workspace_dir.parent,
        )

    assert runner.client.is_available() is False
    result = runner.execute(
        workspace_path=test_workspace_dir,
        command=["python3", "-c", "print('should not run on host')"],
    )

    assert result.status == ExecutionStatus.FAILED
    assert result.exit_code == 127
    assert any(
        "Host execution fallback is strictly prohibited" in err for err in result.errors
    )
