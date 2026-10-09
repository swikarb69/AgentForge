"""AgentForge FastAPI Foundation Entrypoint."""

from fastapi import FastAPI
from pydantic import BaseModel, Field

from backend.app.api.routes import issues, repositories
from backend.app.core.config import settings


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
)

# Register API routers
app.include_router(repositories.router)
app.include_router(issues.router)


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
