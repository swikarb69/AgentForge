"""Data models for secure execution and verification sandbox."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from backend.app.generation.models import GenerationResult


class ExecutionStatus(StrEnum):
    """Lifecycle status of a sandboxed execution request."""

    PENDING = "pending"
    PREPARING = "preparing"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class VerificationProfile(StrEnum):
    """Predefined, permitted verification profiles."""

    PYTHON_UNIT_TESTS = "python-unit-tests"
    PYTHON_QUALITY_CHECKS = "python-quality-checks"


class TestSummary(BaseModel):
    """Parsed outcome summary of test execution."""

    __test__: bool = False

    total: int = Field(default=0, ge=0, description="Total tests collected")
    passed: int = Field(default=0, ge=0, description="Number of passed tests")
    failed: int = Field(default=0, ge=0, description="Number of failed tests")
    skipped: int = Field(default=0, ge=0, description="Number of skipped tests")
    errors: int = Field(
        default=0, ge=0, description="Number of test setup/collection errors"
    )

    @property
    def is_all_passed(self) -> bool:
        """True if tests ran, at least one test executed, and zero failures/errors."""
        return self.total > 0 and self.failed == 0 and self.errors == 0


class WorkspaceMetadata(BaseModel):
    """Metadata describing an isolated ephemeral execution workspace."""

    workspace_id: str = Field(description="Unique workspace identifier")
    workspace_path: str = Field(description="Absolute path to workspace on host")
    created_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
        description="ISO 8601 creation timestamp",
    )
    cleaned_up: bool = Field(default=False, description="True if workspace was deleted")
    file_count: int = Field(
        default=0, ge=0, description="Total files copied to workspace"
    )
    size_bytes: int = Field(
        default=0, ge=0, description="Total size of workspace in bytes"
    )


class ResourceUsage(BaseModel):
    """Container resource usage metrics where available."""

    cpu_percent: float | None = Field(
        default=None, description="Average CPU utilization percentage"
    )
    memory_bytes: int | None = Field(default=None, description="Memory used in bytes")
    peak_memory_bytes: int | None = Field(
        default=None, description="Peak memory used in bytes"
    )
    duration_seconds: float | None = Field(
        default=None, description="Execution wall-clock duration in seconds"
    )


class ExecutionPolicy(BaseModel):
    """Security and resource constraint policy for containerized execution."""

    timeout_seconds: int = Field(
        default=30, ge=1, le=300, description="Max execution duration in seconds"
    )
    memory_limit: str = Field(
        default="512m", description="Container memory limit string (e.g., '512m')"
    )
    cpu_limit: float = Field(
        default=1.0, ge=0.1, le=4.0, description="Container CPU cores limit"
    )
    process_limit: int = Field(
        default=64, ge=1, le=256, description="Max process/pid limit in container"
    )
    maximum_output_bytes: int = Field(
        default=100_000,
        ge=1024,
        le=1_000_000,
        description="Max stdout/stderr bytes captured",
    )
    network_enabled: bool = Field(
        default=False, description="Whether network access is allowed (default False)"
    )
    read_only_root_filesystem: bool = Field(
        default=True, description="Enforce read-only container rootfs"
    )
    drop_capabilities: list[str] = Field(
        default_factory=lambda: ["ALL"],
        description="Linux capabilities to drop from container",
    )
    allowed_commands: list[str] = Field(
        default_factory=lambda: [
            "pytest",
            "python",
            "python3",
            "git",
            "ruff",
        ],
        description="Allowlisted command binaries permitted to execute",
    )
    denied_command_patterns: list[str] = Field(
        default_factory=lambda: [
            "sudo",
            "rm -rf /",
            "curl | bash",
            "wget | bash",
            "cat /etc/passwd",
            "cat /etc/shadow",
            "chmod -R 777",
            ":(){ :|:& };:",
        ],
        description="Forbidden command patterns and injection attacks",
    )
    permitted_environment_variables: list[str] = Field(
        default_factory=lambda: [
            "PYTHONPATH",
            "PYTHONUNBUFFERED",
            "PYTHONDONTWRITEBYTECODE",
            "TERM",
            "LANG",
            "PATH",
        ],
        description="Allowlist of environment variables passed to container",
    )
    allowed_verification_profiles: list[str] = Field(
        default_factory=lambda: [
            "python-unit-tests",
            "python-quality-checks",
        ],
        description="Permitted verification profile identifiers",
    )

    @field_validator("memory_limit")
    @classmethod
    def validate_memory_limit(cls, v: str) -> str:
        """Validate memory limit string format e.g. '256m', '512m', '1g'."""
        cleaned = v.strip().lower()
        if not (
            cleaned.endswith("m") or cleaned.endswith("g") or cleaned.endswith("k")
        ):
            raise ValueError(f"Memory limit must end with 'k', 'm', or 'g' unit: {v}")
        numeric_part = cleaned[:-1]
        if not numeric_part.isdigit() or int(numeric_part) <= 0:
            raise ValueError(f"Invalid numeric value in memory limit: {v}")
        return cleaned


class ExecutionRequest(BaseModel):
    """Structured request specification for executing tests inside a sandbox."""

    execution_id: str = Field(description="Unique execution run identifier")
    generation_id: str | None = Field(
        default=None, description="Associated Sprint 4 generation ID if present"
    )
    generation_result: GenerationResult | None = Field(
        default=None, description="Pre-loaded generation result to verify"
    )
    repository_path: str = Field(
        description="Filesystem path to source repository snapshot"
    )
    requested_verification_profile: VerificationProfile = Field(
        default=VerificationProfile.PYTHON_UNIT_TESTS,
        description="Predefined verification profile to execute",
    )
    policy: ExecutionPolicy = Field(
        default_factory=ExecutionPolicy,
        description="Execution resource and security constraints",
    )
    patch_content: str | None = Field(
        default=None,
        description="Direct unified diff patch text if generation result is absent",
    )
    patch_file_path: str | None = Field(
        default=None, description="Path to patch artifact file if persisted"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Audit and request metadata"
    )


class ExecutionResult(BaseModel):
    """Low-level execution result from running commands inside the sandbox."""

    execution_id: str = Field(description="Unique execution request identifier")
    status: ExecutionStatus = Field(description="Overall execution status")
    exit_code: int | None = Field(
        default=None, description="Process exit code (0 indicates success)"
    )
    started_at: str | None = Field(
        default=None, description="ISO 8601 execution start timestamp"
    )
    completed_at: str | None = Field(
        default=None, description="ISO 8601 execution completion timestamp"
    )
    duration_seconds: float | None = Field(
        default=None, description="Duration of container execution in seconds"
    )
    stdout: str = Field(default="", description="Captured standard output text")
    stderr: str = Field(default="", description="Captured standard error text")
    test_summary: TestSummary | None = Field(
        default=None, description="Parsed test results summary if tests ran"
    )
    workspace_metadata: WorkspaceMetadata | None = Field(
        default=None, description="Workspace isolation details"
    )
    resource_usage: ResourceUsage | None = Field(
        default=None, description="Metrics on CPU and memory consumption"
    )
    errors: list[str] = Field(
        default_factory=list, description="Errors encountered during execution"
    )
    warnings: list[str] = Field(
        default_factory=list, description="Warnings encountered during execution"
    )


class VerificationReport(BaseModel):
    """High-level verification report synthesizing sandbox outcomes."""

    execution_id: str = Field(description="Unique execution identifier")
    execution_success: bool = Field(
        default=False, description="True if container execution exited with 0"
    )
    patch_applied: bool = Field(
        default=False, description="True if patch was applied cleanly"
    )
    tests_executed: bool = Field(
        default=False, description="True if test runner was successfully executed"
    )
    verification_passed: bool = Field(
        default=False, description="True if all verification tests passed"
    )
    failures: list[str] = Field(
        default_factory=list, description="Reasons why verification failed"
    )
    warnings: list[str] = Field(
        default_factory=list, description="Non-blocking verification warnings"
    )
    execution_result: ExecutionResult | None = Field(
        default=None, description="Detailed low-level execution outcome"
    )
    created_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
        description="ISO 8601 report timestamp",
    )


class CreateExecutionRequest(BaseModel):
    """API request payload to execute and verify a proposed patch."""

    repository_path: str = Field(description="Path to target repository")
    generation_id: str | None = Field(
        default=None, description="Generation ID to load patch from"
    )
    generation_result: GenerationResult | None = Field(
        default=None, description="Pre-loaded generation result to verify"
    )
    verification_profile: VerificationProfile = Field(
        default=VerificationProfile.PYTHON_UNIT_TESTS,
        description="Verification profile to run",
    )
    policy: ExecutionPolicy | None = Field(
        default=None, description="Optional custom policy override"
    )
    patch_content: str | None = Field(
        default=None, description="Optional raw patch text override"
    )
