"""Unit tests for configuration system."""

import os
from unittest.mock import patch

from backend.app.core.config import Settings


def test_default_settings() -> None:
    """Verify default values of configuration settings."""
    settings = Settings()
    assert settings.APP_NAME == "AgentForge"
    assert settings.APP_VERSION == "0.1.0"
    assert settings.ENVIRONMENT == "development"
    assert settings.SANDBOX_TIMEOUT == 30
    assert settings.SANDBOX_MEMORY_LIMIT == "512m"
    assert settings.SANDBOX_CPU_LIMIT == 1


def test_missing_optional_credentials() -> None:
    """Verify application handles missing optional API credentials gracefully."""
    settings = Settings()
    assert settings.GITHUB_TOKEN is None
    assert settings.OPENAI_API_KEY is None
    assert settings.GEMINI_API_KEY is None


def test_environment_variable_override() -> None:
    """Verify environment variables override default settings."""
    env_vars = {
        "APP_NAME": "AgentForge-Custom",
        "ENVIRONMENT": "production",
        "SANDBOX_TIMEOUT": "60",
    }
    with patch.dict(os.environ, env_vars):
        custom_settings = Settings()
        assert custom_settings.APP_NAME == "AgentForge-Custom"
        assert custom_settings.ENVIRONMENT == "production"
        assert custom_settings.SANDBOX_TIMEOUT == 60
