"""Adversarial regression test suite for Docker API and container runtime isolation."""

from pathlib import Path
from unittest.mock import patch

import pytest

from backend.app.core.config import Settings
from backend.app.execution.docker_runner import (
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
            "Binds": [],
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
    mount_spec: str = "agentforge-workspaces:/sandbox/workspace:rw",
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
    """Verify named volume configuration replaces host bind mount."""
    client = CLIDockerClient(docker_binary="docker")
    ws_dir = tmp_path / "ws_sub"
    ws_dir.mkdir()
    policy = ExecutionPolicy()

    with patch(
        "backend.app.execution.docker_runner.settings.DOCKER_NAMED_VOLUME",
        "agentforge-workspaces",
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
    assert args[vol_idx] == "agentforge-workspaces:/sandbox/workspace:rw"


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
