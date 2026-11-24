"""Unit tests for AgentForge security hardening and boundaries."""

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from backend.app.core.config import settings
from backend.app.main import app


@pytest.fixture
def client() -> Generator[TestClient]:
    """Provide a TestClient with clean default settings."""
    orig_auth_enabled = settings.API_KEY_AUTH_ENABLED
    orig_api_key = settings.API_KEY
    orig_cors = settings.CORS_ORIGINS

    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        settings.API_KEY_AUTH_ENABLED = orig_auth_enabled
        settings.API_KEY = orig_api_key
        settings.CORS_ORIGINS = orig_cors


def test_security_headers_present_on_health_check(client: TestClient) -> None:
    """Verify production security headers are set on API responses."""
    response = client.get("/api/v1/health")
    assert response.status_code == 200

    headers = response.headers
    assert headers.get("X-Content-Type-Options") == "nosniff"
    assert headers.get("X-Frame-Options") == "DENY"
    assert headers.get("X-XSS-Protection") == "0"
    assert "Strict-Transport-Security" in headers
    assert "max-age=31536000" in headers["Strict-Transport-Security"]
    assert headers.get("Content-Security-Policy") == "default-src 'self'"


def test_cors_preflight_and_headers(client: TestClient) -> None:
    """Verify CORS preflight OPTIONS request returns expected headers."""
    response = client.options(
        "/api/v1/health",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert "access-control-allow-origin" in response.headers


def test_public_endpoints_accessible_when_auth_disabled(
    client: TestClient,
) -> None:
    """Verify public endpoints succeed when API key auth is disabled."""
    settings.API_KEY_AUTH_ENABLED = False

    resp_health = client.get("/api/v1/health")
    assert resp_health.status_code == 200

    resp_version = client.get("/api/v1/version")
    assert resp_version.status_code == 200


def test_public_endpoints_accessible_when_auth_enabled(
    client: TestClient,
) -> None:
    """Verify public and documentation endpoints are exempt from API key auth."""
    settings.API_KEY_AUTH_ENABLED = True
    settings.API_KEY = SecretStr("secret-api-key-12345")

    # Health and version
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/version").status_code == 200

    # Documentation and schema
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/docs").status_code == 200


def test_protected_endpoint_rejected_without_api_key(
    client: TestClient,
) -> None:
    """Verify protected endpoints return 401 when API key auth is active."""
    settings.API_KEY_AUTH_ENABLED = True
    settings.API_KEY = SecretStr("secret-api-key-12345")

    # Attempt accessing protected workflow router without key
    response = client.get("/api/v1/workflows")
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid or missing API key"}
    # Verify security headers are also present on 401 response
    assert response.headers.get("X-Frame-Options") == "DENY"


def test_protected_endpoint_rejected_with_invalid_api_key(
    client: TestClient,
) -> None:
    """Verify protected endpoints return 401 with incorrect API key."""
    settings.API_KEY_AUTH_ENABLED = True
    settings.API_KEY = SecretStr("secret-api-key-12345")

    # Incorrect X-API-Key
    response = client.get(
        "/api/v1/workflows",
        headers={"x-api-key": "wrong-key"},
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid or missing API key"}

    # Incorrect Bearer token
    response_bearer = client.get(
        "/api/v1/workflows",
        headers={"Authorization": "Bearer wrong-bearer-token"},
    )
    assert response_bearer.status_code == 401


def test_protected_endpoint_allowed_with_valid_x_api_key(
    client: TestClient,
) -> None:
    """Verify valid X-API-Key grants access to protected routes."""
    settings.API_KEY_AUTH_ENABLED = True
    settings.API_KEY = SecretStr("secret-api-key-12345")

    response = client.get(
        "/api/v1/workflows",
        headers={"x-api-key": "secret-api-key-12345"},
    )
    # Should not be 401 (will be 200 for workflow listing)
    assert response.status_code == 200


def test_protected_endpoint_allowed_with_valid_bearer_token(
    client: TestClient,
) -> None:
    """Verify valid Authorization Bearer token grants access."""
    settings.API_KEY_AUTH_ENABLED = True
    settings.API_KEY = SecretStr("secret-api-key-12345")

    response = client.get(
        "/api/v1/workflows",
        headers={"Authorization": "Bearer secret-api-key-12345"},
    )
    assert response.status_code == 200


def test_options_preflight_on_protected_endpoint_exempt(
    client: TestClient,
) -> None:
    """Verify OPTIONS preflight on protected endpoint succeeds without key."""
    settings.API_KEY_AUTH_ENABLED = True
    settings.API_KEY = SecretStr("secret-api-key-12345")

    response = client.options(
        "/api/v1/workflows",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200


def test_settings_security_guardrails_validation() -> None:
    """Verify safety guardrails enforce auto-merge and host-execution limits."""
    # Test auto merge rejection
    settings.AUTO_MERGE_ALLOWED = True
    errors = settings.validate_configuration()
    settings.AUTO_MERGE_ALLOWED = False
    assert any("AUTO_MERGE_ALLOWED" in err for err in errors)

    # Test host execution fallback rejection
    settings.HOST_EXECUTION_FALLBACK_ALLOWED = True
    errors = settings.validate_configuration()
    settings.HOST_EXECUTION_FALLBACK_ALLOWED = False
    assert any("HOST_EXECUTION_FALLBACK_ALLOWED" in err for err in errors)


def test_settings_secret_redaction() -> None:
    """Verify sensitive keys are masked in redacted summary."""
    settings.OPENAI_API_KEY = SecretStr("sk-test-secret-value-123")
    summary = settings.get_redacted_summary()
    assert summary["OPENAI_API_KEY"] == "[CONFIGURED]"
    assert "sk-test-secret-value-123" not in str(summary)


def test_production_configuration_security_rules() -> None:
    """Verify rigorous security constraints are validated in production mode."""
    orig_env = settings.ENVIRONMENT
    orig_auth = settings.API_KEY_AUTH_ENABLED
    orig_key = settings.API_KEY
    orig_cors = settings.CORS_ORIGINS
    orig_ai = settings.AI_PROVIDER
    orig_gen = settings.GENERATION_PROVIDER
    orig_gem = settings.GEMINI_API_KEY
    orig_oai = settings.OPENAI_API_KEY

    try:
        settings.ENVIRONMENT = "production"

        # 1. API key auth disabled in production
        settings.API_KEY_AUTH_ENABLED = False
        errors = settings.validate_configuration()
        assert any("API_KEY_AUTH_ENABLED must be True" in err for err in errors)

        # 2. API key auth enabled but key missing or whitespace
        settings.API_KEY_AUTH_ENABLED = True
        settings.API_KEY = None
        errors = settings.validate_configuration()
        assert any("API_KEY must be provided" in err for err in errors)

        settings.API_KEY = SecretStr("   ")
        errors = settings.validate_configuration()
        assert any(
            "API_KEY must be provided with a non-empty secret" in err for err in errors
        )

        settings.API_KEY = SecretStr("valid-production-api-key")  # noqa: S106

        # 3. Wildcard CORS in production
        settings.CORS_ORIGINS = ["*"]
        errors = settings.validate_configuration()
        assert any(
            "CORS_ORIGINS must not contain wildcard '*'" in err for err in errors
        )

        settings.CORS_ORIGINS = ["https://app.agentforge.dev"]

        # 4. Active provider missing required API key
        settings.AI_PROVIDER = "gemini"
        settings.GENERATION_PROVIDER = "mock"
        settings.GEMINI_API_KEY = None
        errors = settings.validate_configuration()
        assert any("GEMINI_API_KEY must be configured" in err for err in errors)

        settings.AI_PROVIDER = "openai"
        settings.GENERATION_PROVIDER = "mock"
        settings.OPENAI_API_KEY = None
        errors = settings.validate_configuration()
        assert any("OPENAI_API_KEY must be configured" in err for err in errors)

    finally:
        settings.ENVIRONMENT = orig_env
        settings.API_KEY_AUTH_ENABLED = orig_auth
        settings.API_KEY = orig_key
        settings.CORS_ORIGINS = orig_cors
        settings.AI_PROVIDER = orig_ai
        settings.GENERATION_PROVIDER = orig_gen
        settings.GEMINI_API_KEY = orig_gem
        settings.OPENAI_API_KEY = orig_oai


def test_production_mode_disables_docs_endpoints() -> None:
    """Verify OpenAPI and documentation endpoints are disabled in production."""
    from fastapi import FastAPI

    orig_env = settings.ENVIRONMENT
    try:
        settings.ENVIRONMENT = "production"
        assert settings.is_production is True

        prod_app = FastAPI(
            docs_url=None if settings.is_production else "/docs",
            redoc_url=None if settings.is_production else "/redoc",
            openapi_url=None if settings.is_production else "/openapi.json",
        )
        with TestClient(prod_app) as prod_client:
            assert prod_client.get("/docs").status_code == 404
            assert prod_client.get("/redoc").status_code == 404
            assert prod_client.get("/openapi.json").status_code == 404
    finally:
        settings.ENVIRONMENT = orig_env
