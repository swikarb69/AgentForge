"""Adversarial regression test suite for Docker API and container runtime isolation."""

from pathlib import Path
from unittest.mock import patch

import pytest

from backend.app.core.config import Settings
from backend.app.execution.broker import (
    ExecutionBrokerRequest,
    SandboxExecutionBroker,
)
from backend.app.execution.docker_runner import (
    BrokerDockerClient,
    CLIDockerClient,
    DockerRunner,
    MockDockerClient,
)
from backend.app.execution.exceptions import ContainerSecurityError
from backend.app.execution.gatekeeper import (
    DEFAULT_ALLOWED_IMAGES,
    SandboxContainerPayloadValidator,
)
from backend.app.execution.models import ExecutionPolicy, ExecutionStatus

# ---------------------------------------------------------------------------
# 1. Docker Engine API POST /containers/create Payload Validation Tests
# ---------------------------------------------------------------------------


def _valid_api_payload() -> dict:
    """Return a valid baseline Docker API container creation payload."""
    return {
        "Image": "agentforge-sandbox:0.11.0",
        "Cmd": ["pytest", "-q"],
        "User": "10001:10001",
        "HostConfig": {
            "Privileged": False,
            "SecurityOpt": ["no-new-privileges:true"],
            "CapDrop": ["ALL"],
            "ReadonlyRootfs": True,
            "NetworkMode": "none",
            "PidMode": "",
            "IpcMode": "",
            "Memory": 536870912,
            "NanoCpus": 1000000000,
            "PidsLimit": 64,
            "Binds": ["agentforge-ws-exec-001:/sandbox/workspace:rw"],
        },
    }


def test_api_payload_accepts_compliant_configuration() -> None:
    """Verify compliant payload passes validation without error."""
    payload = _valid_api_payload()
    # Should not raise
    SandboxContainerPayloadValidator.validate_docker_api_payload(payload)


@pytest.mark.parametrize(
    "bad_image",
    [
        "ubuntu:latest",
        "alpine:3.18",
        "python:3.13-slim",
        "malicious-image:v1",
        "docker:dind",
    ],
)
def test_api_payload_rejects_unapproved_images(bad_image: str) -> None:
    """Verify unapproved container images are rejected by allowlist."""
    payload = _valid_api_payload()
    payload["Image"] = bad_image
    with pytest.raises(ContainerSecurityError, match="not in permitted sandbox images"):
        SandboxContainerPayloadValidator.validate_docker_api_payload(payload)


def test_api_payload_rejects_privileged_mode() -> None:
    """Verify Privileged: true is strictly prohibited in API payload."""
    payload = _valid_api_payload()
    payload["HostConfig"]["Privileged"] = True
    with pytest.raises(
        ContainerSecurityError, match="Privileged container mode is strictly prohibited"
    ):
        SandboxContainerPayloadValidator.validate_docker_api_payload(payload)


@pytest.mark.parametrize(
    ("namespace_key", "value", "match_str"),
    [
        ("PidMode", "host", "Host PID namespace"),
        ("IpcMode", "host", "Host IPC namespace"),
        ("NetworkMode", "host", "Host network namespace"),
    ],
)
def test_api_payload_rejects_host_namespaces(
    namespace_key: str, value: str, match_str: str
) -> None:
    """Verify host PID, IPC, and network namespaces are strictly rejected."""
    payload = _valid_api_payload()
    payload["HostConfig"][namespace_key] = value
    with pytest.raises(ContainerSecurityError, match=match_str):
        SandboxContainerPayloadValidator.validate_docker_api_payload(payload)


def test_api_payload_rejects_host_devices() -> None:
    """Verify host device mapping (Devices) is strictly rejected."""
    payload = _valid_api_payload()
    payload["HostConfig"]["Devices"] = [
        {"PathOnHost": "/dev/sda", "PathInContainer": "/dev/sda"}
    ]
    with pytest.raises(
        ContainerSecurityError, match="Host device mapping is strictly prohibited"
    ):
        SandboxContainerPayloadValidator.validate_docker_api_payload(payload)


def test_api_payload_rejects_capability_additions() -> None:
    """Verify adding Linux capabilities (CapAdd) is strictly rejected."""
    payload = _valid_api_payload()
    payload["HostConfig"]["CapAdd"] = ["SYS_ADMIN", "NET_ADMIN"]
    with pytest.raises(
        ContainerSecurityError, match="Adding Linux capabilities is strictly prohibited"
    ):
        SandboxContainerPayloadValidator.validate_docker_api_payload(payload)


@pytest.mark.parametrize(
    "prohibited_bind",
    [
        "/var/run/docker.sock:/var/run/docker.sock",
        "/var/run/docker.sock:/var/run/docker.sock:ro",
        "/:/host_root",
        "/etc:/etc:ro",
        "/root:/root",
        "/proc:/proc",
        "/sys:/sys",
        "/boot:/boot",
        "c:/:/host_c",
        "c:\\:/host_c",
        "c:\\windows:/windows",
    ],
)
def test_api_payload_rejects_dangerous_bind_mounts(prohibited_bind: str) -> None:
    """Verify arbitrary host bind mounts and docker socket mounts are rejected."""
    payload = _valid_api_payload()
    payload["HostConfig"]["Binds"] = [prohibited_bind]
    with pytest.raises(
        ContainerSecurityError,
        match="Prohibited host filesystem|Mounting the Docker socket",
    ):
        SandboxContainerPayloadValidator.validate_docker_api_payload(payload)


def test_api_payload_rejects_mounts_array_with_docker_socket() -> None:
    """Verify Mounts structure with bind type targeting docker socket is rejected."""
    payload = _valid_api_payload()
    payload["HostConfig"]["Mounts"] = [
        {
            "Type": "bind",
            "Source": "/var/run/docker.sock",
            "Target": "/var/run/docker.sock",
        }
    ]
    with pytest.raises(ContainerSecurityError, match="Mounting the Docker socket"):
        SandboxContainerPayloadValidator.validate_docker_api_payload(payload)


def test_api_payload_requires_no_new_privileges() -> None:
    """Verify missing no-new-privileges security option is rejected."""
    payload = _valid_api_payload()
    payload["HostConfig"]["SecurityOpt"] = []
    with pytest.raises(ContainerSecurityError, match="no-new-privileges:true"):
        SandboxContainerPayloadValidator.validate_docker_api_payload(payload)


@pytest.mark.parametrize("bad_user", ["0", "root", "0:0", ""])
def test_api_payload_rejects_root_user(bad_user: str) -> None:
    """Verify running container as root (UID 0) is prohibited."""
    payload = _valid_api_payload()
    payload["User"] = bad_user
    with pytest.raises(
        ContainerSecurityError, match="Running container as root user is prohibited"
    ):
        SandboxContainerPayloadValidator.validate_docker_api_payload(payload)


# ---------------------------------------------------------------------------
# 2. Docker CLI Arguments Validation Tests
# ---------------------------------------------------------------------------


def _valid_cli_args(
    mount_spec: str = "agentforge-ws-exec-001:/sandbox/workspace:rw",
) -> list[str]:
    """Return a valid baseline CLI argument list."""
    return [
        "docker",
        "run",
        "--rm",
        "--user",
        "10001:10001",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=64m",  # noqa: S108  # nosec B108
        "--memory",
        "512m",
        "--cpus=1.0",
        "--pids-limit=64",
        "--network",
        "none",
        "-v",
        mount_spec,
        "-w",
        "/sandbox/workspace",
        "agentforge-sandbox:latest",
        "pytest",
        "-q",
    ]


def test_cli_args_accepts_compliant_arguments() -> None:
    """Verify compliant CLI arguments pass validation without error."""
    args = _valid_cli_args()
    # Should not raise
    SandboxContainerPayloadValidator.validate_docker_cli_args(args)


def test_cli_args_rejects_privileged_flag() -> None:
    """Verify --privileged flag is strictly prohibited."""
    args = _valid_cli_args()
    args.insert(2, "--privileged")
    with pytest.raises(
        ContainerSecurityError, match="Flag '--privileged' is strictly prohibited"
    ):
        SandboxContainerPayloadValidator.validate_docker_cli_args(args)


@pytest.mark.parametrize(
    "flag",
    [
        "--pid=host",
        "--ipc=host",
        "--net=host",
        "--network=host",
    ],
)
def test_cli_args_rejects_host_namespaces(flag: str) -> None:
    """Verify host namespace flags are rejected."""
    args = _valid_cli_args()
    args.insert(2, flag)
    with pytest.raises(ContainerSecurityError, match="Host namespace sharing"):
        SandboxContainerPayloadValidator.validate_docker_cli_args(args)


def test_cli_args_rejects_device_flag() -> None:
    """Verify --device flag is strictly prohibited."""
    args = _valid_cli_args()
    args.extend(["--device", "/dev/sda"])
    with pytest.raises(
        ContainerSecurityError, match="Flag '--device' is strictly prohibited"
    ):
        SandboxContainerPayloadValidator.validate_docker_cli_args(args)


def test_cli_args_rejects_cap_add_flag() -> None:
    """Verify --cap-add flag is strictly prohibited."""
    args = _valid_cli_args()
    args.extend(["--cap-add", "SYS_ADMIN"])
    with pytest.raises(
        ContainerSecurityError, match="Flag '--cap-add' is strictly prohibited"
    ):
        SandboxContainerPayloadValidator.validate_docker_cli_args(args)


@pytest.mark.parametrize(
    "bad_volume",
    [
        "/var/run/docker.sock:/var/run/docker.sock",
        "/var/run/docker.sock:/var/run/docker.sock:ro",
        "/:/host_root",
        "/etc:/etc",
        "/proc:/proc",
        "/sys:/sys",
        "/root:/root",
        "c:/:/host_c",
    ],
)
def test_cli_args_rejects_dangerous_volume_mounts(bad_volume: str) -> None:
    """Verify dangerous volume flags are rejected in CLI arguments."""
    args = _valid_cli_args(mount_spec=bad_volume)
    with pytest.raises(
        ContainerSecurityError,
        match="Prohibited host filesystem|Mounting the Docker socket",
    ):
        SandboxContainerPayloadValidator.validate_docker_cli_args(args)


def test_cli_args_rejects_missing_cap_drop() -> None:
    """Verify absence of --cap-drop ALL is rejected."""
    args = _valid_cli_args()
    args.remove("--cap-drop")
    args.remove("ALL")
    with pytest.raises(
        ContainerSecurityError, match="Mandatory capability drop '--cap-drop ALL'"
    ):
        SandboxContainerPayloadValidator.validate_docker_cli_args(args)


def test_cli_args_rejects_missing_read_only() -> None:
    """Verify absence of --read-only is rejected."""
    args = _valid_cli_args()
    args.remove("--read-only")
    with pytest.raises(ContainerSecurityError, match="Mandatory flag '--read-only'"):
        SandboxContainerPayloadValidator.validate_docker_cli_args(args)


def test_cli_args_rejects_missing_user() -> None:
    """Verify absence of --user flag is rejected."""
    args = _valid_cli_args()
    args.remove("--user")
    args.remove("10001:10001")
    with pytest.raises(
        ContainerSecurityError, match="Mandatory flag '--user 10001:10001'"
    ):
        SandboxContainerPayloadValidator.validate_docker_cli_args(args)


def test_cli_args_rejects_unapproved_image() -> None:
    """Verify CLI arguments with unapproved image are rejected."""
    args = _valid_cli_args()
    # Replace image
    idx = args.index("agentforge-sandbox:latest")
    args[idx] = "unauthorized-image:latest"
    with pytest.raises(ContainerSecurityError, match="not in permitted sandbox images"):
        SandboxContainerPayloadValidator.validate_docker_cli_args(
            args, allowed_images=DEFAULT_ALLOWED_IMAGES
        )


# ---------------------------------------------------------------------------
# 3. CLIDockerClient & DockerRunner Integration Isolation Tests
# ---------------------------------------------------------------------------


def test_cli_docker_client_builds_compliant_command(tmp_path: Path) -> None:
    """Verify CLIDockerClient._build_docker_args builds a compliant argument list."""
    client = CLIDockerClient(docker_binary="docker")
    ws_dir = tmp_path / "ws_sub"
    ws_dir.mkdir()
    policy = ExecutionPolicy(memory_limit="256m", cpu_limit=1.0, network_enabled=False)

    args = client._build_docker_args(
        image="agentforge-sandbox:0.11.0",
        command=["pytest", "-q"],
        workspace_path=ws_dir,
        policy=policy,
        environment={"TEST_VAR": "val"},
    )

    assert "--security-opt" in args
    assert "no-new-privileges:true" in args
    assert "--cap-drop" in args
    assert "ALL" in args
    assert "--read-only" in args
    assert "--user" in args
    assert "10001:10001" in args
    assert "-e" in args
    assert "TEST_VAR=val" in args


def test_cli_docker_client_uses_named_volume_when_configured(tmp_path: Path) -> None:
    """Verify named volume configuration generates isolated per-execution volume."""
    client = CLIDockerClient(docker_binary="docker")
    ws_dir = tmp_path / "ws_sub"
    ws_dir.mkdir()
    policy = ExecutionPolicy()

    with patch(
        "backend.app.execution.docker_runner.settings.DOCKER_NAMED_VOLUME",
        "agentforge-ws",
    ):
        args = client._build_docker_args(
            image="agentforge-sandbox:0.11.0",
            command=["pytest", "-q"],
            workspace_path=ws_dir,
            policy=policy,
            environment=None,
        )

    assert "-v" in args
    vol_idx = args.index("-v") + 1
    mount_str = args[vol_idx]
    assert mount_str.startswith("agentforge-ws-")
    assert mount_str.endswith(":/sandbox/workspace:rw")


def test_docker_runner_rejects_when_daemon_unavailable(tmp_path: Path) -> None:
    """Verify fail-closed behavior when Docker daemon is not available."""
    mock_client = MockDockerClient(available=False)
    runner = DockerRunner(docker_client=mock_client)
    ws_dir = tmp_path / "ws_test"
    ws_dir.mkdir()

    result = runner.execute(
        workspace_path=ws_dir,
        command=["pytest", "-q"],
    )

    assert result.status == ExecutionStatus.FAILED
    assert result.exit_code == 127
    assert any(
        "Host execution fallback is strictly prohibited" in err for err in result.errors
    )


def test_docker_runner_rejects_traversal_workspace(tmp_path: Path) -> None:
    """Verify DockerRunner returns REJECTED status on path traversal workspace."""
    mock_client = MockDockerClient(available=True)
    runner = DockerRunner(docker_client=mock_client)

    result = runner.execute(
        workspace_path=tmp_path / ".." / "escaped",
        command=["pytest", "-q"],
    )

    assert result.status == ExecutionStatus.REJECTED
    assert result.exit_code == 126
    assert any("security gatekeeper" in err for err in result.errors)


# ---------------------------------------------------------------------------
# 4. Configuration Validation Tests
# ---------------------------------------------------------------------------


def test_production_config_rejects_disabled_gatekeeper() -> None:
    """Verify that in production, disabling the container gatekeeper is blocked."""
    cfg = Settings(
        ENVIRONMENT="production",
        API_KEY_AUTH_ENABLED=True,
        API_KEY="test-prod-key-12345",  # noqa: S106
        CORS_ORIGINS=["https://app.agentforge.dev"],
        DOCKER_CONTAINER_GATEKEEPER_ENFORCED=False,
    )
    errors = cfg.validate_configuration()
    assert any(
        "DOCKER_CONTAINER_GATEKEEPER_ENFORCED cannot be disabled in production" in err
        for err in errors
    )


# ---------------------------------------------------------------------------
# 5. Exact Image Matching & Hardened Gatekeeper Validation Tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prefix_bypass_image",
    [
        "agentforge-sandbox:malicious",
        "agentforge-sandbox-evil:latest",
        "agentforge-sandbox:latest-fake",
        "agentforge-sandbox:0.11.0-compromised",
    ],
)
def test_gatekeeper_rejects_prefix_image_bypass(prefix_bypass_image: str) -> None:
    """Verify exact normalized image matching rejects prefix spoofing."""
    payload = _valid_api_payload()
    payload["Image"] = prefix_bypass_image
    with pytest.raises(ContainerSecurityError, match="not in permitted sandbox images"):
        SandboxContainerPayloadValidator.validate_docker_api_payload(payload)


def test_gatekeeper_rejects_missing_cap_drop_in_payload() -> None:
    """Verify missing CapDrop: ['ALL'] is rejected."""
    payload = _valid_api_payload()
    payload["HostConfig"]["CapDrop"] = ["MKNOD"]
    with pytest.raises(ContainerSecurityError, match="Mandatory capability drop 'ALL'"):
        SandboxContainerPayloadValidator.validate_docker_api_payload(payload)


def test_gatekeeper_rejects_missing_readonly_rootfs_in_payload() -> None:
    """Verify missing ReadonlyRootfs: true is rejected."""
    payload = _valid_api_payload()
    payload["HostConfig"]["ReadonlyRootfs"] = False
    with pytest.raises(ContainerSecurityError, match="Mandatory read-only root"):
        SandboxContainerPayloadValidator.validate_docker_api_payload(payload)


@pytest.mark.parametrize(
    ("limit_key", "invalid_val", "match_str"),
    [
        ("Memory", 0, "Mandatory memory resource limit"),
        ("NanoCpus", 0, "Mandatory CPU resource limit"),
        ("PidsLimit", 0, "Mandatory process limit"),
    ],
)
def test_gatekeeper_rejects_missing_resource_limits(
    limit_key: str, invalid_val: int, match_str: str
) -> None:
    """Verify missing or zero resource limits are rejected."""
    payload = _valid_api_payload()
    payload["HostConfig"][limit_key] = invalid_val
    if limit_key == "NanoCpus":
        payload["HostConfig"]["CpuQuota"] = 0
    with pytest.raises(ContainerSecurityError, match=match_str):
        SandboxContainerPayloadValidator.validate_docker_api_payload(payload)


@pytest.mark.parametrize(
    "prohibited_env",
    [
        "DOCKER_HOST=tcp://evil:2375",
        "DOCKER_AUTH=secret",
        "LD_PRELOAD=/evil.so",
        "LD_LIBRARY_PATH=/evil/lib",
        "SUDO_ASKPASS=/bin/sh",
        "API_KEY=leak",
        "GITHUB_TOKEN=leak",
    ],
)
def test_gatekeeper_rejects_prohibited_env_vars(prohibited_env: str) -> None:
    """Verify dangerous environment variables are rejected."""
    payload = _valid_api_payload()
    payload["Env"] = [prohibited_env]
    with pytest.raises(ContainerSecurityError, match="is prohibited in sandbox"):
        SandboxContainerPayloadValidator.validate_docker_api_payload(payload)


def test_gatekeeper_rejects_restart_policy() -> None:
    """Verify container restart policies are prohibited."""
    payload = _valid_api_payload()
    payload["HostConfig"]["RestartPolicy"] = {"Name": "always"}
    with pytest.raises(
        ContainerSecurityError, match="Restart policy 'always' is prohibited"
    ):
        SandboxContainerPayloadValidator.validate_docker_api_payload(payload)


def test_gatekeeper_rejects_volumes_from() -> None:
    """Verify VolumesFrom is strictly prohibited."""
    payload = _valid_api_payload()
    payload["HostConfig"]["VolumesFrom"] = ["other-container"]
    with pytest.raises(
        ContainerSecurityError, match="VolumesFrom is strictly prohibited"
    ):
        SandboxContainerPayloadValidator.validate_docker_api_payload(payload)


def test_gatekeeper_rejects_shared_root_volume_directly() -> None:
    """Verify mounting the shared agentforge-workspaces volume is prohibited."""
    payload = _valid_api_payload()
    payload["HostConfig"]["Binds"] = ["agentforge-workspaces:/sandbox/workspace:rw"]
    with pytest.raises(
        ContainerSecurityError,
        match=(
            "Mounting the shared 'agentforge-workspaces' volume directly is prohibited"
        ),
    ):
        SandboxContainerPayloadValidator.validate_docker_api_payload(payload)


# ---------------------------------------------------------------------------
# 6. Dedicated Execution Broker & Broker Client Tests
# ---------------------------------------------------------------------------


def test_execution_broker_health() -> None:
    """Verify SandboxExecutionBroker health reflects underlying Docker daemon."""
    mock_cli = MockDockerClient(available=True)
    broker = SandboxExecutionBroker(docker_client=mock_cli)  # type: ignore[arg-type]
    assert broker.is_healthy() is True

    mock_cli_down = MockDockerClient(available=False)
    broker_down = SandboxExecutionBroker(docker_client=mock_cli_down)  # type: ignore[arg-type]
    assert broker_down.is_healthy() is False


def test_execution_broker_fails_closed_when_docker_unavailable() -> None:
    """Verify broker fails closed without host fallback when Docker is down."""
    mock_cli = MockDockerClient(available=False)
    broker = SandboxExecutionBroker(docker_client=mock_cli)  # type: ignore[arg-type]
    req = ExecutionBrokerRequest(
        command=["pytest", "-q"],
        image="agentforge-sandbox:latest",
    )
    res = broker.execute_request(req)
    assert res.status == ExecutionStatus.FAILED
    assert res.exit_code == 127
    assert any(
        "Host execution fallback is strictly prohibited" in err for err in res.errors
    )


def test_execution_broker_rejects_path_traversal_files(tmp_path: Path) -> None:
    """Verify broker rejects files attempting to escape per-execution workspace."""
    mock_cli = MockDockerClient(available=True)
    broker = SandboxExecutionBroker(
        docker_client=mock_cli,  # type: ignore[arg-type]
        workspace_base_dir=tmp_path / "broker_ws",
    )
    req = ExecutionBrokerRequest(
        command=["pytest", "-q"],
        workspace_files={"../escape.txt": "evil content"},
    )
    res = broker.execute_request(req)
    assert res.status == ExecutionStatus.REJECTED
    assert res.exit_code == 126
    assert any(
        "traversal" in err.lower() or "escapes" in err.lower() for err in res.errors
    )


def test_execution_broker_executes_and_cleans_up_workspace(tmp_path: Path) -> None:
    """Verify broker creates isolated workspace, executes, and cleans up on exit."""
    mock_cli = MockDockerClient(
        available=True,
        simulated_exit_code=0,
        simulated_stdout="3 passed in 0.05s",
    )
    ws_base = tmp_path / "broker_ws"
    broker = SandboxExecutionBroker(
        docker_client=mock_cli,  # type: ignore[arg-type]
        workspace_base_dir=ws_base,
    )
    exec_id = "test-exec-cleanup-01"
    req = ExecutionBrokerRequest(
        execution_id=exec_id,
        command=["pytest", "-q"],
        workspace_files={"test_app.py": "def test_ok(): assert True\n"},
    )
    res = broker.execute_request(req)
    assert res.status == ExecutionStatus.COMPLETED
    assert res.exit_code == 0
    assert "3 passed" in res.stdout
    # Verify per-execution workspace was thoroughly cleaned up
    exec_ws_dir = ws_base / f"ws-{exec_id}"
    assert not exec_ws_dir.exists()


def test_broker_docker_client_is_available() -> None:
    """Verify BrokerDockerClient returns False when unconfigured or unreachable."""
    client_empty = BrokerDockerClient(broker_url=None)
    assert client_empty.is_available() is False

    client_dead = BrokerDockerClient(broker_url="http://127.0.0.1:59999")
    assert client_dead.is_available() is False


def test_docker_runner_uses_broker_client_when_configured() -> None:
    """Verify DockerRunner defaults to BrokerDockerClient when configured."""
    with patch(
        "backend.app.execution.docker_runner.settings.EXECUTION_BROKER_URL",
        "http://execution-broker:8001",
    ):
        runner = DockerRunner()
        assert isinstance(runner.client, BrokerDockerClient)
        assert runner.client.broker_url == "http://execution-broker:8001"
