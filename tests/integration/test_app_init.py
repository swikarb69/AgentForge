"""Integration test for application initialization and OpenAPI documentation."""

from fastapi.testclient import TestClient

from backend.app.main import app

client = TestClient(app)


def test_app_initialization_and_openapi() -> None:
    """Verify application initializes cleanly and generates OpenAPI documentation."""
    # Test OpenAPI schema endpoint
    openapi_response = client.get("/openapi.json")
    assert openapi_response.status_code == 200
    schema = openapi_response.json()
    assert schema["info"]["title"] == "AgentForge"
    assert schema["info"]["version"] == "0.1.0"
    assert "/api/v1/health" in schema["paths"]
    assert "/api/v1/version" in schema["paths"]

    # Test Swagger UI documentation endpoint
    docs_response = client.get("/docs")
    assert docs_response.status_code == 200
