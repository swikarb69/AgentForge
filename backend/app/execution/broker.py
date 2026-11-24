"""Dedicated sandbox execution broker.

Enforces zero-trust container isolation and eliminates direct Docker API access
from the application container. The broker mediates all container operations,
enforces exact image allowlisting, creates ephemeral per-execution isolated
workspaces, and guarantees resource cleanup.
"""

import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field

from backend.app.execution.docker_runner import CLIDockerClient
from backend.app.execution.exceptions import (
    ContainerSecurityError,
    WorkspaceSecurityError,
)
from backend.app.execution.models import (
    ExecutionPolicy,
    ExecutionStatus,
)
from backend.app.execution.policy import ExecutionPolicyEnforcer


class ExecutionBrokerRequest(BaseModel):
    """Specification for a sandboxed execution request submitted to the broker."""

    execution_id: str = Field(default_factory=lambda: f"exec-{uuid.uuid4().hex[:12]}")
    command: list[str]
    workspace_files: dict[str, str] = Field(default_factory=dict)
    policy: ExecutionPolicy = Field(default_factory=ExecutionPolicy)
    environment: dict[str, str] | None = None
    image: str = "agentforge-sandbox:latest"


class ExecutionBrokerResponse(BaseModel):
    """Execution telemetry and outcome returned by the broker."""

    execution_id: str
    status: ExecutionStatus
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0
    timed_out: bool = False
    error: str | None = None
    errors: list[str] = Field(default_factory=list)


class SandboxExecutionBroker:
    """Brokers sandboxed execution with per-execution workspace volume isolation."""

    def __init__(
        self,
        docker_client: CLIDockerClient | None = None,
        workspace_base_dir: Path | None = None,
    ) -> None:
        self.docker_client = docker_client or CLIDockerClient()
        self.workspace_base_dir = (
            workspace_base_dir or Path(tempfile.gettempdir()) / "agentforge_broker_ws"
        )
        self.workspace_base_dir.mkdir(parents=True, exist_ok=True)

    def is_healthy(self) -> bool:
        """Check if broker can reach the underlying Docker daemon."""
        return self.docker_client.is_available()

    def execute_request(self, req: ExecutionBrokerRequest) -> ExecutionBrokerResponse:
        """Synchronously execute sandboxed request with strict isolation."""
        exec_id = req.execution_id
        active_policy = req.policy

        # 1. Fail closed if daemon is unavailable
        if not self.docker_client.is_available():
            return ExecutionBrokerResponse(
                execution_id=exec_id,
                status=ExecutionStatus.FAILED,
                exit_code=127,
                error="Docker daemon is not available on execution broker.",
                errors=[
                    "Docker daemon is unavailable or unresponsive on the "
                    "execution broker. "
                    "Host execution fallback is strictly prohibited."
                ],
            )

        # 2. Command Policy Validation
        try:
            ExecutionPolicyEnforcer.validate_command(req.command, active_policy)
        except ValueError as val_err:
            return ExecutionBrokerResponse(
                execution_id=exec_id,
                status=ExecutionStatus.REJECTED,
                exit_code=126,
                error=f"Command rejected by security policy: {val_err}",
                errors=[f"Command rejected by security policy: {val_err}"],
            )

        # 3. Environment Sanitization
        sanitized_env = ExecutionPolicyEnforcer.sanitize_environment(
            req.environment, active_policy
        )

        # 4. Create isolated per-execution workspace directory
        exec_ws_dir = self.workspace_base_dir / f"ws-{exec_id}"
        exec_ws_dir.mkdir(parents=True, exist_ok=True)

        try:
            # Populate execution files
            for rel_path_str, content in req.workspace_files.items():
                if "\x00" in rel_path_str or ".." in Path(rel_path_str).parts:
                    raise WorkspaceSecurityError(
                        f"Path traversal detected in file: '{rel_path_str}'"
                    )
                target_file = (exec_ws_dir / rel_path_str).resolve()
                if not target_file.is_relative_to(exec_ws_dir.resolve()):
                    raise WorkspaceSecurityError(
                        f"File '{rel_path_str}' escapes isolated execution workspace."
                    )
                target_file.parent.mkdir(parents=True, exist_ok=True)
                target_file.write_text(content, encoding="utf-8")

            # 5. Build and validate isolated container execution args
            output = self.docker_client.run_container(
                image=req.image,
                command=req.command,
                workspace_path=exec_ws_dir,
                policy=active_policy,
                environment=sanitized_env,
            )

            # Format response
            status_enum = (
                ExecutionStatus.TIMED_OUT
                if output.timed_out
                else (
                    ExecutionStatus.COMPLETED
                    if output.exit_code == 0
                    else ExecutionStatus.FAILED
                )
            )

            errors: list[str] = []
            if output.error:
                errors.append(output.error)

            return ExecutionBrokerResponse(
                execution_id=exec_id,
                status=status_enum,
                exit_code=output.exit_code,
                stdout=output.stdout,
                stderr=output.stderr,
                duration_seconds=output.duration_seconds,
                timed_out=output.timed_out,
                error=output.error,
                errors=errors,
            )

        except (ContainerSecurityError, WorkspaceSecurityError) as sec_err:
            return ExecutionBrokerResponse(
                execution_id=exec_id,
                status=ExecutionStatus.REJECTED,
                exit_code=126,
                error=str(sec_err),
                errors=[f"Broker security gatekeeper rejected request: {sec_err}"],
            )
        finally:
            # 6. Guaranteed Cleanup of per-execution workspace
            shutil.rmtree(exec_ws_dir, ignore_errors=True)


def create_broker_app() -> FastAPI:
    """Create FastAPI application for the isolated execution broker service."""
    app = FastAPI(
        title="AgentForge Execution Broker",
        description="Isolated container execution broker with zero-trust gatekeeper.",
        version="0.11.0",
    )
    broker = SandboxExecutionBroker()

    @app.get("/health")
    def health_check() -> dict[str, Any]:
        healthy = broker.is_healthy()
        if not healthy:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Docker daemon is not responsive on execution broker.",
            )
        return {"status": "ok", "docker_available": True}

    @app.post("/v1/execute", response_model=ExecutionBrokerResponse)
    def execute_endpoint(req: ExecutionBrokerRequest) -> ExecutionBrokerResponse:
        res = broker.execute_request(req)
        if res.status == ExecutionStatus.REJECTED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=res.error or "Request rejected by broker security gatekeeper.",
            )
        return res

    return app


broker_app = create_broker_app()
