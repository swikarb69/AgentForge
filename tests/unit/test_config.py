"""Unit tests for configuration system, secret management, and safety validation."""

import os
from unittest.mock import patch

from pydantic import SecretStr

from backend.app.core.config import Settings


def test_default_settings() -> None:
    """Verify default values of configuration settings."""
    settings = Settings()
    assert settings.APP_NAME == "AgentForge"
    assert settings.APP_VERSION == "0.11.0"
    assert settings.ENVIRONMENT == "development"
    assert settings.is_production is False
    assert settings.AI_PROVIDER == "mock"
    assert settings.GITHUB_OWNER == "swikarb69"
    assert settings.GITHUB_REPO == "AgentForge"
    assert settings.STORAGE_BACKEND == "memory"
    assert settings.AUTO_MERGE_ALLOWED is False
    assert settings.HOST_EXECUTION_FALLBACK_ALLOWED is False
    assert settings.SANDBOX_TIMEOUT == 30
    assert settings.DEBUGGING_ENABLED is True


def test_missing_optional_credentials() -> None:
    """Verify application handles missing optional API credentials gracefully."""
    settings = Settings()
    assert settings.GITHUB_TOKEN is None
    assert settings.OPENAI_API_KEY is None
    assert settings.GEMINI_API_KEY is None
    assert settings.SECRET_KEY is None


def test_environment_variable_override() -> None:
    """Verify environment variables override default settings."""
    env_vars = {
        "APP_NAME": "AgentForge-Custom",
        "ENVIRONMENT": "production",
        "SANDBOX_TIMEOUT": "60",
        "AI_PROVIDER": "gemini",
        "GEMINI_API_KEY": "secret-gemini-test-key",
    }
    with patch.dict(os.environ, env_vars):
        custom_settings = Settings()
        assert custom_settings.APP_NAME == "AgentForge-Custom"
        assert custom_settings.ENVIRONMENT == "production"
        assert custom_settings.is_production is True
        assert custom_settings.SANDBOX_TIMEOUT == 60
        assert custom_settings.AI_PROVIDER == "gemini"
        assert custom_settings.GEMINI_API_KEY is not None
        assert (
            custom_settings.GEMINI_API_KEY.get_secret_value()
            == "secret-gemini-test-key"
        )


def test_secret_redaction_summary() -> None:
    """Verify secrets are cleanly redacted and never exposed in summaries."""
    settings = Settings(
        GEMINI_API_KEY=SecretStr("super-secret-gemini-key"),
        GITHUB_TOKEN=SecretStr("super-secret-github-token"),
    )
    summary = settings.get_redacted_summary()
    assert summary["GEMINI_API_KEY"] == "[CONFIGURED]"
    assert summary["GITHUB_TOKEN"] == "[CONFIGURED]"  # noqa: S105
    assert "super-secret-gemini-key" not in str(summary)
    assert "super-secret-github-token" not in str(summary)


def test_safety_validation_zero_auto_merge() -> None:
    """Verify validation strictly blocks any attempt to enable auto-merge."""
    settings = Settings(AUTO_MERGE_ALLOWED=True)
    errors = settings.validate_configuration()
    assert any("Zero Auto-Merge boundary violated" in err for err in errors)


def test_safety_validation_host_fallback() -> None:
    """Verify validation strictly blocks host execution fallback."""
    settings = Settings(HOST_EXECUTION_FALLBACK_ALLOWED=True)
    errors = settings.validate_configuration()
    assert any("Host execution fallback prohibited" in err for err in errors)


def test_safety_validation_production_requirements() -> None:
    """Verify production requires API keys when services are active."""
    # Production with API key auth enabled but no key provided
    prod_settings = Settings(
        ENVIRONMENT="production",
        API_KEY_AUTH_ENABLED=True,
        API_KEY=None,
    )
    errors = prod_settings.validate_configuration()
    assert any("API_KEY must be provided" in err for err in errors)

    # Production with gemini provider but no gemini key
    gemini_prod = Settings(
        ENVIRONMENT="production",
        AI_PROVIDER="gemini",
        GEMINI_API_KEY=None,
    )
    errors_gemini = gemini_prod.validate_configuration()
    assert any("GEMINI_API_KEY must be configured" in err for err in errors_gemini)


def test_safety_validation_clean_defaults() -> None:
    """Verify default development configuration produces zero validation errors."""
    settings = Settings()
    errors = settings.validate_configuration()
    assert len(errors) == 0
