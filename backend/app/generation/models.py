"""Data models for controlled code generation and patch validation."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from backend.app.planning.models import ChangeType, ImplementationPlan


class GenerationStatus(StrEnum):
    """Lifecycle status of a code generation request."""

    PENDING = "pending"
    GENERATING = "generating"
    GENERATED = "generated"
    VALIDATED = "validated"
    REJECTED = "rejected"
    FAILED = "failed"


class GeneratedFileChange(BaseModel):
    """Structured code change proposed for a repository file."""

    path: str = Field(description="Repository-relative target file path")
    change_type: ChangeType = Field(description="Change operation: create or modify")
    content: str = Field(description="Complete proposed file content")
    task_ids: list[str] = Field(
        default_factory=list, description="Associated task IDs from plan"
    )
    requirement_ids: list[str] = Field(
        default_factory=list, description="Associated requirement IDs"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Generation metadata without secrets"
    )

    @field_validator("change_type")
    @classmethod
    def validate_change_type(cls, v: ChangeType) -> ChangeType:
        """Ensure only create and modify change types are supported for generation."""
        if v not in (ChangeType.CREATE, ChangeType.MODIFY):
            raise ValueError(
                f"Unsupported change type for code generation: '{v}'. "
                "Only 'create' and 'modify' are permitted."
            )
        return v

    @field_validator("path")
    @classmethod
    def validate_path_syntax(cls, v: str) -> str:
        """Validate relative file path syntax."""
        cleaned = v.strip().replace("\\", "/")
        if not cleaned:
            raise ValueError("Target file path cannot be empty.")
        if cleaned.startswith("/"):
            raise ValueError(f"Path must be repository-relative, not absolute: {v}")
        if ":" in cleaned:
            raise ValueError(f"Drive-qualified paths are prohibited: {v}")
        parts = cleaned.split("/")
        if ".." in parts:
            raise ValueError(f"Path traversal ('..') is prohibited: {v}")
        return cleaned


class FileContext(BaseModel):
    """Source context for an authorized target file."""

    path: str = Field(description="Normalized relative path to target file")
    change_type: ChangeType = Field(
        description="Authorized change type: modify, create, or test"
    )
    original_content: str = Field(
        default="", description="Original file content if exists"
    )
    original_hash: str | None = Field(
        default=None, description="SHA256 hash of original file content"
    )
    size_bytes: int = Field(default=0, description="Size of original content in bytes")
    target_symbols: list[str] = Field(
        default_factory=list, description="Targeted symbols for this file"
    )
    exists: bool = Field(default=True, description="Whether file exists on disk")


class SourceContext(BaseModel):
    """Aggregate bounded source context for code generation."""

    repository_path: str = Field(description="Root path of target repository")
    target_files: dict[str, FileContext] = Field(
        default_factory=dict, description="Target file contexts keyed by relative path"
    )
    reference_files: dict[str, str] = Field(
        default_factory=dict, description="Read-only reference file contents"
    )
    total_bytes: int = Field(
        default=0, description="Total size in bytes of gathered context"
    )
    warnings: list[str] = Field(
        default_factory=list, description="Non-blocking context warnings"
    )
    errors: list[str] = Field(
        default_factory=list, description="Blocking context errors"
    )

    @property
    def is_valid(self) -> bool:
        """True if context has no blocking errors."""
        return len(self.errors) == 0


class PatchValidation(BaseModel):
    """Validation report inspecting changes against plan and security bounds."""

    valid: bool = Field(description="True if patch passes all validation checks")
    status: GenerationStatus = Field(description="Resulting generation status")
    errors: list[str] = Field(
        default_factory=list, description="Blocking validation error messages"
    )
    warnings: list[str] = Field(
        default_factory=list, description="Non-blocking warning notices"
    )
    files_checked: list[str] = Field(
        default_factory=list, description="List of repository paths inspected"
    )
    plan_scope_violations: list[str] = Field(
        default_factory=list, description="Out-of-scope files or symbols detected"
    )
    path_safety_findings: list[str] = Field(
        default_factory=list, description="Path security violations detected"
    )


class GenerationRequest(BaseModel):
    """Internal request specification for executing code generation."""

    generation_id: str = Field(description="Unique identifier for generation request")
    plan: ImplementationPlan = Field(description="Validated implementation plan")
    repository_path: str = Field(description="Controlled path to repository root")
    permitted_files: list[str] = Field(
        default_factory=list, description="Explicitly permitted target file paths"
    )
    max_files: int = Field(default=10, description="Max files allowed in generation")
    max_output_bytes: int = Field(
        default=100_000, description="Max output bytes per file"
    )
    timeout_seconds: int = Field(
        default=60, description="Generation timeout in seconds"
    )
    provider_type: str = Field(default="mock", description="Configured provider type")


class GenerationResult(BaseModel):
    """Auditable result artifact of a code generation attempt."""

    generation_id: str = Field(description="Unique generation request identifier")
    status: GenerationStatus = Field(description="Overall generation lifecycle status")
    changes: list[GeneratedFileChange] = Field(
        default_factory=list, description="Proposed file changes"
    )
    validation: PatchValidation = Field(
        description="Validation outcome for the proposed changes"
    )
    patch_content: str = Field(default="", description="Unified diff review artifact")
    patch_file_path: str | None = Field(
        default=None, description="Path to stored patch artifact if persisted"
    )
    provider_type: str = Field(description="Provider used for generation")
    errors: list[str] = Field(
        default_factory=list, description="Errors encountered during pipeline"
    )
    warnings: list[str] = Field(
        default_factory=list, description="Warnings encountered during pipeline"
    )
    created_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
        description="ISO 8601 creation timestamp",
    )
    usage_metadata: dict[str, Any] | None = Field(
        default=None, description="Token/cost usage stats if available"
    )


class CreateGenerationRequest(BaseModel):
    """API request payload to generate code changes for an issue or plan."""

    repository_path: str = Field(description="Filesystem path to repository root")
    plan: ImplementationPlan | None = Field(
        default=None, description="Pre-validated implementation plan if available"
    )
    title: str | None = Field(
        default=None, description="Issue title if plan needs to be built on-the-fly"
    )
    description: str = Field(
        default="", description="Issue description if plan built on-the-fly"
    )
    issue_id: str | None = Field(default=None, description="Optional issue ID")
    provider_type: str | None = Field(
        default=None, description="Optional provider override (e.g., 'mock')"
    )
