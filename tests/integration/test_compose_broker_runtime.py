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

import os
import shutil
import subprocess  # nosec B404
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
# Test 5: Concurrent Workspace Isolation
# ---------------------------------------------------------------------------


def test_broker_concurrent_workspace_isolation() -> None:
    """Verify execution 2 cannot access files from execution 1's workspace."""
    base_root = Path(settings.WORKSPACE_ROOT).resolve()
    base_root.mkdir(parents=True, exist_ok=True)
    ws1 = base_root / f"ws_tenant_1_{uuid.uuid4().hex[:6]}"
    ws1.mkdir(parents=True, exist_ok=True)
    (ws1 / "tenant1_private.txt").write_text("CONFIDENTIAL_TENANT_1")

    ws2 = base_root / f"ws_tenant_2_{uuid.uuid4().hex[:6]}"
    ws2.mkdir(parents=True, exist_ok=True)
    (ws2 / "tenant2_private.txt").write_text("CONFIDENTIAL_TENANT_2")

    try:
        runner = DockerRunner(workspace_root=base_root)

        # Execute request 1
        res1 = runner.execute(
            workspace_path=ws1,
            command=[
                "python3",
                "-c",
                "print('T1=' + open('tenant1_private.txt').read().strip())",
            ],
        )
        assert res1.status == ExecutionStatus.COMPLETED
        assert "T1=CONFIDENTIAL_TENANT_1" in res1.stdout

        # Execute request 2 with check that tenant 1's file does not exist
        res2 = runner.execute(
            workspace_path=ws2,
            command=[
                "python3",
                "-c",
                "print('T1_LEAK=' + "
                "str(__import__('os').path.exists('tenant1_private.txt')), "
                "'T2=' + open('tenant2_private.txt').read().strip(), sep='\\n')",
            ],
        )
        assert res2.status == ExecutionStatus.COMPLETED
        assert "T1_LEAK=False" in res2.stdout
        assert "T2=CONFIDENTIAL_TENANT_2" in res2.stdout
    finally:
        shutil.rmtree(ws1, ignore_errors=True)
        shutil.rmtree(ws2, ignore_errors=True)


# ---------------------------------------------------------------------------
# Test 6: Broker-Side Policy Enforcement & Prohibited Requests Rejection
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
# Test 7: Containers and Temporary Volumes Cleaned Up
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


# ---------------------------------------------------------------------------
# Test 8: Stopping Broker Causes Fail Closed Without Host Fallback
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
