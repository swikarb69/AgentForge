"""Application configuration management using Pydantic Settings."""

from typing import Any

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """AgentForge application settings and environment configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    # Application Configuration
    APP_NAME: str = "AgentForge"
    APP_VERSION: str = "0.11.0"
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"
    API_V1_STR: str = "/api/v1"
    SECRET_KEY: SecretStr | None = None

    # AI Provider Configuration
    AI_PROVIDER: str = "mock"
    OPENAI_API_KEY: SecretStr | None = None
    OPENAI_MODEL: str = "gpt-4o"
    GEMINI_API_KEY: SecretStr | None = None
    GEMINI_MODEL: str = "gemini-1.5-pro"
    AI_TIMEOUT_SECONDS: int = 60
    AI_MAX_RETRIES: int = 3
    AI_TEMPERATURE: float = 0.2

    # GitHub Integration Configuration
    GITHUB_TOKEN: SecretStr | None = None
    GITHUB_API_URL: str = "https://api.github.com"
    GITHUB_OWNER: str = "swikarb69"
    GITHUB_REPO: str = "AgentForge"

    # Storage & Persistence Configuration
    DATABASE_URL: str = "sqlite:///./agentforge.db"
    STORAGE_BACKEND: str = "memory"
    STORAGE_DIR: str = "agentforge-storage"

    # Sandbox & Isolated Execution Configuration
    SANDBOX_TIMEOUT: int = 30
    SANDBOX_MEMORY_LIMIT: str = "512m"
    SANDBOX_CPU_LIMIT: int = 1
    DOCKER_IMAGE: str = "agentforge-sandbox:latest"
    DOCKER_HOST: str | None = None
    WORKSPACE_ROOT: str = "agentforge-workspaces"
    WORKSPACE_SIZE_LIMIT: int = 50_000_000  # 50MB
    WORKSPACE_RETENTION_SECONDS: int = 3600  # 1 hour
    EXECUTION_TIMEOUT: int = 30
    EXECUTION_MEMORY_LIMIT: str = "512m"
    EXECUTION_CPU_LIMIT: float = 1.0
    EXECUTION_PROCESS_LIMIT: int = 64
    EXECUTION_MAX_OUTPUT: int = 100_000
    EXECUTION_NETWORK_ENABLED: bool = False
    ALLOWED_PROFILES: list[str] = [
        "python-unit-tests",
        "python-quality-checks",
    ]
    DOCKER_CONTAINER_GATEKEEPER_ENFORCED: bool = True
    DOCKER_NAMED_VOLUME: str | None = None
    DOCKER_PER_EXECUTION_VOLUMES: bool = True
    EXECUTION_BROKER_URL: str | None = None
    EXECUTION_BROKER_AUTH_TOKEN: SecretStr | None = None
    EXECUTION_BROKER_WORKSPACE_ROOT: str | None = None
    SANDBOX_ALLOWED_IMAGES: list[str] = [
        "agentforge-sandbox:latest",
        "agentforge-sandbox:0.11.0",
        "agentforge-sandbox",
    ]

    # Repository Scanner Configuration
    MAX_FILE_SIZE: int = 1_048_576  # 1MB limit per file
    MAX_FILES: int = 10_000  # Max files scanned per repo

    # Issue Intelligence Configuration
    MAX_ISSUE_TITLE_LENGTH: int = 256
    MAX_ISSUE_DESCRIPTION_LENGTH: int = 65_536  # 64KB max issue text

    # Code Generation Configuration
    GENERATION_PROVIDER: str = "mock"
    GENERATION_TIMEOUT: int = 60
    MAX_CONTEXT_SIZE: int = 100_000
    MAX_GENERATED_OUTPUT: int = 100_000
    MAX_FILES_PER_REQUEST: int = 10
    MAX_TASKS_PER_REQUEST: int = 20
    GENERATION_ARTIFACT_DIR: str = "agentforge-artifacts/generation"
    PROTECTED_PATHS: list[str] = [
        ".env",
        ".env.*",
        "id_rsa",
        "id_ed25519",
        "*.pem",
        "*.key",
        "secrets.json",
        "credentials.json",
    ]
    GENERATION_RETRY_LIMIT: int = 2

    # Debugging & Automatic Repair Configuration (Sprint 6)
    DEBUGGING_ENABLED: bool = True
    DEBUGGING_MAX_ATTEMPTS: int = 3
    DEBUGGING_MAX_ATTEMPTS_LIMIT: int = 5
    DEBUGGING_ALLOW_PROVIDER_DIAGNOSIS: bool = False
    DEBUGGING_AUTO_EXECUTE_REPAIRS: bool = True
    DEBUGGING_MAX_EVIDENCE_BYTES: int = 100_000
    DEBUGGING_MAX_TRACEBACK_LINES: int = 50
    DEBUGGING_REQUIRE_APPROVAL_FOR_HIGH_RISK: bool = True

    # Security & Production Safety Controls
    AUTO_MERGE_ALLOWED: bool = Field(
        default=False,
        description="Zero auto-merge policy flag; MUST remain false",
    )
    HOST_EXECUTION_FALLBACK_ALLOWED: bool = Field(
        default=False,
        description="Host execution fallback forbidden; MUST remain false",
    )
    CORS_ORIGINS: list[str] = ["*"]
    API_KEY_AUTH_ENABLED: bool = False
    API_KEY: SecretStr | None = None

    @property
    def is_production(self) -> bool:
        """True if running in production mode."""
        return self.ENVIRONMENT.lower() in ("production", "prod")

    @property
    def effective_ai_provider(self) -> str:
        """Resolve active AI provider (GENERATION_PROVIDER fallback to AI_PROVIDER)."""
        if self.GENERATION_PROVIDER and self.GENERATION_PROVIDER.lower() != "mock":
            return self.GENERATION_PROVIDER.lower()
        return self.AI_PROVIDER.lower()

    @property
    def is_auth_enforced(self) -> bool:
        """Production mode ALWAYS enforces authentication; cannot be overridden."""
        if self.is_production:
            return True
        return self.API_KEY_AUTH_ENABLED

    def validate_configuration(self) -> list[str]:
        """Validate safety constraints and configuration consistency."""
        errors: list[str] = []

        # 1. Non-negotiable safety guardrails
        if self.AUTO_MERGE_ALLOWED:
            errors.append(
                "AUTO_MERGE_ALLOWED must never be enabled (Zero Auto-Merge "
                "boundary violated)."
            )
        if self.HOST_EXECUTION_FALLBACK_ALLOWED:
            errors.append(
                "HOST_EXECUTION_FALLBACK_ALLOWED must never be enabled (Host "
                "execution fallback prohibited)."
            )

        # 2. Production requirements
        if self.is_production:
            if not self.API_KEY_AUTH_ENABLED:
                errors.append(
                    "API_KEY_AUTH_ENABLED cannot be disabled in production. "
                    "Authentication is mandatory."
                )
            if not self.API_KEY or not self.API_KEY.get_secret_value().strip():
                errors.append(
                    "API_KEY must be provided with a non-empty secret in production."
                )
            if "*" in self.CORS_ORIGINS:
                errors.append(
                    "CORS_ORIGINS must not contain wildcard '*' in production. "
                    "Explicitly specify allowed origins."
                )
            if not self.DOCKER_CONTAINER_GATEKEEPER_ENFORCED:
                errors.append(
                    "DOCKER_CONTAINER_GATEKEEPER_ENFORCED cannot be disabled "
                    "in production."
                )
            active_provider = self.effective_ai_provider
            if active_provider == "gemini" and not self.GEMINI_API_KEY:
                errors.append(
                    "GEMINI_API_KEY must be configured when AI_PROVIDER is 'gemini'."
                )
            if active_provider == "openai" and not self.OPENAI_API_KEY:
                errors.append(
                    "OPENAI_API_KEY must be configured when AI_PROVIDER is 'openai'."
                )
            if self.EXECUTION_BROKER_URL and (
                not self.EXECUTION_BROKER_AUTH_TOKEN
                or not self.EXECUTION_BROKER_AUTH_TOKEN.get_secret_value().strip()
            ):
                errors.append(
                    "EXECUTION_BROKER_AUTH_TOKEN must be configured with a non-empty "
                    "secret when EXECUTION_BROKER_URL is configured in production."
                )

        return errors

    def get_redacted_summary(self) -> dict[str, Any]:
        """Return configuration summary with all sensitive credentials redacted."""
        summary: dict[str, Any] = {}
        for field_name, field_val in self:
            if isinstance(field_val, SecretStr):
                summary[field_name] = (
                    "[CONFIGURED]" if field_val.get_secret_value() else None
                )
            elif field_val is None:
                summary[field_name] = None
            else:
                summary[field_name] = field_val
        return summary


settings = Settings()
