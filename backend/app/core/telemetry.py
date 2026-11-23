"""Production telemetry, readiness probes, and request tracing."""

import contextvars
import os
import platform
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

correlation_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "correlation_id", default=None
)

APP_START_TIME: float = time.time()


def get_correlation_id() -> str | None:
    """Return current request correlation ID if available."""
    return correlation_id_ctx.get()


def get_uptime_seconds() -> float:
    """Return elapsed process uptime in seconds."""
    return round(time.time() - APP_START_TIME, 2)


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Track and propagate correlation IDs across incoming requests."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        correlation_id = (
            request.headers.get("x-correlation-id")
            or request.headers.get("x-request-id")
            or str(uuid.uuid4())
        )
        token = correlation_id_ctx.set(correlation_id)
        try:
            response = await call_next(request)
            response.headers["X-Correlation-ID"] = correlation_id
            response.headers["X-Request-ID"] = correlation_id
            return response
        finally:
            correlation_id_ctx.reset(token)


class ComponentCheck(BaseModel):
    """Health check outcome for an individual subsystem component."""

    status: str = Field(description="Operational status: ok, degraded, unavailable")
    message: str = Field(description="Descriptive status message")
    details: dict[str, Any] = Field(default_factory=dict)


class ReadinessResponse(BaseModel):
    """Readiness probe response payload."""

    ready: bool = Field(description="True if system is ready to accept traffic")
    timestamp: str = Field(description="ISO 8601 timestamp of check")
    environment: str = Field(description="Current application environment")
    checks: dict[str, ComponentCheck] = Field(
        description="Individual subsystem health and readiness checks"
    )


class WorkflowMetrics(BaseModel):
    """Live workflow execution metrics and counters."""

    total_workflows: int = Field(default=0)
    active_workflows: int = Field(default=0)
    completed_workflows: int = Field(default=0)
    failed_workflows: int = Field(default=0)
    paused_workflows: int = Field(default=0)
    failure_rate_percentage: float = Field(default=0.0)


class SystemMetrics(BaseModel):
    """Host environment and runtime process metrics."""

    uptime_seconds: float = Field(description="Application uptime in seconds")
    python_version: str = Field(description="Python runtime version")
    process_id: int = Field(description="Current process PID")
    platform: str = Field(description="Operating system platform")


class MetricsResponse(BaseModel):
    """System operational metrics response."""

    timestamp: str = Field(description="ISO 8601 timestamp of metrics snapshot")
    workflows: WorkflowMetrics = Field(description="Workflow execution statistics")
    system: SystemMetrics = Field(description="Host and runtime process metrics")


async def check_readiness() -> tuple[bool, dict[str, ComponentCheck]]:
    """Check readiness across storage, sandbox, and configuration."""
    from backend.app.core.config import settings
    from backend.app.execution.docker_runner import CLIDockerClient
    from backend.app.orchestration.storage import create_workflow_store

    checks: dict[str, ComponentCheck] = {}
    overall_ready = True

    # 1. Check storage reachability
    try:
        store = create_workflow_store()
        await store.count()
        checks["storage"] = ComponentCheck(
            status="ok",
            message=f"Storage backend '{settings.STORAGE_BACKEND}' is reachable.",
            details={"backend": settings.STORAGE_BACKEND},
        )
    except Exception as err:
        overall_ready = False
        checks["storage"] = ComponentCheck(
            status="error",
            message=f"Storage check failed: {err}",
            details={"backend": settings.STORAGE_BACKEND, "error": str(err)},
        )

    # 2. Check Docker sandbox reachability
    try:
        docker_client = CLIDockerClient()
        docker_ok = docker_client.is_available()
        checks["docker_sandbox"] = ComponentCheck(
            status="ok" if docker_ok else "unavailable",
            message=(
                "Docker sandbox is available."
                if docker_ok
                else "Docker daemon unreachable or not installed."
            ),
            details={
                "available": docker_ok,
                "fallback_allowed": settings.HOST_EXECUTION_FALLBACK_ALLOWED,
            },
        )
        if settings.is_production and not docker_ok:
            overall_ready = False
    except Exception as err:
        checks["docker_sandbox"] = ComponentCheck(
            status="unavailable",
            message=f"Docker check error: {err}",
            details={"error": str(err)},
        )
        if settings.is_production:
            overall_ready = False

    # 3. Check configuration safety constraints
    config_errors = settings.validate_configuration()
    if config_errors:
        overall_ready = False
        checks["configuration"] = ComponentCheck(
            status="error",
            message="Configuration validation errors detected.",
            details={"errors": config_errors},
        )
    else:
        checks["configuration"] = ComponentCheck(
            status="ok",
            message="Configuration safety rules verified.",
            details={"environment": settings.ENVIRONMENT},
        )

    return overall_ready, checks


async def collect_metrics() -> MetricsResponse:
    """Gather live workflow execution counters and host runtime metrics."""
    from backend.app.orchestration.states import WorkflowStatus
    from backend.app.orchestration.storage import create_workflow_store

    store = create_workflow_store()
    try:
        total = await store.count()
        running = await store.count(WorkflowStatus.RUNNING)
        queued = await store.count(WorkflowStatus.QUEUED)
        retrying = await store.count(WorkflowStatus.RETRYING)
        waiting = await store.count(WorkflowStatus.WAITING_FOR_APPROVAL)
        active = running + queued + retrying + waiting
        completed = await store.count(WorkflowStatus.COMPLETED)
        failed = await store.count(WorkflowStatus.FAILED)
        paused = await store.count(WorkflowStatus.PAUSED)
        rate = round((failed / total * 100.0), 2) if total > 0 else 0.0
    except Exception:
        total = 0
        active = 0
        completed = 0
        failed = 0
        paused = 0
        rate = 0.0

    wf_metrics = WorkflowMetrics(
        total_workflows=total,
        active_workflows=active,
        completed_workflows=completed,
        failed_workflows=failed,
        paused_workflows=paused,
        failure_rate_percentage=rate,
    )

    sys_metrics = SystemMetrics(
        uptime_seconds=get_uptime_seconds(),
        python_version=platform.python_version(),
        process_id=os.getpid(),
        platform=platform.platform(),
    )

    return MetricsResponse(
        timestamp=datetime.now(UTC).isoformat(),
        workflows=wf_metrics,
        system=sys_metrics,
    )
