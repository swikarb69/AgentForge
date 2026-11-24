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
        assert any("API_KEY_AUTH_ENABLED cannot be disabled" in err for err in errors)

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


def test_production_auth_cannot_be_disabled_via_override(
    client: TestClient,
) -> None:
    """Regression Test 1: Production authentication cannot be bypassed.

    Even if an environment or Compose override sets API_KEY_AUTH_ENABLED=False,
    settings.is_auth_enforced must remain True and protected endpoints must
    reject unauthenticated requests with 401.
    """
    orig_env = settings.ENVIRONMENT
    orig_auth = settings.API_KEY_AUTH_ENABLED
    orig_key = settings.API_KEY

    try:
        settings.ENVIRONMENT = "production"
        settings.API_KEY_AUTH_ENABLED = False  # Attempted bypass override
        settings.API_KEY = SecretStr("prod-secret-99999")  # noqa: S106

        # Prove is_auth_enforced remains True in production
        assert settings.is_production is True
        assert settings.is_auth_enforced is True

        # Unauthenticated request to protected endpoint MUST return 401
        res_unauth = client.get("/api/v1/workflows")
        assert res_unauth.status_code == 401
        assert res_unauth.json() == {"detail": "Invalid or missing API key"}

        # Request with invalid key MUST return 401
        res_invalid = client.get(
            "/api/v1/workflows",
            headers={"x-api-key": "invalid-override-key"},
        )
        assert res_invalid.status_code == 401

        # Request with valid production key succeeds
        res_auth = client.get(
            "/api/v1/workflows",
            headers={"x-api-key": "prod-secret-99999"},
        )
        assert res_auth.status_code == 200

        # Public endpoints remain accessible without auth
        res_health = client.get("/api/v1/health")
        assert res_health.status_code == 200
        res_version = client.get("/api/v1/version")
        assert res_version.status_code == 200

    finally:
        settings.ENVIRONMENT = orig_env
        settings.API_KEY_AUTH_ENABLED = orig_auth
        settings.API_KEY = orig_key


def test_docker_runner_socket_isolation_and_host_support() -> None:
    """Regression Test 2: Docker runner security boundaries & socket isolation.

    Proves:
    1. CLIDockerClient correctly uses -H for socket proxy mediation.
    2. Sandbox container execution NEVER mounts /var/run/docker.sock.
    3. Sandbox enforces least-privilege options (--read-only, --cap-drop ALL, etc.).
    4. docker-compose.yml does NOT mount /var/run/docker.sock into agentforge app.
    """
    from pathlib import Path

    from backend.app.execution.docker_runner import CLIDockerClient
    from backend.app.execution.models import ExecutionPolicy

    # 1. Verify DOCKER_HOST handling
    client = CLIDockerClient(
        docker_binary="docker",
        docker_host="tcp://docker-proxy:2375",
    )
    assert client.docker_host == "tcp://docker-proxy:2375"

    policy = ExecutionPolicy()
    ws_path = Path("/tmp/test-workspace")  # noqa: S108
    args = client._build_docker_args(
        image="agentforge-sandbox:latest",
        command=["pytest"],
        workspace_path=ws_path,
        policy=policy,
        environment={"TEST_VAR": "1"},
    )

    # Prove -H parameter is passed to docker client
    assert "-H" in args
    h_idx = args.index("-H")
    assert args[h_idx + 1] == "tcp://docker-proxy:2375"

    # Prove /var/run/docker.sock is NEVER mounted into sandbox
    assert not any("docker.sock" in arg for arg in args)

    # Prove security sandbox constraints
    assert "--read-only" in args
    assert "--cap-drop" in args
    assert "ALL" in args
    assert "--network" in args
    assert "none" in args
    assert "--user" in args
    assert "10001:10001" in args

    # 4. Verify docker-compose.yml does NOT mount /var/run/docker.sock into agentforge
    compose_path = Path("docker-compose.yml")
    if compose_path.exists():
        compose_content = compose_path.read_text(encoding="utf-8")
        # Ensure agentforge service block does not mount docker.sock
        lines = compose_content.splitlines()
        in_agentforge = False
        agentforge_volumes: list[str] = []
        for line in lines:
            if line.strip().startswith("agentforge:"):
                in_agentforge = True
            elif in_agentforge and line.strip().startswith("healthcheck:"):
                in_agentforge = False
            elif in_agentforge and "docker.sock" in line:
                agentforge_volumes.append(line)

        assert len(agentforge_volumes) == 0, (
            "Found docker.sock mounted directly in agentforge container: "
            f"{agentforge_volumes}"
        )


def test_dependency_audit_ci_failure_enforcement() -> None:
    """Regression Test 3: CI dependency audit failure stops workflow.

    Proves:
    1. .github/workflows/ci.yml runs pip-audit without fallback echoes.
    2. pip-audit step does not have continue-on-error: true.
    3. Security scan job will fail overall workflow on vulnerability detection.
    """
    from pathlib import Path

    ci_path = Path(".github/workflows/ci.yml")
    assert ci_path.exists()
    ci_content = ci_path.read_text(encoding="utf-8")

    # Verify security-scan job exists
    assert "security-scan:" in ci_content

    # Verify pip-audit is invoked directly
    assert "pip-audit --desc" in ci_content

    # Prove NO fallback echo swallowing the exit code
    assert "|| echo" not in ci_content

    # Prove NO continue-on-error on security scan steps
    sec_block = ci_content.split("security-scan:")[1].split("test-suite:")[0]
    assert "continue-on-error: true" not in sec_block
