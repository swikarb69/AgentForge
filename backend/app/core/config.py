"""Application configuration management using Pydantic Settings."""

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """AgentForge application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    # Application Configuration
    APP_NAME: str = "AgentForge"
    APP_VERSION: str = "0.1.0"
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"

    # AI & GitHub API Tokens (Optional in Sprint 0/1/2)
    GITHUB_TOKEN: SecretStr | None = None
    OPENAI_API_KEY: SecretStr | None = None
    GEMINI_API_KEY: SecretStr | None = None

    # Sandbox & Execution Configuration
    SANDBOX_TIMEOUT: int = 30
    SANDBOX_MEMORY_LIMIT: str = "512m"
    SANDBOX_CPU_LIMIT: int = 1
    DOCKER_IMAGE: str = "agentforge-sandbox:latest"
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


settings = Settings()
