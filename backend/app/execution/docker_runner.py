"""Restricted Docker runner for sandboxed execution without host fallback."""

import asyncio
import logging
import os
import shutil
import subprocess  # nosec B404
import time
import uuid
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel

from backend.app.core.config import settings
from backend.app.execution.exceptions import (
    ContainerSecurityError,
    WorkspaceSecurityError,
)
from backend.app.execution.gatekeeper import (
    PROHIBITED_ENV_VARS,
    SandboxContainerPayloadValidator,
)
from backend.app.execution.models import (
    ExecutionPolicy,
    ExecutionResult,
    ExecutionStatus,
    ResourceUsage,
)
from backend.app.execution.policy import ExecutionPolicyEnforcer

logger = logging.getLogger(__name__)


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

    def __init__(
        self,
        docker_binary: str | None = None,
        docker_host: str | None = None,
    ) -> None:
        self.docker_binary = docker_binary or shutil.which("docker")
        self.docker_host = (
            docker_host
            or getattr(settings, "DOCKER_HOST", None)
            or os.environ.get("DOCKER_HOST")
        )

    def is_available(self) -> bool:
        """Check if docker binary exists and daemon responds to ping."""
        if not self.docker_binary:
            logger.error(
                "CLIDockerClient: docker binary not found in PATH: %s",
                os.environ.get("PATH"),
            )
            return False
        cmd = [self.docker_binary]
        if self.docker_host:
            cmd.extend(["-H", self.docker_host])

        # Probe version first (minimal permission requirement: VERSION=1),
        # falling back to info (INFO=1).
        for probe in ["version", "info"]:
            try:
                res = subprocess.run(  # noqa: S603  # nosec B603
                    cmd + [probe],
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
                if res.returncode == 0:
                    return True
                logger.error(
                    "Docker availability probe '%s' failed (cmd=%s, exit %d): "
                    "stderr=%r | stdout=%r",
                    probe,
                    cmd + [probe],
                    res.returncode,
                    res.stderr.decode(errors="replace").strip(),
                    res.stdout.decode(errors="replace").strip(),
                )
            except (subprocess.SubprocessError, OSError) as exc:
                logger.error(
                    "Docker availability probe '%s' error (cmd=%s): %s",
                    probe,
                    cmd + [probe],
                    exc,
                )
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
        named_volume = getattr(settings, "DOCKER_NAMED_VOLUME", None)
        per_exec_vol = getattr(settings, "DOCKER_PER_EXECUTION_VOLUMES", True)
        if named_volume:
            if per_exec_vol and not str(named_volume).startswith("agentforge-ws-"):
                exec_vol = f"agentforge-ws-{uuid.uuid4().hex[:12]}"
                mount_spec = f"{exec_vol}:/sandbox/workspace:rw"
            else:
                mount_spec = f"{named_volume}:/sandbox/workspace:rw"
        else:
            mount_spec = f"{workspace_path.resolve()}:/sandbox/workspace:rw"

        args: list[str] = [self.docker_binary or "docker"]
        if self.docker_host:
            args.extend(["-H", self.docker_host])
        args.extend(
            [
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
                policy.memory_limit,
                f"--cpus={policy.cpu_limit}",
                f"--pids-limit={policy.process_limit}",
                "--network",
                "none" if not policy.network_enabled else "bridge",
                "-v",
                mount_spec,
                "-w",
                "/sandbox/workspace",
            ]
        )
        if environment:
            for key, val in environment.items():
                args.extend(["-e", f"{key}={val}"])

        args.append(image)
        args.extend(command)

        if getattr(settings, "DOCKER_CONTAINER_GATEKEEPER_ENFORCED", True):
            SandboxContainerPayloadValidator.validate_docker_cli_args(
                args,
                allowed_images=getattr(settings, "SANDBOX_ALLOWED_IMAGES", None),
            )

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
        vol_to_clean: str | None = None
        for i, a in enumerate(args):
            if a in ("-v", "--volume") and i + 1 < len(args):
                spec = args[i + 1].split(":")[0]
                if spec.startswith("agentforge-ws-"):
                    vol_to_clean = spec
                break

        container_name = (
            f"agentforge-sbx-{vol_to_clean.replace('agentforge-ws-', '')}"
            if vol_to_clean
            else None
        )
        start_time = time.monotonic()
        try:
            if vol_to_clean and container_name and self.docker_binary:
                # 1. Create isolated named volume
                v_cmd = [self.docker_binary]
                if self.docker_host:
                    v_cmd.extend(["-H", self.docker_host])
                v_cmd.extend(["volume", "create", vol_to_clean])
                subprocess.run(  # noqa: S603  # nosec B603
                    v_cmd, capture_output=True, timeout=5, check=False
                )

                # 2. Create container (not yet started)
                create_args = list(args)
                run_idx = create_args.index("run")
                create_args[run_idx] = "create"
                if "--rm" in create_args:
                    create_args.remove("--rm")
                create_args.insert(run_idx + 1, "--name")
                create_args.insert(run_idx + 2, container_name)

                c_proc = subprocess.run(  # noqa: S603  # nosec B603
                    create_args,
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                if c_proc.returncode != 0:
                    duration = time.monotonic() - start_time
                    return DockerExecutionOutput(
                        exit_code=1,
                        duration_seconds=duration,
                        error=f"Container creation failed: {c_proc.stderr}",
                    )

                # 3. Populate container volume from workspace_path
                if workspace_path.exists() and workspace_path.is_dir():
                    try:
                        has_entries = any(workspace_path.iterdir())
                    except OSError:
                        has_entries = False
                    if has_entries:
                        cp_cmd = [self.docker_binary]
                        if self.docker_host:
                            cp_cmd.extend(["-H", self.docker_host])
                        src_spec = str(workspace_path).rstrip("/\\") + "/."
                        cp_cmd.extend(
                            ["cp", src_spec, f"{container_name}:/sandbox/workspace/"]
                        )
                        cp_proc = subprocess.run(  # noqa: S603  # nosec B603
                            cp_cmd,
                            capture_output=True,
                            text=True,
                            timeout=15,
                            check=False,
                        )
                        if cp_proc.returncode != 0:
                            duration = time.monotonic() - start_time
                            return DockerExecutionOutput(
                                exit_code=1,
                                duration_seconds=duration,
                                error=f"Workspace transfer failed: {cp_proc.stderr}",
                            )

                # 4. Start container and attach
                start_cmd = [self.docker_binary]
                if self.docker_host:
                    start_cmd.extend(["-H", self.docker_host])
                start_cmd.extend(["start", "-a", container_name])
                proc = subprocess.run(  # noqa: S603  # nosec B603
                    start_cmd,
                    capture_output=True,
                    text=True,
                    timeout=policy.timeout_seconds,
                    check=False,
                )
            else:
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
        finally:
            if self.docker_binary:
                if container_name:
                    rm_c = [self.docker_binary]
                    if self.docker_host:
                        rm_c.extend(["-H", self.docker_host])
                    rm_c.extend(["rm", "-f", container_name])
                    try:
                        subprocess.run(  # noqa: S603  # nosec B603
                            rm_c, capture_output=True, timeout=5, check=False
                        )
                    except Exception:  # noqa: S110  # nosec B110
                        pass
                if vol_to_clean:
                    rm_v = [self.docker_binary]
                    if self.docker_host:
                        rm_v.extend(["-H", self.docker_host])
                    rm_v.extend(["volume", "rm", "-f", vol_to_clean])
                    try:
                        subprocess.run(  # noqa: S603  # nosec B603
                            rm_v, capture_output=True, timeout=5, check=False
                        )
                    except Exception:  # noqa: S110  # nosec B110
                        pass

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
        vol_to_clean: str | None = None
        for i, a in enumerate(args):
            if a in ("-v", "--volume") and i + 1 < len(args):
                spec = args[i + 1].split(":")[0]
                if spec.startswith("agentforge-ws-"):
                    vol_to_clean = spec
                break

        container_name = (
            f"agentforge-sbx-{vol_to_clean.replace('agentforge-ws-', '')}"
            if vol_to_clean
            else None
        )
        start_time = time.monotonic()
        try:
            if vol_to_clean and container_name and self.docker_binary:
                # 1. Create isolated named volume
                v_cmd = [self.docker_binary]
                if self.docker_host:
                    v_cmd.extend(["-H", self.docker_host])
                v_cmd.extend(["volume", "create", vol_to_clean])
                proc_v = await asyncio.create_subprocess_exec(
                    v_cmd[0],
                    *v_cmd[1:],
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.wait_for(proc_v.wait(), timeout=5.0)

                # 2. Create container
                create_args = list(args)
                run_idx = create_args.index("run")
                create_args[run_idx] = "create"
                if "--rm" in create_args:
                    create_args.remove("--rm")
                create_args.insert(run_idx + 1, "--name")
                create_args.insert(run_idx + 2, container_name)

                proc_c = await asyncio.create_subprocess_exec(
                    create_args[0],
                    *create_args[1:],
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout_c, stderr_c = await asyncio.wait_for(
                    proc_c.communicate(), timeout=10.0
                )
                if proc_c.returncode != 0:
                    duration = time.monotonic() - start_time
                    err_c = stderr_c.decode(errors="replace")
                    return DockerExecutionOutput(
                        exit_code=1,
                        duration_seconds=duration,
                        error=f"Container creation failed: {err_c}",
                    )

                # 3. Populate container volume from workspace_path
                if workspace_path.exists() and workspace_path.is_dir():
                    try:
                        has_entries = any(workspace_path.iterdir())
                    except OSError:
                        has_entries = False
                    if has_entries:
                        cp_cmd = [self.docker_binary]
                        if self.docker_host:
                            cp_cmd.extend(["-H", self.docker_host])
                        src_spec = str(workspace_path).rstrip("/\\") + "/."
                        cp_cmd.extend(
                            ["cp", src_spec, f"{container_name}:/sandbox/workspace/"]
                        )
                        proc_cp = await asyncio.create_subprocess_exec(
                            cp_cmd[0],
                            *cp_cmd[1:],
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                        )
                        stdout_cp, stderr_cp = await asyncio.wait_for(
                            proc_cp.communicate(), timeout=15.0
                        )
                        if proc_cp.returncode != 0:
                            duration = time.monotonic() - start_time
                            err_cp = stderr_cp.decode(errors="replace")
                            return DockerExecutionOutput(
                                exit_code=1,
                                duration_seconds=duration,
                                error=f"Workspace transfer failed: {err_cp}",
                            )

                # 4. Start container and attach
                start_cmd = [self.docker_binary]
                if self.docker_host:
                    start_cmd.extend(["-H", self.docker_host])
                start_cmd.extend(["start", "-a", container_name])
                proc = await asyncio.create_subprocess_exec(
                    start_cmd[0],
                    *start_cmd[1:],
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=float(policy.timeout_seconds),
                )
            else:
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
        finally:
            if self.docker_binary:
                if container_name:
                    rm_c = [self.docker_binary]
                    if self.docker_host:
                        rm_c.extend(["-H", self.docker_host])
                    rm_c.extend(["rm", "-f", container_name])
                    try:
                        proc_rm = await asyncio.create_subprocess_exec(
                            rm_c[0],
                            *rm_c[1:],
                            stdout=asyncio.subprocess.DEVNULL,
                            stderr=asyncio.subprocess.DEVNULL,
                        )
                        await asyncio.wait_for(proc_rm.wait(), timeout=5.0)
                    except Exception:  # noqa: S110  # nosec B110
                        pass
                if vol_to_clean:
                    rm_v = [self.docker_binary]
                    if self.docker_host:
                        rm_v.extend(["-H", self.docker_host])
                    rm_v.extend(["volume", "rm", "-f", vol_to_clean])
                    try:
                        proc_rmv = await asyncio.create_subprocess_exec(
                            rm_v[0],
                            *rm_v[1:],
                            stdout=asyncio.subprocess.DEVNULL,
                            stderr=asyncio.subprocess.DEVNULL,
                        )
                        await asyncio.wait_for(proc_rmv.wait(), timeout=5.0)
                    except Exception:  # noqa: S110  # nosec B110
                        pass


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


class BrokerDockerClient(DockerClientInterface):
    """Executes container commands via the isolated Execution Broker HTTP service."""

    def __init__(
        self,
        broker_url: str | None = None,
        auth_token: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.broker_url = (
            broker_url
            or getattr(settings, "EXECUTION_BROKER_URL", None)
            or os.environ.get("EXECUTION_BROKER_URL")
        )
        token_setting = getattr(settings, "EXECUTION_BROKER_AUTH_TOKEN", None)
        if token_setting is not None and hasattr(token_setting, "get_secret_value"):
            tok_val = token_setting.get_secret_value()
        else:
            tok_val = str(token_setting) if token_setting else None
        self.auth_token = (
            auth_token or tok_val or os.environ.get("EXECUTION_BROKER_AUTH_TOKEN")
        )
        self.timeout = timeout

    def is_available(self) -> bool:
        """Check if execution broker is responsive and reports healthy daemon."""
        if not self.broker_url:
            return False
        try:
            url = f"{self.broker_url.rstrip('/')}/health"
            resp = httpx.get(url, timeout=3.0)
            return (
                resp.status_code == 200 and resp.json().get("docker_available") is True
            )
        except Exception:
            return False

    def _collect_workspace_files(self, workspace_path: Path) -> dict[str, str]:
        """Collect relative files from workspace directory to send to broker."""
        files: dict[str, str] = {}
        if not workspace_path.exists() or not workspace_path.is_dir():
            return files

        for p in workspace_path.rglob("*"):
            if p.is_file():
                try:
                    rel = p.relative_to(workspace_path).as_posix()
                    # Skip files larger than 1MB to prevent memory exhaustion
                    if p.stat().st_size <= 1_000_000:
                        files[rel] = p.read_text(encoding="utf-8", errors="replace")
                except Exception:  # noqa: S112  # nosec B112
                    continue
        return files

    def run_container(
        self,
        image: str,
        command: list[str],
        workspace_path: Path,
        policy: ExecutionPolicy,
        environment: dict[str, str] | None = None,
    ) -> DockerExecutionOutput:
        """Synchronously execute command via execution broker."""
        if not self.broker_url:
            return DockerExecutionOutput(
                exit_code=127,
                error="EXECUTION_BROKER_URL is not configured.",
            )

        workspace_files = self._collect_workspace_files(workspace_path)
        payload = {
            "image": image,
            "command": command,
            "workspace_files": workspace_files,
            "policy": policy.model_dump(),
            "environment": environment or {},
        }
        url = f"{self.broker_url.rstrip('/')}/v1/execute"
        headers: dict[str, str] = {}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
            headers["X-Broker-Token"] = str(self.auth_token)

        start_time = time.monotonic()
        try:
            resp = httpx.post(
                url,
                json=payload,
                headers=headers,
                timeout=float(policy.timeout_seconds + 10),
            )
            duration = time.monotonic() - start_time
            if resp.status_code == 401:
                raise ContainerSecurityError(
                    "Execution broker authentication failed: Unauthorized"
                )
            if resp.status_code == 400:
                detail = resp.json().get("detail", "Request rejected by broker")
                raise ContainerSecurityError(detail)
            resp.raise_for_status()
            data = resp.json()
            return DockerExecutionOutput(
                exit_code=data.get("exit_code", 0),
                stdout=data.get("stdout", ""),
                stderr=data.get("stderr", ""),
                duration_seconds=data.get("duration_seconds", duration),
                timed_out=data.get("timed_out", False),
                error=data.get("error"),
            )
        except ContainerSecurityError:
            raise
        except httpx.TimeoutException:
            duration = time.monotonic() - start_time
            return DockerExecutionOutput(
                exit_code=124,
                duration_seconds=duration,
                timed_out=True,
                error=(
                    f"Container execution timed out after {policy.timeout_seconds}s"
                ),
            )
        except Exception as exc:
            duration = time.monotonic() - start_time
            return DockerExecutionOutput(
                exit_code=1,
                duration_seconds=duration,
                error=f"Execution broker communication failed: {exc}",
            )

    async def run_container_async(
        self,
        image: str,
        command: list[str],
        workspace_path: Path,
        policy: ExecutionPolicy,
        environment: dict[str, str] | None = None,
    ) -> DockerExecutionOutput:
        """Asynchronously execute command via execution broker."""
        if not self.broker_url:
            return DockerExecutionOutput(
                exit_code=127,
                error="EXECUTION_BROKER_URL is not configured.",
            )

        workspace_files = self._collect_workspace_files(workspace_path)
        payload = {
            "image": image,
            "command": command,
            "workspace_files": workspace_files,
            "policy": policy.model_dump(),
            "environment": environment or {},
        }
        url = f"{self.broker_url.rstrip('/')}/v1/execute"
        headers: dict[str, str] = {}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
            headers["X-Broker-Token"] = str(self.auth_token)

        start_time = time.monotonic()
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=float(policy.timeout_seconds + 10),
                )
            duration = time.monotonic() - start_time
            if resp.status_code == 401:
                raise ContainerSecurityError(
                    "Execution broker authentication failed: Unauthorized"
                )
            if resp.status_code == 400:
                detail = resp.json().get("detail", "Request rejected by broker")
                raise ContainerSecurityError(detail)
            resp.raise_for_status()
            data = resp.json()
            return DockerExecutionOutput(
                exit_code=data.get("exit_code", 0),
                stdout=data.get("stdout", ""),
                stderr=data.get("stderr", ""),
                duration_seconds=data.get("duration_seconds", duration),
                timed_out=data.get("timed_out", False),
                error=data.get("error"),
            )
        except ContainerSecurityError:
            raise
        except httpx.TimeoutException:
            duration = time.monotonic() - start_time
            return DockerExecutionOutput(
                exit_code=124,
                duration_seconds=duration,
                timed_out=True,
                error=(
                    f"Container execution timed out after {policy.timeout_seconds}s"
                ),
            )
        except Exception as exc:
            duration = time.monotonic() - start_time
            return DockerExecutionOutput(
                exit_code=1,
                duration_seconds=duration,
                error=f"Execution broker communication failed: {exc}",
            )


class DockerRunner:
    """Restricted Docker runner enforcing strict sandboxing and no host fallback."""

    def __init__(
        self,
        docker_client: DockerClientInterface | None = None,
        image: str | None = None,
        workspace_root: str | Path | None = None,
    ) -> None:
        if docker_client is not None:
            self.client = docker_client
        elif getattr(settings, "EXECUTION_BROKER_URL", None):
            self.client = BrokerDockerClient(broker_url=settings.EXECUTION_BROKER_URL)
        else:
            self.client = CLIDockerClient()
        self.image = image or settings.DOCKER_IMAGE
        self.workspace_root = (
            Path(workspace_root) if workspace_root is not None else None
        )

    def _resolve_and_validate_workspace(self, workspace_path: str | Path) -> Path:
        """Resolve and validate workspace containment against authorized root."""
        if not getattr(settings, "DOCKER_CONTAINER_GATEKEEPER_ENFORCED", True):
            return Path(workspace_path).resolve()

        effective_root = self.workspace_root
        if effective_root is None:
            default_root = Path(settings.WORKSPACE_ROOT).resolve()
            try:
                resolved_cand = Path(workspace_path).resolve()
                if resolved_cand.is_relative_to(default_root):
                    effective_root = default_root
            except ValueError:
                pass

            if effective_root is None and not settings.is_production:
                effective_root = Path(workspace_path).resolve().parent
            elif effective_root is None:
                effective_root = default_root

        return SandboxContainerPayloadValidator.validate_workspace_path(
            workspace_path,
            workspace_root=effective_root,
        )

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

        # Workspace gatekeeper validation
        try:
            ws_dir = self._resolve_and_validate_workspace(workspace_path)
        except WorkspaceSecurityError as ws_err:
            return ExecutionResult(
                execution_id=exec_id,
                status=ExecutionStatus.REJECTED,
                exit_code=126,
                errors=[f"Workspace rejected by security gatekeeper: {ws_err}"],
            )

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

        if environment:
            for env_key in environment:
                if env_key.upper() in PROHIBITED_ENV_VARS:
                    return ExecutionResult(
                        execution_id=exec_id,
                        status=ExecutionStatus.REJECTED,
                        exit_code=126,
                        errors=[
                            "Container configuration rejected by security gatekeeper: "
                            f"Prohibited environment variable '{env_key}'"
                        ],
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
        try:
            output = self.client.run_container(
                image=self.image,
                command=command,
                workspace_path=ws_dir,
                policy=active_policy,
                environment=sanitized_env,
            )
        except ContainerSecurityError as sec_err:
            return ExecutionResult(
                execution_id=exec_id,
                status=ExecutionStatus.REJECTED,
                exit_code=126,
                errors=[
                    "Container configuration rejected by security gatekeeper: "
                    f"{sec_err}"
                ],
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

        # Workspace gatekeeper validation
        try:
            ws_dir = self._resolve_and_validate_workspace(workspace_path)
        except WorkspaceSecurityError as ws_err:
            return ExecutionResult(
                execution_id=exec_id,
                status=ExecutionStatus.REJECTED,
                exit_code=126,
                errors=[f"Workspace rejected by security gatekeeper: {ws_err}"],
            )

        try:
            ExecutionPolicyEnforcer.validate_command(command, active_policy)
        except ValueError as val_err:
            return ExecutionResult(
                execution_id=exec_id,
                status=ExecutionStatus.REJECTED,
                exit_code=126,
                errors=[f"Command rejected by security policy: {val_err}"],
            )

        if environment:
            for env_key in environment:
                if env_key.upper() in PROHIBITED_ENV_VARS:
                    return ExecutionResult(
                        execution_id=exec_id,
                        status=ExecutionStatus.REJECTED,
                        exit_code=126,
                        errors=[
                            "Container configuration rejected by security gatekeeper: "
                            f"Prohibited environment variable '{env_key}'"
                        ],
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
        try:
            output = await self.client.run_container_async(
                image=self.image,
                command=command,
                workspace_path=ws_dir,
                policy=active_policy,
                environment=sanitized_env,
            )
        except ContainerSecurityError as sec_err:
            return ExecutionResult(
                execution_id=exec_id,
                status=ExecutionStatus.REJECTED,
                exit_code=126,
                errors=[
                    "Container configuration rejected by security gatekeeper: "
                    f"{sec_err}"
                ],
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
