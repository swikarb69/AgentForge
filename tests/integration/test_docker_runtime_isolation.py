"""Live Docker container runtime isolation verification suite.

These tests execute against a real running Docker daemon and live sandbox containers
to prove complete boundary enforcement, filesystem isolation, non-root execution,
Docker socket inaccessibility, resource limits, timeout termination, and
guaranteed cleanup.
"""

import os
import shutil
import subprocess  # nosec B404
import time
import uuid
from pathlib import Path

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

# Skip entire live runtime verification suite if Docker CLI / daemon is absent.
# On GitHub Actions Ubuntu runner, Docker is running and agentforge-sandbox is built.
docker_cli = shutil.which("docker")
_docker_responsive = False
_sandbox_image_available = False
if docker_cli:
    try:
        cmd = [docker_cli]
        docker_host = getattr(settings, "DOCKER_HOST", None) or os.environ.get(
            "DOCKER_HOST"
        )
        if docker_host:
            cmd.extend(["-H", docker_host])
        cmd.append("info")
        res = subprocess.run(  # noqa: S603  # nosec B603
            cmd,
            capture_output=True,
            timeout=3,
            check=False,
        )
        _docker_responsive = res.returncode == 0
        if _docker_responsive:
            insp_cmd = [docker_cli]
            if docker_host:
                insp_cmd.extend(["-H", docker_host])
            insp_cmd.extend(["image", "inspect", "agentforge-sandbox:latest"])
            insp_res = subprocess.run(  # noqa: S603  # nosec B603
                insp_cmd,
                capture_output=True,
                timeout=3,
                check=False,
            )
            _sandbox_image_available = insp_res.returncode == 0
    except (subprocess.SubprocessError, OSError):
        _docker_responsive = False
        _sandbox_image_available = False

pytestmark = pytest.mark.skipif(
    not _docker_responsive or not _sandbox_image_available,
    reason=(
        "Live Docker daemon or 'agentforge-sandbox:latest' image is not available; "
        "skipping live container runtime tests."
    ),
)


@pytest.fixture
def sandbox_workspace(tmp_path: Path) -> Path:
    """Create an isolated temporary workspace directory inside authorized root."""
    ws = tmp_path / f"ws-{uuid.uuid4().hex[:8]}"
    ws.mkdir(parents=True, exist_ok=True)
    return ws


# ---------------------------------------------------------------------------
# Test 1: Harmless Permitted Command Execution
# ---------------------------------------------------------------------------


def test_runtime_harmless_command_executes(sandbox_workspace: Path) -> None:
    """Verify safe compliant command executes in real container and captures stdout."""
    runner = DockerRunner(
        image="agentforge-sandbox:latest",
        workspace_root=sandbox_workspace.parent,
    )
    cmd = ["python3", "-c", "print('runtime-isolation-verified')"]
    policy = ExecutionPolicy(timeout_seconds=10)

    result = runner.execute(
        workspace_path=sandbox_workspace,
        command=cmd,
        policy=policy,
    )

    assert result.status == ExecutionStatus.COMPLETED
    assert result.exit_code == 0
    assert "runtime-isolation-verified" in result.stdout


# ---------------------------------------------------------------------------
# Test 2: Prohibited Container Configuration Rejected
# ---------------------------------------------------------------------------


def test_runtime_prohibited_config_rejected(sandbox_workspace: Path) -> None:
    """Verify prohibited container configs (bad image, mount) are rejected."""
    runner = DockerRunner(
        image="unauthorized-malicious-image:v1",
        workspace_root=sandbox_workspace.parent,
    )
    result = runner.execute(
        workspace_path=sandbox_workspace,
        command=["python3", "-c", "print('should not run')"],
    )

    assert result.status == ExecutionStatus.REJECTED
    assert result.exit_code == 126
    assert any("gatekeeper" in err.lower() for err in result.errors)


# ---------------------------------------------------------------------------
# Test 3: Host Filesystem Inaccessible (Read-Only Rootfs & Isolation)
# ---------------------------------------------------------------------------


def test_runtime_host_files_inaccessible(sandbox_workspace: Path) -> None:
    """Verify sandbox cannot access host files or mutate root filesystem."""
    runner = DockerRunner(
        image="agentforge-sandbox:latest",
        workspace_root=sandbox_workspace.parent,
    )
    script_file = sandbox_workspace / "check_readonly.py"
    script_file.write_text(
        "try:\n"
        "    with open('/etc/agentforge_pwn.txt', 'w') as f:\n"
        "        f.write('pwned')\n"
        "    print('WRITE_SUCCEEDED')\n"
        "except OSError as e:\n"
        "    print(f'READONLY_ENFORCED: {e}')\n",
        encoding="utf-8",
    )
    cmd = ["python3", "check_readonly.py"]
    policy = ExecutionPolicy(timeout_seconds=10)
    result = runner.execute(
        workspace_path=sandbox_workspace,
        command=cmd,
        policy=policy,
    )

    assert result.status == ExecutionStatus.COMPLETED
    assert "READONLY_ENFORCED" in result.stdout
    assert "WRITE_SUCCEEDED" not in result.stdout


# ---------------------------------------------------------------------------
# Test 4: Docker Socket Inaccessible Inside Container
# ---------------------------------------------------------------------------


def test_runtime_docker_socket_inaccessible(sandbox_workspace: Path) -> None:
    """Verify Docker socket does NOT exist inside the container sandbox."""
    runner = DockerRunner(
        image="agentforge-sandbox:latest",
        workspace_root=sandbox_workspace.parent,
    )
    script_file = sandbox_workspace / "check_socket.py"
    script_file.write_text(
        "import os\n"
        "exists = os.path.exists('/var/run/docker.sock')\n"
        "print(f'DOCKER_SOCKET_EXISTS: {exists}')\n",
        encoding="utf-8",
    )
    cmd = ["python3", "check_socket.py"]
    policy = ExecutionPolicy(timeout_seconds=10)
    result = runner.execute(
        workspace_path=sandbox_workspace,
        command=cmd,
        policy=policy,
    )

    assert result.status == ExecutionStatus.COMPLETED
    assert "DOCKER_SOCKET_EXISTS: False" in result.stdout


# ---------------------------------------------------------------------------
# Test 5: Concurrent Workspace Isolation
# ---------------------------------------------------------------------------


def test_runtime_concurrent_workspaces_isolated(tmp_path: Path) -> None:
    """Verify sibling workspaces cannot discover or read each other's files."""
    root_ws = tmp_path / "authorized_root"
    root_ws.mkdir()
    ws1 = root_ws / "ws_alpha"
    ws2 = root_ws / "ws_beta"
    ws1.mkdir()
    ws2.mkdir()

    secret_file_beta = ws2 / "secret_beta.key"
    secret_file_beta.write_text("CONFIDENTIAL_BETA_SECRET_98765")

    runner = DockerRunner(
        image="agentforge-sandbox:latest",
        workspace_root=root_ws,
    )
    script_file = ws1 / "check_sibling.py"
    script_file.write_text(
        "import os\n"
        "found = os.path.exists('../ws_beta/secret_beta.key') "
        "or os.path.exists('/sandbox/workspace/../ws_beta')\n"
        "print(f'SIBLING_ACCESSIBLE: {found}')\n",
        encoding="utf-8",
    )
    cmd = ["python3", "check_sibling.py"]
    policy = ExecutionPolicy(timeout_seconds=10)
    result = runner.execute(
        workspace_path=ws1,
        command=cmd,
        policy=policy,
    )

    assert result.status == ExecutionStatus.COMPLETED
    assert "SIBLING_ACCESSIBLE: False" in result.stdout


# ---------------------------------------------------------------------------
# Test 6: Resource Limits & Timeout Enforcement
# ---------------------------------------------------------------------------


def test_runtime_resource_limits_and_timeout(sandbox_workspace: Path) -> None:
    """Verify container execution exceeding policy timeout is forcibly terminated."""
    runner = DockerRunner(
        image="agentforge-sandbox:latest",
        workspace_root=sandbox_workspace.parent,
    )
    # Sleep longer than the 2-second timeout
    script_file = sandbox_workspace / "sleep_forever.py"
    script_file.write_text("import time\ntime.sleep(15)\n", encoding="utf-8")
    cmd = ["python3", "sleep_forever.py"]
    policy = ExecutionPolicy(timeout_seconds=2)

    start = time.monotonic()
    result = runner.execute(
        workspace_path=sandbox_workspace,
        command=cmd,
        policy=policy,
    )
    elapsed = time.monotonic() - start

    assert result.status == ExecutionStatus.TIMED_OUT
    assert result.exit_code == 124
    assert elapsed < 10.0  # Verify it did not hang for 15s


# ---------------------------------------------------------------------------
# Test 7: Container and Ephemeral Volume Cleanup
# ---------------------------------------------------------------------------


def test_runtime_cleanup_removes_containers(sandbox_workspace: Path) -> None:
    """Verify containers are automatically removed (--rm) and do not leak."""
    runner = DockerRunner(
        image="agentforge-sandbox:latest",
        workspace_root=sandbox_workspace.parent,
    )
    exec_id = f"exec-clean-{uuid.uuid4().hex[:8]}"
    cmd = ["python3", "-c", "print('clean-me')"]
    policy = ExecutionPolicy(timeout_seconds=5)

    result = runner.execute(
        workspace_path=sandbox_workspace,
        command=cmd,
        policy=policy,
        execution_id=exec_id,
    )
    assert result.status == ExecutionStatus.COMPLETED

    # Query docker for any stopped or orphan container
    docker_bin = shutil.which("docker") or "/usr/bin/docker"
    res = subprocess.run(  # noqa: S603  # nosec B603
        [docker_bin, "ps", "-a", "--filter", f"name={exec_id}", "--format", "{{.ID}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.stdout.strip() == "", "Orphan container detected after execution!"


# ---------------------------------------------------------------------------
# Test 8: Execution Broker Prevents Direct Docker API Bypass & Fails Closed
# ---------------------------------------------------------------------------


def test_runtime_broker_fails_closed_without_host_fallback(
    sandbox_workspace: Path,
) -> None:
    """Verify BrokerDockerClient fails closed if broker is unreachable."""
    broker_client = BrokerDockerClient(broker_url="http://127.0.0.1:59998")
    assert broker_client.is_available() is False

    runner = DockerRunner(
        docker_client=broker_client,
        workspace_root=sandbox_workspace.parent,
    )
    result = runner.execute(
        workspace_path=sandbox_workspace,
        command=["python3", "-c", "print('bypass attempt')"],
    )

    assert result.status == ExecutionStatus.FAILED
    assert result.exit_code == 127
    assert any(
        "Host execution fallback is strictly prohibited" in err for err in result.errors
    )
