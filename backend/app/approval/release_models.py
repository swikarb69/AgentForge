"""Data models for controlled release operations and pull request tracking."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ReleaseStatus(StrEnum):
    """Lifecycle status of a controlled release execution."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PullRequestMetadata(BaseModel):
    """Metadata describing a human-reviewable pull request created by release engine."""

    pr_number: int = Field(description="Pull request issue number on repository")
    title: str = Field(description="Pull request title summarizing changes")
    body: str = Field(description="Pull request markdown description and audit links")
    source_branch: str = Field(
        description="Release branch containing validated changes"
    )
    target_branch: str = Field(description="Target branch (e.g. main) for pull request")
    pr_url: str = Field(description="Web URL to inspect pull request on code forge")
    is_merged: bool = Field(
        default=False, description="True if pull request has been merged"
    )
    is_closed: bool = Field(
        default=False, description="True if pull request is closed without merge"
    )


class ReleaseExecutionRecord(BaseModel):
    """Complete auditable record of a controlled release operation."""

    release_id: str = Field(description="Unique release operation identifier")
    candidate_id: str = Field(description="Associated release candidate identifier")
    approval_id: str = Field(description="Authorized approval request identifier")
    repository_id: str = Field(description="Target repository identifier or path")
    target_branch: str = Field(description="Base branch intended for release")
    release_branch: str = Field(
        description="Release candidate branch created for the pull request"
    )
    status: ReleaseStatus = Field(
        default=ReleaseStatus.PENDING,
        description="Execution status of release operation",
    )
    commit_sha: str | None = Field(
        default=None,
        description="Git commit hash created on release candidate branch",
    )
    pull_request: PullRequestMetadata | None = Field(
        default=None,
        description="Pull request created for human review and final merge",
    )
    dry_run: bool = Field(
        default=False,
        description="True if release operation simulated without network side-effects",
    )
    executed_by: str = Field(description="Actor ID authorizing and executing release")
    started_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
        description="ISO 8601 start timestamp",
    )
    completed_at: str | None = Field(
        default=None, description="ISO 8601 completion timestamp"
    )
    error_message: str | None = Field(
        default=None, description="Error explanation if release failed"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Supplementary release metadata"
    )
