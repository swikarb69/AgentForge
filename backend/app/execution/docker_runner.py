"""Restricted Docker runner for sandboxed execution without host fallback."""

import asyncio
import shutil
import subprocess  # nosec B404
import time
import uuid
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from backend.app.core.config import settings
from backend.app.execution.models import (
    ExecutionPolicy,
    ExecutionResult,
    ExecutionStatus,
    ResourceUsage,
)
from backend.app.execution.policy import ExecutionPolicyEnforcer


class DockerExecutionOutput(BaseModel):
    """Raw output and telemetry from container execution."""

    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0
    timed_out: bool = False
    error: str | None = None


class DockerClientInterface(ABC):
    """Abstract interface for container execution clients."""

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if Docker is installed and daemon is responsive."""

    @abstractmethod
    def run_container(
        self,
        image: str,
        command: list[str],
        workspace_path: Path,
        policy: ExecutionPolicy,
        environment: dict[str, str] | None = None,
    ) -> DockerExecutionOutput:
        """Synchronously execute command inside isolated container."""

    @abstractmethod
    async def run_container_async(
        self,
        image: str,
        command: list[str],
        workspace_path: Path,
        policy: ExecutionPolicy,
        environment: dict[str, str] | None = None,
    ) -> DockerExecutionOutput:
        """Asynchronously execute command inside isolated container."""


class CLIDockerClient(DockerClientInterface):
    """Executes container commands via the docker CLI binary."""

    def __init__(self, docker_binary: str | None = None) -> None:
        self.docker_binary = docker_binary or shutil.which("docker")

    def is_available(self) -> bool:
        """Check if docker binary exists and daemon responds to ping."""
        if not self.docker_binary:
            return False
        try:
            res = subprocess.run(  # noqa: S603  # nosec B603
                [self.docker_binary, "info"],
                capture_output=True,
                timeout=5,
                check=False,
            )
            return res.returncode == 0
        except (subprocess.SubprocessError, OSError):
            return False

    def _build_docker_args(
        self,
        image: str,
        command: list[str],
        workspace_path: Path,
        policy: ExecutionPolicy,
        environment: dict[str, str] | None,
    ) -> list[str]:
        """Construct isolated, restricted docker run command arguments."""
        args: list[str] = [
            self.docker_binary or "docker",
            "run",
            "--rm",
            "--user",
            "10001:10001",
            "--cap-drop",
            "ALL",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",  # noqa: S108  # nosec B108
            "--memory",
            policy.memory_limit,
            f"--cpus={policy.cpu_limit}",
            f"--pids-limit={policy.process_limit}",
            "--network",
            "none" if not policy.network_enabled else "bridge",
            "-v",
            f"{workspace_path.resolve()}:/sandbox/workspace:rw",
            "-w",
            "/sandbox/workspace",
        ]
        if environment:
            for key, val in environment.items():
                args.extend(["-e", f"{key}={val}"])

        args.append(image)
        args.extend(command)
        return args

    def run_container(
        self,
        image: str,
        command: list[str],
        workspace_path: Path,
        policy: ExecutionPolicy,
        environment: dict[str, str] | None = None,
    ) -> DockerExecutionOutput:
        """Execute container synchronously using subprocess."""
        args = self._build_docker_args(
            image, command, workspace_path, policy, environment
        )
        start_time = time.monotonic()
        try:
            proc = subprocess.run(  # noqa: S603  # nosec B603
                args,
                capture_output=True,
                text=True,
                timeout=policy.timeout_seconds,
                check=False,
            )
            duration = time.monotonic() - start_time
            return DockerExecutionOutput(
                exit_code=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
                duration_seconds=duration,
                timed_out=False,
            )
        except subprocess.TimeoutExpired as exc:
            duration = time.monotonic() - start_time
            stdout = (
                exc.stdout.decode()
                if isinstance(exc.stdout, bytes)
                else (exc.stdout or "")
            )
            stderr = (
                exc.stderr.decode()
                if isinstance(exc.stderr, bytes)
                else (exc.stderr or "")
            )
            return DockerExecutionOutput(
                exit_code=124,
                stdout=stdout,
                stderr=stderr,
                duration_seconds=duration,
                timed_out=True,
                error=f"Container execution timed out after {policy.timeout_seconds}s",
            )
        except Exception as exc:
            duration = time.monotonic() - start_time
            return DockerExecutionOutput(
                exit_code=1,
                duration_seconds=duration,
                error=str(exc),
            )

    async def run_container_async(
        self,
        image: str,
        command: list[str],
        workspace_path: Path,
        policy: ExecutionPolicy,
        environment: dict[str, str] | None = None,
    ) -> DockerExecutionOutput:
        """Execute container asynchronously using asyncio subprocess."""
        args = self._build_docker_args(
            image, command, workspace_path, policy, environment
        )
        start_time = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                args[0],
                *args[1:],
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=float(policy.timeout_seconds),
            )
            duration = time.monotonic() - start_time
            return DockerExecutionOutput(
                exit_code=proc.returncode if proc.returncode is not None else 1,
                stdout=stdout_bytes.decode(errors="replace"),
                stderr=stderr_bytes.decode(errors="replace"),
                duration_seconds=duration,
                timed_out=False,
            )
        except TimeoutError:
            duration = time.monotonic() - start_time
            return DockerExecutionOutput(
                exit_code=124,
                duration_seconds=duration,
                timed_out=True,
                error=f"Container execution timed out after {policy.timeout_seconds}s",
            )
        except Exception as exc:
            duration = time.monotonic() - start_time
            return DockerExecutionOutput(
                exit_code=1,
                duration_seconds=duration,
                error=str(exc),
            )


class MockDockerClient(DockerClientInterface):
    """Configurable mock Docker client for deterministic testing."""

    def __init__(
        self,
        available: bool = True,
        simulated_exit_code: int = 0,
        simulated_stdout: str = "1 passed in 0.05s\n",
        simulated_stderr: str = "",
        simulated_timed_out: bool = False,
        simulated_duration: float = 0.5,
    ) -> None:
        self.available = available
        self.simulated_exit_code = simulated_exit_code
        self.simulated_stdout = simulated_stdout
        self.simulated_stderr = simulated_stderr
        self.simulated_timed_out = simulated_timed_out
        self.simulated_duration = simulated_duration
        self.history: list[dict[str, Any]] = []

    def is_available(self) -> bool:
        return self.available

    def run_container(
        self,
        image: str,
        command: list[str],
        workspace_path: Path,
        policy: ExecutionPolicy,
        environment: dict[str, str] | None = None,
    ) -> DockerExecutionOutput:
        self.history.append(
            {
                "image": image,
                "command": command,
                "workspace_path": str(workspace_path),
                "policy": policy.model_dump(),
                "environment": environment,
            }
        )
        if self.simulated_timed_out:
            return DockerExecutionOutput(
                exit_code=124,
                stdout=self.simulated_stdout,
                stderr=self.simulated_stderr,
                duration_seconds=float(policy.timeout_seconds),
                timed_out=True,
                error=f"Container execution timed out after {policy.timeout_seconds}s",
            )
        return DockerExecutionOutput(
            exit_code=self.simulated_exit_code,
            stdout=self.simulated_stdout,
            stderr=self.simulated_stderr,
            duration_seconds=self.simulated_duration,
            timed_out=False,
        )

    async def run_container_async(
        self,
        image: str,
        command: list[str],
        workspace_path: Path,
        policy: ExecutionPolicy,
        environment: dict[str, str] | None = None,
    ) -> DockerExecutionOutput:
        return self.run_container(image, command, workspace_path, policy, environment)


class DockerRunner:
    """Restricted Docker runner enforcing strict sandboxing and no host fallback."""

    def __init__(
        self,
        docker_client: DockerClientInterface | None = None,
        image: str | None = None,
    ) -> None:
        self.client = docker_client if docker_client is not None else CLIDockerClient()
        self.image = image or settings.DOCKER_IMAGE

    def execute(
        self,
        workspace_path: str | Path,
        command: list[str],
        policy: ExecutionPolicy | None = None,
        environment: dict[str, str] | None = None,
        execution_id: str | None = None,
    ) -> ExecutionResult:
        """Synchronously execute a command inside a sandboxed container."""
        active_policy = policy or ExecutionPolicy()
        exec_id = execution_id or f"exec-{uuid.uuid4().hex[:12]}"
        ws_dir = Path(workspace_path).resolve()

        # Policy validation
        try:
            ExecutionPolicyEnforcer.validate_command(command, active_policy)
        except ValueError as val_err:
            return ExecutionResult(
                execution_id=exec_id,
                status=ExecutionStatus.REJECTED,
                exit_code=126,
                errors=[f"Command rejected by security policy: {val_err}"],
            )

        sanitized_env = ExecutionPolicyEnforcer.sanitize_environment(
            environment, active_policy
        )

        # STRICT REQUIREMENT: No host execution fallback!
        if not self.client.is_available():
            return ExecutionResult(
                execution_id=exec_id,
                status=ExecutionStatus.FAILED,
                exit_code=127,
                started_at=datetime.now(UTC).isoformat(),
                completed_at=datetime.now(UTC).isoformat(),
                duration_seconds=0.0,
                stderr="Docker daemon is not available or running.",
                errors=[
                    "Docker daemon is not available or running. "
                    "Host execution fallback is strictly prohibited."
                ],
            )

        started_at = datetime.now(UTC).isoformat()
        output = self.client.run_container(
            image=self.image,
            command=command,
            workspace_path=ws_dir,
            policy=active_policy,
            environment=sanitized_env,
        )
        completed_at = datetime.now(UTC).isoformat()

        return self._format_result(
            exec_id=exec_id,
            output=output,
            policy=active_policy,
            started_at=started_at,
            completed_at=completed_at,
        )

    async def execute_async(
        self,
        workspace_path: str | Path,
        command: list[str],
        policy: ExecutionPolicy | None = None,
        environment: dict[str, str] | None = None,
        execution_id: str | None = None,
    ) -> ExecutionResult:
        """Asynchronously execute a command inside a sandboxed container."""
        active_policy = policy or ExecutionPolicy()
        exec_id = execution_id or f"exec-{uuid.uuid4().hex[:12]}"
        ws_dir = Path(workspace_path).resolve()

        try:
            ExecutionPolicyEnforcer.validate_command(command, active_policy)
        except ValueError as val_err:
            return ExecutionResult(
                execution_id=exec_id,
                status=ExecutionStatus.REJECTED,
                exit_code=126,
                errors=[f"Command rejected by security policy: {val_err}"],
            )

        sanitized_env = ExecutionPolicyEnforcer.sanitize_environment(
            environment, active_policy
        )

        # STRICT REQUIREMENT: No host execution fallback!
        if not self.client.is_available():
            return ExecutionResult(
                execution_id=exec_id,
                status=ExecutionStatus.FAILED,
                exit_code=127,
                started_at=datetime.now(UTC).isoformat(),
                completed_at=datetime.now(UTC).isoformat(),
                duration_seconds=0.0,
                stderr="Docker daemon is not available or running.",
                errors=[
                    "Docker daemon is not available or running. "
                    "Host execution fallback is strictly prohibited."
                ],
            )

        started_at = datetime.now(UTC).isoformat()
        output = await self.client.run_container_async(
            image=self.image,
            command=command,
            workspace_path=ws_dir,
            policy=active_policy,
            environment=sanitized_env,
        )
        completed_at = datetime.now(UTC).isoformat()

        return self._format_result(
            exec_id=exec_id,
            output=output,
            policy=active_policy,
            started_at=started_at,
            completed_at=completed_at,
        )

    def _format_result(
        self,
        exec_id: str,
        output: DockerExecutionOutput,
        policy: ExecutionPolicy,
        started_at: str,
        completed_at: str,
    ) -> ExecutionResult:
        """Format DockerExecutionOutput into standard ExecutionResult."""
        stdout = output.stdout
        stderr = output.stderr
        warnings: list[str] = []
        errors: list[str] = []

        if len(stdout.encode()) > policy.maximum_output_bytes:
            stdout = stdout.encode()[: policy.maximum_output_bytes].decode(
                errors="replace"
            )
            warnings.append("Standard output was truncated due to policy size limit.")

        if len(stderr.encode()) > policy.maximum_output_bytes:
            stderr = stderr.encode()[: policy.maximum_output_bytes].decode(
                errors="replace"
            )
            warnings.append("Standard error was truncated due to policy size limit.")

        if output.timed_out:
            status = ExecutionStatus.TIMED_OUT
            errors.append(f"Execution timed out after {policy.timeout_seconds} seconds")
        elif output.exit_code == 0:
            status = ExecutionStatus.COMPLETED
        else:
            status = ExecutionStatus.FAILED
            if output.error:
                errors.append(output.error)

        return ExecutionResult(
            execution_id=exec_id,
            status=status,
            exit_code=output.exit_code,
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=round(output.duration_seconds, 3),
            stdout=stdout,
            stderr=stderr,
            resource_usage=ResourceUsage(
                duration_seconds=round(output.duration_seconds, 3),
            ),
            errors=errors,
            warnings=warnings,
        )
