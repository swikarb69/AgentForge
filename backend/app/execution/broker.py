"""Dedicated sandbox execution broker.

Enforces zero-trust container isolation and eliminates direct Docker API access
from the application container. The broker mediates all container operations,
enforces immutable server-owned security policies, creates ephemeral per-execution
isolated workspaces, and guarantees resource cleanup.
"""

import hmac
import logging
import os
import re
import shutil
import tempfile
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

from backend.app.core.config import settings
from backend.app.execution.docker_runner import (
    CLIDockerClient,
    DockerClientInterface,
)
from backend.app.execution.exceptions import (
    ContainerSecurityError,
    WorkspaceSecurityError,
)
from backend.app.execution.gatekeeper import (
    DEFAULT_ALLOWED_IMAGES,
    PROHIBITED_ENV_VARS,
)
from backend.app.execution.models import (
    ExecutionPolicy,
    ExecutionStatus,
)
from backend.app.execution.policy import ExecutionPolicyEnforcer

logger = logging.getLogger(__name__)


def parse_memory_bytes(mem_str: str) -> int:
    """Parse Docker memory limit string into integer bytes."""
    s = mem_str.strip().lower()
    if not s:
        raise ContainerSecurityError("Empty memory limit string.")
    multiplier = 1
    if s.endswith("gb") or s.endswith("g"):
        multiplier = 1024 * 1024 * 1024
        s = s.rstrip("gb")
    elif s.endswith("mb") or s.endswith("m"):
        multiplier = 1024 * 1024
        s = s.rstrip("mb")
    elif s.endswith("kb") or s.endswith("k"):
        multiplier = 1024
        s = s.rstrip("kb")
    elif s.endswith("b"):
        s = s.rstrip("b")
    try:
        val = int(s)
        if val <= 0:
            raise ValueError()
        return val * multiplier
    except ValueError:
        raise ContainerSecurityError(
            f"Invalid memory limit format: '{mem_str}'"
        ) from None


class BrokerServerSecurityPolicy:
    """Server-owned immutable security constraints for sandboxed execution."""

    # Resource Ceilings
    MAX_TIMEOUT_SECONDS: float = 60.0
    MAX_MEMORY_BYTES: int = 512 * 1024 * 1024  # 512 MB
    MAX_MEMORY_STR: str = "512m"
    MAX_CPU_QUOTA: float = 2.0
    MAX_PIDS_LIMIT: int = 64
    MAX_OUTPUT_SIZE_BYTES: int = 100_000

    # Workspace Limits
    MAX_WORKSPACE_FILES_COUNT: int = 500
    MAX_SINGLE_FILE_SIZE_BYTES: int = 1_048_576  # 1 MB
    MAX_AGGREGATE_WORKSPACE_SIZE_BYTES: int = 10_485_760  # 10 MB

    # Network
    ALLOW_NETWORK: bool = False

    # Allowed Commands (base executables)
    ALLOWED_COMMANDS: tuple[str, ...] = (
        "python",
        "python3",
        "pytest",
        "ruff",
        "mypy",
        "git",
        "echo",
    )

    # Allowed Environment Variables
    ALLOWED_ENV_VARS: tuple[str, ...] = (
        "PYTHONUNBUFFERED",
        "PYTHONDONTWRITEBYTECODE",
        "PATH",
        "LANG",
        "LC_ALL",
        "TMPDIR",
    )

    PROHIBITED_ENV_VARS: tuple[str, ...] = PROHIBITED_ENV_VARS
    ALLOWED_IMAGES: tuple[str, ...] = DEFAULT_ALLOWED_IMAGES


class ExecutionBrokerRequest(BaseModel):
    """Specification for a sandboxed execution request submitted to the broker."""

    execution_id: str | None = None
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


def get_expected_broker_token() -> str | None:
    """Retrieve configured broker authentication token."""
    token = getattr(settings, "EXECUTION_BROKER_AUTH_TOKEN", None)
    if token is not None and hasattr(token, "get_secret_value"):
        val = str(token.get_secret_value()).strip()
        if val:
            return val
    raw = os.environ.get("EXECUTION_BROKER_AUTH_TOKEN", "").strip()
    return raw if raw else None


def verify_broker_auth(
    authorization: str | None = Header(None),
    x_broker_token: str | None = Header(None, alias="X-Broker-Token"),
) -> None:
    """Validate incoming HTTP request authentication against configured token.

    Broker authentication is strictly mandatory and cannot be disabled in production.
    Missing, empty, or unconfigured token secrets cause immediate fail-closed rejection.
    """
    expected = get_expected_broker_token()
    if expected is not None:
        expected = expected.strip()
    if not expected:
        logger.error(
            "Broker authentication failed closed: "
            "server auth token is not configured or empty."
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Execution broker authentication token is not configured on the "
                "server. Failing closed."
            ),
        )

    token: str | None = None
    if authorization is not None:
        parts = authorization.strip().split()
        if len(parts) != 2 or parts[0] != "Bearer" or not parts[1].strip():
            logger.warning("Rejected malformed Authorization header.")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=(
                    "Malformed Authorization header. Format must be 'Bearer <token>'."
                ),
            )
        token = parts[1].strip()
    elif x_broker_token is not None:
        token = x_broker_token.strip()
        if not token:
            logger.warning("Rejected empty X-Broker-Token header.")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="X-Broker-Token header must not be empty.",
            )
    else:
        logger.warning(
            "Rejected request with missing broker authentication credentials."
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing execution broker authentication credentials.",
        )

    # Constant-time comparison to prevent timing attacks
    if not hmac.compare_digest(token, expected):
        logger.warning("Rejected invalid execution broker authentication token.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid execution broker authentication token.",
        )


class SandboxExecutionBroker:
    """Brokers sandboxed execution with immutable policy and workspace isolation."""

    def __init__(
        self,
        docker_client: DockerClientInterface | None = None,
        workspace_base_dir: Path | None = None,
    ) -> None:
        self.docker_client = docker_client or CLIDockerClient()
        configured_root = (
            workspace_base_dir
            or getattr(settings, "EXECUTION_BROKER_WORKSPACE_ROOT", None)
            or (Path(tempfile.gettempdir()) / "agentforge_broker_ws")
        )
        self.workspace_base_dir = Path(configured_root).resolve()
        try:
            self.workspace_base_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            fallback = Path(tempfile.gettempdir()) / "agentforge_broker_ws"
            self.workspace_base_dir = fallback.resolve()
            self.workspace_base_dir.mkdir(parents=True, exist_ok=True)

    def is_healthy(self) -> bool:
        """Check if broker can reach the underlying Docker daemon."""
        return self.docker_client.is_available()

    def _validate_and_generate_exec_ids(self, client_id: str | None) -> tuple[str, str]:
        """Validate client execution ID and generate safe internal identifier."""
        if client_id is not None:
            if not isinstance(client_id, str):
                raise WorkspaceSecurityError("Execution ID must be a string.")
            if (
                "\x00" in client_id
                or ".." in client_id
                or "/" in client_id
                or "\\" in client_id
            ):
                raise WorkspaceSecurityError(
                    f"Path traversal characters detected in execution ID: '{client_id}'"
                )
            if not re.match(r"^[a-zA-Z0-9_-]{1,64}$", client_id):
                raise WorkspaceSecurityError(
                    f"Execution ID '{client_id}' contains invalid characters. "
                    "Only alphanumeric, hyphen, and underscore (max 64 chars) allowed."
                )
            external_id = client_id
        else:
            external_id = f"exec-{uuid.uuid4().hex[:12]}"

        # Broker-owned collision-resistant internal ID
        internal_id = f"{external_id}-{uuid.uuid4().hex[:12]}"
        return external_id, internal_id

    def _validate_and_constrain_policy(
        self, req: ExecutionBrokerRequest
    ) -> tuple[ExecutionPolicy, dict[str, str]]:
        """Validate caller request against server-owned immutable security policy."""
        # 1. Exact image allowlist
        image_norm = req.image.strip().lower()
        valid_images = {
            img.strip().lower() for img in BrokerServerSecurityPolicy.ALLOWED_IMAGES
        }
        if image_norm not in valid_images:
            raise ContainerSecurityError(
                f"Container image '{req.image}' is not in permitted sandbox images: "
                f"{sorted(valid_images)}"
            )

        # 2. Command executable validation
        if not req.command:
            raise ContainerSecurityError("Command list cannot be empty.")
        executable = req.command[0].strip().split("/")[-1].split("\\")[-1]
        if executable not in BrokerServerSecurityPolicy.ALLOWED_COMMANDS:
            raise ContainerSecurityError(
                f"Command executable '{executable}' is not permitted by broker "
                "server policy. Allowed commands: "
                f"{sorted(BrokerServerSecurityPolicy.ALLOWED_COMMANDS)}"
            )

        # 3. Network isolation enforcement
        if req.policy.network_enabled and not BrokerServerSecurityPolicy.ALLOW_NETWORK:
            raise ContainerSecurityError(
                "Enabling network access is strictly prohibited by broker "
                "server policy."
            )

        # 4. Timeout bounds enforcement
        if req.policy.timeout_seconds <= 0:
            raise ContainerSecurityError("Timeout must be greater than 0.")
        if req.policy.timeout_seconds > BrokerServerSecurityPolicy.MAX_TIMEOUT_SECONDS:
            raise ContainerSecurityError(
                f"Requested timeout {req.policy.timeout_seconds}s exceeds broker "
                "maximum allowed limit "
                f"({BrokerServerSecurityPolicy.MAX_TIMEOUT_SECONDS}s)."
            )
        effective_timeout = min(
            req.policy.timeout_seconds,
            BrokerServerSecurityPolicy.MAX_TIMEOUT_SECONDS,
        )

        # 5. Memory ceiling enforcement
        requested_mem = parse_memory_bytes(req.policy.memory_limit)
        if requested_mem > BrokerServerSecurityPolicy.MAX_MEMORY_BYTES:
            raise ContainerSecurityError(
                f"Requested memory limit '{req.policy.memory_limit}' exceeds broker "
                "maximum allowed limit "
                f"({BrokerServerSecurityPolicy.MAX_MEMORY_STR})."
            )

        # 6. CPU ceiling enforcement
        if (
            req.policy.cpu_limit <= 0
            or req.policy.cpu_limit > BrokerServerSecurityPolicy.MAX_CPU_QUOTA
        ):
            raise ContainerSecurityError(
                f"Requested CPU limit {req.policy.cpu_limit} exceeds broker maximum "
                f"allowed limit ({BrokerServerSecurityPolicy.MAX_CPU_QUOTA})."
            )

        # 7. Process count ceiling enforcement
        if (
            req.policy.process_limit <= 0
            or req.policy.process_limit > BrokerServerSecurityPolicy.MAX_PIDS_LIMIT
        ):
            raise ContainerSecurityError(
                f"Requested process limit {req.policy.process_limit} exceeds broker "
                "maximum allowed limit "
                f"({BrokerServerSecurityPolicy.MAX_PIDS_LIMIT})."
            )

        # 8. Environment variable filtering and sensitive variable prohibition
        sanitized_env: dict[str, str] = {
            "PYTHONUNBUFFERED": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        if req.environment:
            for k, v in req.environment.items():
                k_upper = k.strip().upper()
                if k_upper in BrokerServerSecurityPolicy.PROHIBITED_ENV_VARS:
                    raise ContainerSecurityError(
                        f"Environment variable '{k}' is prohibited by broker "
                        "security policy."
                    )
                if k_upper not in BrokerServerSecurityPolicy.ALLOWED_ENV_VARS:
                    raise ContainerSecurityError(
                        f"Environment variable '{k}' is not in broker permitted "
                        "environment list: "
                        f"{sorted(BrokerServerSecurityPolicy.ALLOWED_ENV_VARS)}"
                    )
                sanitized_env[k] = str(v)

        # 9. Build immutable server-constrained policy
        constrained_policy = ExecutionPolicy(
            timeout_seconds=int(effective_timeout),
            memory_limit=req.policy.memory_limit,
            cpu_limit=req.policy.cpu_limit,
            process_limit=req.policy.process_limit,
            maximum_output_bytes=min(
                req.policy.maximum_output_bytes,
                BrokerServerSecurityPolicy.MAX_OUTPUT_SIZE_BYTES,
            ),
            network_enabled=False,
            allowed_commands=list(BrokerServerSecurityPolicy.ALLOWED_COMMANDS),
            permitted_environment_variables=list(
                BrokerServerSecurityPolicy.ALLOWED_ENV_VARS
            ),
        )

        # 10. Secondary command syntax and pattern validation
        try:
            ExecutionPolicyEnforcer.validate_command(req.command, constrained_policy)
        except ValueError as err:
            raise ContainerSecurityError(str(err)) from err

        return constrained_policy, sanitized_env

    def _safe_cleanup_workspace(self, ws_dir: Path) -> None:
        """Safely delete workspace directory verifying strict confinement."""
        try:
            resolved = ws_dir.resolve()
            base = self.workspace_base_dir.resolve()
            if resolved == base or not resolved.is_relative_to(base):
                return
            if ws_dir.is_symlink() or resolved.is_symlink():
                return
            shutil.rmtree(resolved, ignore_errors=True)
        except Exception:  # noqa: S110  # nosec B110
            pass

    def execute_request(self, req: ExecutionBrokerRequest) -> ExecutionBrokerResponse:
        """Synchronously execute sandboxed request with strict isolation."""
        # 1. Fail closed if daemon is unavailable
        if not self.docker_client.is_available():
            ext_id = req.execution_id or f"exec-{uuid.uuid4().hex[:12]}"
            return ExecutionBrokerResponse(
                execution_id=ext_id,
                status=ExecutionStatus.FAILED,
                exit_code=127,
                error="Docker daemon is not available on execution broker.",
                errors=[
                    "Docker daemon is unavailable or unresponsive on the "
                    "execution broker. "
                    "Host execution fallback is strictly prohibited."
                ],
            )

        # 2. Validate and generate execution IDs
        try:
            ext_id, internal_id = self._validate_and_generate_exec_ids(req.execution_id)
        except WorkspaceSecurityError as ws_err:
            return ExecutionBrokerResponse(
                execution_id=req.execution_id or "invalid-id",
                status=ExecutionStatus.REJECTED,
                exit_code=126,
                error=str(ws_err),
                errors=[f"Broker security gatekeeper rejected request: {ws_err}"],
            )

        # 3. Policy Validation against immutable server policy
        try:
            constrained_policy, sanitized_env = self._validate_and_constrain_policy(req)
        except (ContainerSecurityError, ValueError) as sec_err:
            return ExecutionBrokerResponse(
                execution_id=ext_id,
                status=ExecutionStatus.REJECTED,
                exit_code=126,
                error=str(sec_err),
                errors=[f"Broker security gatekeeper rejected request: {sec_err}"],
            )

        # 4. Resolve and verify per-execution workspace path confinement
        exec_ws_dir = (self.workspace_base_dir / f"ws-{internal_id}").resolve()
        if (
            not exec_ws_dir.is_relative_to(self.workspace_base_dir.resolve())
            or exec_ws_dir == self.workspace_base_dir.resolve()
        ):
            return ExecutionBrokerResponse(
                execution_id=ext_id,
                status=ExecutionStatus.REJECTED,
                exit_code=126,
                error="Resolved workspace path escapes authorized broker root.",
                errors=["Resolved workspace path escapes authorized broker root."],
            )

        # 5. Populate workspace files with strict containment checks
        if (
            len(req.workspace_files)
            > BrokerServerSecurityPolicy.MAX_WORKSPACE_FILES_COUNT
        ):
            max_files = BrokerServerSecurityPolicy.MAX_WORKSPACE_FILES_COUNT
            return ExecutionBrokerResponse(
                execution_id=ext_id,
                status=ExecutionStatus.REJECTED,
                exit_code=126,
                error=(
                    f"Workspace file count ({len(req.workspace_files)}) exceeds "
                    f"maximum allowed ({max_files})."
                ),
                errors=["Too many files submitted in request."],
            )

        total_bytes = 0
        exec_ws_dir.mkdir(parents=True, exist_ok=True)
        try:
            for rel_path_str, content in req.workspace_files.items():
                if "\x00" in rel_path_str:
                    raise WorkspaceSecurityError(
                        f"Null byte detected in file path: '{rel_path_str}'"
                    )
                if ".." in Path(rel_path_str).parts or ".." in rel_path_str:
                    raise WorkspaceSecurityError(
                        f"Path traversal detected in file path: '{rel_path_str}'"
                    )
                if (
                    Path(rel_path_str).is_absolute()
                    or rel_path_str.startswith("/")
                    or rel_path_str.startswith("\\")
                ):
                    raise WorkspaceSecurityError(
                        f"Absolute path detected in file path: '{rel_path_str}'"
                    )
                if len(rel_path_str) >= 2 and rel_path_str[1] == ":":
                    raise WorkspaceSecurityError(
                        f"Drive letter detected in file path: '{rel_path_str}'"
                    )

                target_file = (exec_ws_dir / rel_path_str).resolve()
                if (
                    not target_file.is_relative_to(exec_ws_dir)
                    or target_file == exec_ws_dir
                ):
                    raise WorkspaceSecurityError(
                        f"File '{rel_path_str}' escapes isolated execution workspace."
                    )

                # Check symlink escapes in parent directory chain
                curr = target_file.parent
                while curr != exec_ws_dir and curr != curr.parent:
                    if curr.is_symlink():
                        raise WorkspaceSecurityError(
                            f"Symlink directory detected in path: '{curr}'"
                        )
                    curr = curr.parent

                content_bytes = content.encode("utf-8")
                max_single = BrokerServerSecurityPolicy.MAX_SINGLE_FILE_SIZE_BYTES
                if len(content_bytes) > max_single:
                    raise WorkspaceSecurityError(
                        f"File '{rel_path_str}' size exceeds maximum allowed "
                        f"({max_single} bytes)."
                    )
                total_bytes += len(content_bytes)
                max_agg = BrokerServerSecurityPolicy.MAX_AGGREGATE_WORKSPACE_SIZE_BYTES
                if total_bytes > max_agg:
                    raise WorkspaceSecurityError(
                        "Aggregate workspace size exceeds maximum allowed "
                        f"({max_agg} bytes)."
                    )

                target_file.parent.mkdir(parents=True, exist_ok=True)
                target_file.write_text(content, encoding="utf-8")
                try:
                    target_file.chmod(0o666)
                except OSError:
                    pass

            # 6. Execute container via Docker client
            output = self.docker_client.run_container(
                image=req.image,
                command=req.command,
                workspace_path=exec_ws_dir,
                policy=constrained_policy,
                environment=sanitized_env,
            )

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
                execution_id=ext_id,
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
                execution_id=ext_id,
                status=ExecutionStatus.REJECTED,
                exit_code=126,
                error=str(sec_err),
                errors=[f"Broker security gatekeeper rejected request: {sec_err}"],
            )
        finally:
            # 7. Guaranteed safe cleanup of staging files
            self._safe_cleanup_workspace(exec_ws_dir)


@asynccontextmanager
async def broker_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Ensure mandatory authentication secret is configured on broker startup."""
    token = get_expected_broker_token()
    if not token or not token.strip():
        msg = (
            "Execution broker startup failed: EXECUTION_BROKER_AUTH_TOKEN is "
            "not configured or is empty. Broker authentication is mandatory."
        )
        logger.critical(msg)
        raise RuntimeError(msg)
    yield


def create_broker_app() -> FastAPI:
    """Create FastAPI application for the isolated execution broker service."""
    app = FastAPI(
        title="AgentForge Execution Broker",
        description="Isolated container execution broker with zero-trust gatekeeper.",
        version="0.11.0",
        lifespan=broker_lifespan,
    )
    broker = SandboxExecutionBroker()

    @app.get("/health")
    def health_check() -> dict[str, Any]:
        healthy = broker.is_healthy()
        if not healthy:
            logger.error("Broker health check failed: Docker daemon is not responsive.")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Docker daemon is not responsive on execution broker.",
            )
        return {"status": "ok", "docker_available": True}

    @app.post("/v1/execute", response_model=ExecutionBrokerResponse)
    def execute_endpoint(
        req: ExecutionBrokerRequest,
        _auth: None = Depends(verify_broker_auth),
    ) -> ExecutionBrokerResponse:
        res = broker.execute_request(req)
        if res.status == ExecutionStatus.REJECTED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=res.error or "Request rejected by broker security gatekeeper.",
            )
        return res

    return app


broker_app = create_broker_app()
