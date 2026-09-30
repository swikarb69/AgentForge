"""Unit tests for health and version API endpoints."""

from fastapi.testclient import TestClient

from backend.app.main import app

client = TestClient(app)


def test_health_endpoint() -> None:
    """Verify GET /api/v1/health returns healthy status."""
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"


def test_version_endpoint() -> None:
    """Verify GET /api/v1/version returns application name and version."""
    response = client.get("/api/v1/version")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "AgentForge"
    assert data["version"] == "0.1.0"
