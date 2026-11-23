"""Unit tests for telemetry, readiness probe, and correlation ID tracing."""

from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from backend.app.core.config import settings
from backend.app.main import app
from backend.app.orchestration.states import WorkflowStatus


@pytest.fixture
def client() -> Generator[TestClient]:
    """Provide a TestClient with clean default settings."""
    orig_auth_enabled = settings.API_KEY_AUTH_ENABLED
    orig_api_key = settings.API_KEY
    orig_backend = settings.STORAGE_BACKEND

    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        settings.API_KEY_AUTH_ENABLED = orig_auth_enabled
        settings.API_KEY = orig_api_key
        settings.STORAGE_BACKEND = orig_backend


def test_get_readiness_healthy(client: TestClient) -> None:
    """Verify /api/v1/ready returns 200 with structured component checks."""
    response = client.get("/api/v1/ready")
    assert response.status_code == 200
    data = response.json()

    assert data["ready"] is True
    assert "timestamp" in data
    assert data["environment"] == settings.ENVIRONMENT
    assert "checks" in data

    checks = data["checks"]
    assert "storage" in checks
    assert checks["storage"]["status"] == "ok"
    assert "docker_sandbox" in checks
    assert "configuration" in checks
    assert checks["configuration"]["status"] == "ok"


def test_get_readiness_storage_failure_returns_503(client: TestClient) -> None:
    """Verify /api/v1/ready returns 503 when storage backend is down."""
    mock_store = AsyncMock()
    mock_store.count.side_effect = RuntimeError("Database connection refused")

    with patch(
        "backend.app.orchestration.storage.create_workflow_store",
        return_value=mock_store,
    ):
        response = client.get("/api/v1/ready")
        assert response.status_code == 503
        data = response.json()
        assert data["ready"] is False
        assert data["checks"]["storage"]["status"] == "error"
        assert "Database connection refused" in data["checks"]["storage"]["message"]


def test_get_metrics_reflects_workflow_counts(client: TestClient) -> None:
    """Verify /api/v1/metrics provides accurate workflow execution stats."""
    mock_store = AsyncMock()

    async def mock_count(status: WorkflowStatus | None = None) -> int:
        if status is None:
            return 3
        if status in (
            WorkflowStatus.RUNNING,
            WorkflowStatus.COMPLETED,
            WorkflowStatus.FAILED,
        ):
            return 1
        return 0

    mock_store.count.side_effect = mock_count

    with patch(
        "backend.app.orchestration.storage.create_workflow_store",
        return_value=mock_store,
    ):
        response = client.get("/api/v1/metrics")
        assert response.status_code == 200
        data = response.json()

        assert "workflows" in data
        wf = data["workflows"]
        assert wf["total_workflows"] == 3
        assert wf["active_workflows"] == 1
        assert wf["completed_workflows"] == 1
        assert wf["failed_workflows"] == 1
        assert wf["failure_rate_percentage"] == round(1 / 3 * 100.0, 2)

        assert "system" in data
        sys = data["system"]
        assert sys["uptime_seconds"] >= 0.0
        assert "python_version" in sys
        assert sys["process_id"] > 0
        assert "platform" in sys


def test_correlation_id_auto_generated(client: TestClient) -> None:
    """Verify request without correlation ID gets auto-assigned UUID."""
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert "x-correlation-id" in response.headers
    assert "x-request-id" in response.headers
    assert len(response.headers["x-correlation-id"]) > 10


def test_correlation_id_propagated_from_header(client: TestClient) -> None:
    """Verify client-supplied X-Correlation-ID is echoed in response."""
    custom_id = "trace-client-12345-abcde"
    response = client.get(
        "/api/v1/health",
        headers={"x-correlation-id": custom_id},
    )
    assert response.status_code == 200
    assert response.headers.get("x-correlation-id") == custom_id
    assert response.headers.get("x-request-id") == custom_id


def test_correlation_id_on_unauthorized_error(client: TestClient) -> None:
    """Verify correlation ID and security headers exist on 401 response."""
    settings.API_KEY_AUTH_ENABLED = True
    settings.API_KEY = SecretStr("correct-api-key")

    response = client.get(
        "/api/v1/workflows",
        headers={"x-correlation-id": "unauth-trace-id"},
    )
    assert response.status_code == 401
    assert response.headers.get("x-correlation-id") == "unauth-trace-id"
    assert response.headers.get("x-frame-options") == "DENY"


def test_ready_and_metrics_exempt_from_api_key_auth(
    client: TestClient,
) -> None:
    """Verify ready and metrics probes are accessible without API key."""
    settings.API_KEY_AUTH_ENABLED = True
    settings.API_KEY = SecretStr("correct-api-key")

    resp_ready = client.get("/api/v1/ready")
    assert resp_ready.status_code in (200, 503)

    resp_metrics = client.get("/api/v1/metrics")
    assert resp_metrics.status_code == 200
