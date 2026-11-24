"""AgentForge FastAPI Foundation Entrypoint."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from backend.app.api.routes import (
    approval,
    debugging,
    execution,
    generation,
    issues,
    planning,
    repositories,
    review,
    workflows,
)
from backend.app.core.config import settings
from backend.app.core.telemetry import (
    CorrelationIdMiddleware,
    MetricsResponse,
    ReadinessResponse,
    check_readiness,
    collect_metrics,
)

logger = logging.getLogger(__name__)

PUBLIC_EXEMPT_PATHS: set[str] = {
    "/api/v1/health",
    "/api/v1/version",
    "/api/v1/ready",
    "/api/v1/metrics",
    "/docs",
    "/redoc",
    "/openapi.json",
}


def _is_public_path(path: str) -> bool:
    """Return True if path is exempt from API key authentication."""
    normalized = path.rstrip("/")
    if normalized in PUBLIC_EXEMPT_PATHS or path in PUBLIC_EXEMPT_PATHS:
        return True
    if path.startswith("/docs") or path.startswith("/redoc"):
        return True
    return False


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Enforce production security headers on all HTTP responses."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "0"
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
        response.headers["Content-Security-Policy"] = "default-src 'self'"
        return response


class APIKeyAuthMiddleware(BaseHTTPMiddleware):
    """Enforce API key authentication for protected endpoints when enabled."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if not settings.API_KEY_AUTH_ENABLED or request.method == "OPTIONS":
            return await call_next(request)

        if _is_public_path(request.url.path):
            return await call_next(request)

        # Extract API key from header: X-API-Key or Authorization Bearer
        api_key = request.headers.get("x-api-key")
        if not api_key:
            auth_header = request.headers.get("authorization", "")
            if auth_header.startswith("Bearer "):
                api_key = auth_header[7:].strip()

        configured_key = (
            settings.API_KEY.get_secret_value() if settings.API_KEY else None
        )

        if not api_key or not configured_key or api_key != configured_key:
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or missing API key"},
            )

        return await call_next(request)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Execute startup configuration validation and shutdown lifecycle tasks."""
    errors = settings.validate_configuration()
    if errors:
        msg = f"Configuration validation failed: {'; '.join(errors)}"
        if settings.is_production:
            logger.critical(msg)
            raise RuntimeError(msg)
        logger.warning(msg)

    logger.info(
        "AgentForge %s starting up in %s environment.",
        settings.APP_VERSION,
        settings.ENVIRONMENT,
    )
    yield
    logger.info("AgentForge shutting down.")


class HealthResponse(BaseModel):
    """Schema for API health status check."""

    status: str = Field(
        default="healthy", description="Current operational status of the service"
    )


class VersionResponse(BaseModel):
    """Schema for API version details."""

    name: str = Field(description="Name of the application")
    version: str = Field(description="Version string of the application")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description=(
        "AgentForge: Autonomous AI software-engineering agent transforming "
        "GitHub issues into tested, reviewed, and auditable pull requests."
    ),
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
    openapi_url=None if settings.is_production else "/openapi.json",
    lifespan=lifespan,
)

# Register Middlewares (added in order: inner -> outer)
app.add_middleware(APIKeyAuthMiddleware)
app.add_middleware(CorrelationIdMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials="*" not in settings.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API routers
app.include_router(repositories.router)
app.include_router(issues.router)
app.include_router(planning.router)
app.include_router(generation.router)
app.include_router(execution.router)
app.include_router(debugging.router)
app.include_router(review.router)
app.include_router(approval.router)
app.include_router(workflows.router)


@app.get(
    "/api/v1/health",
    response_model=HealthResponse,
    summary="Health Check",
    tags=["System"],
)
def get_health() -> HealthResponse:
    """Return health status of the AgentForge application."""
    return HealthResponse(status="healthy")


@app.get(
    "/api/v1/version",
    response_model=VersionResponse,
    summary="Version Information",
    tags=["System"],
)
def get_version() -> VersionResponse:
    """Return application name and version details."""
    return VersionResponse(name=settings.APP_NAME, version=settings.APP_VERSION)


@app.get(
    "/api/v1/ready",
    response_model=ReadinessResponse,
    summary="Readiness Probe",
    tags=["System"],
)
async def get_readiness(response: Response) -> ReadinessResponse:
    """Return component readiness check results."""
    ready, checks = await check_readiness()
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(
        ready=ready,
        timestamp=datetime.now(UTC).isoformat(),
        environment=settings.ENVIRONMENT,
        checks=checks,
    )


@app.get(
    "/api/v1/metrics",
    response_model=MetricsResponse,
    summary="Operational Metrics",
    tags=["System"],
)
async def get_metrics() -> MetricsResponse:
    """Return runtime operational metrics."""
    return await collect_metrics()
