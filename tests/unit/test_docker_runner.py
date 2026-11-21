"""Unit tests for restricted Docker execution runner."""

from pathlib import Path

import pytest

from backend.app.execution.docker_runner import (
    CLIDockerClient,
    DockerRunner,
    MockDockerClient,
)
from backend.app.execution.models import ExecutionPolicy, ExecutionStatus


def test_docker_unavailable_strictly_prohibits_host_fallback(tmp_path: Path) -> None:
    """Verify that when Docker is unavailable, host execution fallback is blocked."""
    mock_client = MockDockerClient(available=False)
    runner = DockerRunner(docker_client=mock_client)

    result = runner.execute(
        workspace_path=tmp_path,
        command=["pytest", "-q"],
    )

    assert result.status == ExecutionStatus.FAILED
    assert result.exit_code == 127
    assert any(
        "Host execution fallback is strictly prohibited" in err for err in result.errors
    )
    assert len(mock_client.history) == 0


def test_docker_execution_success(tmp_path: Path) -> None:
    """Verify successful container execution and security policy options."""
    mock_client = MockDockerClient(
        available=True,
        simulated_exit_code=0,
        simulated_stdout="3 passed in 0.15s\n",
    )
    runner = DockerRunner(docker_client=mock_client)
    policy = ExecutionPolicy(
        timeout_seconds=20,
        memory_limit="256m",
        cpu_limit=1.5,
        network_enabled=False,
    )

    result = runner.execute(
        workspace_path=tmp_path,
        command=["pytest", "-q"],
        policy=policy,
    )

    assert result.status == ExecutionStatus.COMPLETED
    assert result.exit_code == 0
    assert "3 passed" in result.stdout
    assert len(mock_client.history) == 1

    entry = mock_client.history[0]
    assert entry["command"] == ["pytest", "-q"]
    assert entry["policy"]["memory_limit"] == "256m"
    assert entry["policy"]["cpu_limit"] == 1.5
    assert not entry["policy"]["network_enabled"]


def test_docker_execution_failure(tmp_path: Path) -> None:
    """Verify failing test exit code is recorded properly."""
    mock_client = MockDockerClient(
        available=True,
        simulated_exit_code=1,
        simulated_stderr="FAILED test_app.py::test_fail\n",
    )
    runner = DockerRunner(docker_client=mock_client)

    result = runner.execute(
        workspace_path=tmp_path,
        command=["pytest", "-q"],
    )

    assert result.status == ExecutionStatus.FAILED
    assert result.exit_code == 1
    assert "FAILED" in result.stderr


def test_docker_execution_timeout(tmp_path: Path) -> None:
    """Verify container timeout maps to TIMED_OUT status."""
    mock_client = MockDockerClient(
        available=True,
        simulated_timed_out=True,
    )
    policy = ExecutionPolicy(timeout_seconds=15)
    runner = DockerRunner(docker_client=mock_client)

    result = runner.execute(
        workspace_path=tmp_path,
        command=["pytest", "-q"],
        policy=policy,
    )

    assert result.status == ExecutionStatus.TIMED_OUT
    assert result.exit_code == 124
    assert any("timed out after 15 seconds" in err for err in result.errors)


def test_docker_command_security_policy_rejection(tmp_path: Path) -> None:
    """Verify unapproved commands are rejected before invoking Docker."""
    mock_client = MockDockerClient(available=True)
    runner = DockerRunner(docker_client=mock_client)

    result = runner.execute(
        workspace_path=tmp_path,
        command=["bash", "-c", "whoami"],
    )

    assert result.status == ExecutionStatus.REJECTED
    assert result.exit_code == 126
    assert any("Command rejected by security policy" in err for err in result.errors)
    assert len(mock_client.history) == 0


def test_docker_output_truncation(tmp_path: Path) -> None:
    """Verify stdout exceeding policy maximum output is truncated with warning."""
    huge_output = "X" * 5000
    mock_client = MockDockerClient(
        available=True,
        simulated_stdout=huge_output,
    )
    policy = ExecutionPolicy(maximum_output_bytes=2048)
    runner = DockerRunner(docker_client=mock_client)

    result = runner.execute(
        workspace_path=tmp_path,
        command=["pytest", "-q"],
        policy=policy,
    )

    assert result.status == ExecutionStatus.COMPLETED
    assert len(result.stdout.encode()) == 2048
    assert any("Standard output was truncated" in w for w in result.warnings)


@pytest.mark.anyio
async def test_docker_async_execution(tmp_path: Path) -> None:
    """Verify asynchronous container execution."""
    mock_client = MockDockerClient(
        available=True,
        simulated_exit_code=0,
        simulated_stdout="Async run passed\n",
    )
    runner = DockerRunner(docker_client=mock_client)

    result = await runner.execute_async(
        workspace_path=tmp_path,
        command=["pytest", "-q"],
    )

    assert result.status == ExecutionStatus.COMPLETED
    assert result.exit_code == 0
    assert "Async run passed" in result.stdout


def test_cli_docker_client_arg_building(tmp_path: Path) -> None:
    """Verify CLI argument construction adheres to container security policy."""
    client = CLIDockerClient(docker_binary="docker")
    policy = ExecutionPolicy(
        memory_limit="512m",
        cpu_limit=1.0,
        process_limit=64,
        network_enabled=False,
    )
    env = {"PYTHONPATH": "/sandbox/workspace"}

    args = client._build_docker_args(
        image="agentforge-sandbox:latest",
        command=["pytest", "-q"],
        workspace_path=tmp_path,
        policy=policy,
        environment=env,
    )

    assert "docker" in args[0]
    assert "--user" in args
    assert "10001:10001" in args
    assert "--cap-drop" in args
    assert "ALL" in args
    assert "--read-only" in args
    assert "--network" in args
    assert "none" in args
    assert "-e" in args
    assert "PYTHONPATH=/sandbox/workspace" in args
    assert "agentforge-sandbox:latest" in args
    assert args[-2:] == ["pytest", "-q"]
