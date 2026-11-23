"""AgentForge FastAPI Foundation Entrypoint."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel, Field

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

logger = logging.getLogger(__name__)


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
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
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
