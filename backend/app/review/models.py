"""Data models for code review and quality assurance engine."""

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class FindingCategory(StrEnum):
    """Categories classifying code review findings."""

    CORRECTNESS = "correctness"
    SECURITY = "security"
    RELIABILITY = "reliability"
    MAINTAINABILITY = "maintainability"
    PERFORMANCE = "performance"
    ARCHITECTURE = "architecture"
    REGRESSION = "regression"
    TESTING = "testing"
    CONFIGURATION = "configuration"


class FindingSeverity(StrEnum):
    """Severity ratings for code review findings."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFORMATIONAL = "informational"


class FindingConfidence(StrEnum):
    """Confidence levels indicating certainty of a finding."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ReviewStatus(StrEnum):
    """Lifecycle status of a code review execution."""

    PENDING = "pending"
    ANALYZING = "analyzing"
    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"
    FAILED = "failed"
    BLOCKED = "blocked"


class ReviewScope(StrEnum):
    """Scope defining files subjected to review."""

    PATCH_ONLY = "patch_only"
    AFFECTED_FILES = "affected_files"
    FULL_REPOSITORY = "full_repository"


# Regex patterns for detecting and redacting sensitive credentials in code excerpts
SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"""(?i)(api[_-]?key|secret|token|password|passwd|auth[_-]?token)"""
            r"""\s*[:=]\s*['"]([^'"]{8,})['"]"""
        ),
        r'\1="[REDACTED]"',
    ),
    (
        re.compile(r"""gh[pousr]_[A-Za-z0-9_]{36,255}"""),
        "[REDACTED_GITHUB_TOKEN]",
    ),
    (
        re.compile(r"""AKIA[0-9A-Z]{16}"""),
        "[REDACTED_AWS_KEY]",
    ),
    (
        re.compile(r"""(?i)bearer\s+[a-zA-Z0-9_\-\.]{15,}"""),
        "Bearer [REDACTED_BEARER_TOKEN]",
    ),
]


def redact_secrets(text: str) -> str:
    """Redact sensitive API keys, tokens, and passwords from code excerpts."""
    redacted = text
    for pattern, replacement in SECRET_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


class ReviewConfiguration(BaseModel):
    """Configuration options governing code review behavior and gating."""

    enabled_categories: list[FindingCategory] = Field(
        default_factory=lambda: list(FindingCategory),
        description="Categories enabled for analysis",
    )
    disabled_rule_ids: list[str] = Field(
        default_factory=list,
        description="Explicit rule IDs to skip during evaluation",
    )
    min_severity: FindingSeverity = Field(
        default=FindingSeverity.INFORMATIONAL,
        description="Minimum severity threshold for emitted findings",
    )
    max_findings_per_file: int = Field(
        default=50,
        ge=1,
        le=500,
        description="Upper limit on findings per file",
    )
    max_excerpt_lines: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum lines included in evidence excerpts",
    )
    max_file_size_bytes: int = Field(
        default=500_000,
        ge=1024,
        le=10_000_000,
        description="Maximum file size in bytes subjected to static review",
    )
    allow_llm_explanations: bool = Field(
        default=False,
        description="Allow optional LLM provider assistance for explanations",
    )
    block_on_critical: bool = Field(
        default=True,
        description="Automatically block approval if any critical finding exists",
    )
    require_human_on_high: bool = Field(
        default=True,
        description="Require explicit human review if any high-severity finding exists",
    )


class ReviewRequest(BaseModel):
    """Specification of an incoming code review request."""

    review_id: str = Field(description="Unique identifier for the review session")
    repository_path: str = Field(description="Filesystem path to repository root")
    repository_id: str | None = Field(
        default=None, description="Optional logical repository identifier"
    )
    generation_id: str | None = Field(
        default=None, description="Sprint 4 generation ID if applicable"
    )
    execution_id: str | None = Field(
        default=None, description="Sprint 5 execution ID if applicable"
    )
    repair_id: str | None = Field(
        default=None, description="Sprint 6 repair ID if applicable"
    )
    plan_id: str | None = Field(
        default=None, description="Sprint 3 implementation plan ID if applicable"
    )
    issue_id: str | None = Field(
        default=None, description="Sprint 2 issue ID if applicable"
    )
    patch_content: str | None = Field(
        default=None, description="Direct unified diff patch text if available"
    )
    review_scope: ReviewScope = Field(
        default=ReviewScope.PATCH_ONLY, description="Scope of code to review"
    )
    config: ReviewConfiguration = Field(
        default_factory=ReviewConfiguration, description="Review engine configuration"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Audit and request metadata"
    )

    @field_validator("repository_path")
    @classmethod
    def validate_repository_path(cls, v: str) -> str:
        """Validate repository path is non-empty."""
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("repository_path cannot be empty.")
        return cleaned


class CodeEvidence(BaseModel):
    """Bounded, validated evidence linking a finding to source code."""

    evidence_id: str = Field(description="Unique identifier for evidence item")
    file_path: str = Field(description="Repository-relative path of affected file")
    start_line: int = Field(ge=1, description="1-indexed starting line number")
    end_line: int = Field(ge=1, description="1-indexed ending line number (inclusive)")
    code_excerpt: str = Field(description="Bounded excerpt of code evidencing issue")
    evidence_type: str = Field(
        default="code_snippet", description="Classification of evidence type"
    )
    related_rule: str | None = Field(
        default=None, description="Associated rule ID if applicable"
    )
    source_artifact: str | None = Field(
        default=None, description="Source artifact name or identifier"
    )
    content_hash: str | None = Field(
        default=None, description="SHA-256 hash of reviewed code excerpt"
    )

    @field_validator("file_path")
    @classmethod
    def validate_file_path(cls, v: str) -> str:
        """Ensure file path is relative and does not traverse parent directories."""
        cleaned = v.strip().replace("\\", "/")
        if not cleaned:
            raise ValueError("file_path cannot be empty.")
        if cleaned.startswith("/"):
            raise ValueError(f"Path must be repository-relative, not absolute: {v}")
        if ":" in cleaned:
            raise ValueError(f"Drive-qualified paths are prohibited: {v}")
        parts = cleaned.split("/")
        if ".." in parts:
            raise ValueError(f"Path traversal ('..') is prohibited: {v}")
        return cleaned

    @field_validator("code_excerpt")
    @classmethod
    def sanitize_code_excerpt(cls, v: str) -> str:
        """Sanitize excerpt by redacting sensitive secrets and bounding length."""
        redacted = redact_secrets(v)
        # Bounded maximum length of 2000 characters
        if len(redacted) > 2000:
            return redacted[:1997] + "..."
        return redacted

    @model_validator(mode="after")
    def validate_line_range(self) -> "CodeEvidence":
        """Verify start_line <= end_line."""
        if self.start_line > self.end_line:
            raise ValueError(
                f"start_line ({self.start_line}) cannot be greater than "
                f"end_line ({self.end_line})."
            )
        return self


class ReviewFinding(BaseModel):
    """Specific defect, security concern, or quality finding from code review."""

    finding_id: str = Field(description="Unique finding identifier")
    rule_id: str = Field(description="Identifier of triggered rule")
    title: str = Field(description="Short human-readable summary of finding")
    description: str = Field(description="Detailed explanation of finding")
    category: FindingCategory = Field(description="Functional finding category")
    severity: FindingSeverity = Field(description="Assigned severity rating")
    confidence: FindingConfidence = Field(description="Confidence in finding accuracy")
    file_path: str = Field(description="Repository-relative path of affected file")
    start_line: int = Field(ge=1, description="1-indexed starting line")
    end_line: int = Field(ge=1, description="1-indexed ending line")
    affected_symbol: str | None = Field(
        default=None, description="Symbol name implicated in finding"
    )
    evidence: CodeEvidence = Field(description="Grounding code evidence for finding")
    impact: str = Field(default="", description="Potential impact if unaddressed")
    remediation: str = Field(default="", description="Recommended remediation guidance")
    source: str = Field(
        default="static_analysis",
        description="Source: static_analysis, security, architecture, regression",
    )
    requires_human_review: bool = Field(
        default=False,
        description="True if finding necessitates explicit human review/decision",
    )

    @field_validator("file_path")
    @classmethod
    def validate_file_path(cls, v: str) -> str:
        """Ensure file path is relative and clean."""
        cleaned = v.strip().replace("\\", "/")
        if not cleaned:
            raise ValueError("file_path cannot be empty.")
        if cleaned.startswith("/"):
            raise ValueError(f"Path must be repository-relative: {v}")
        if ".." in cleaned.split("/"):
            raise ValueError(f"Path traversal is prohibited: {v}")
        return cleaned

    @model_validator(mode="after")
    def validate_lines_and_evidence(self) -> "ReviewFinding":
        """Validate line range consistency with evidence."""
        if self.start_line > self.end_line:
            raise ValueError(
                f"start_line ({self.start_line}) cannot exceed "
                f"end_line ({self.end_line})."
            )
        return self


class ReviewSummary(BaseModel):
    """Aggregate statistical summary and approval gating for review findings."""

    total_findings: int = Field(
        default=0, ge=0, description="Total findings identified"
    )
    findings_by_severity: dict[str, int] = Field(
        default_factory=dict, description="Counts keyed by severity name"
    )
    findings_by_category: dict[str, int] = Field(
        default_factory=dict, description="Counts keyed by category name"
    )
    files_reviewed: list[str] = Field(
        default_factory=list, description="List of files inspected"
    )
    files_with_findings: list[str] = Field(
        default_factory=list,
        description="List of files containing one or more findings",
    )
    critical_findings: int = Field(
        default=0, ge=0, description="Count of critical severity findings"
    )
    high_risk_findings: int = Field(
        default=0, ge=0, description="Count of high severity findings"
    )
    review_status: ReviewStatus = Field(
        default=ReviewStatus.PENDING, description="Overall review status"
    )
    approval_recommended: bool = Field(
        default=True,
        description="True if code changes meet quality and security criteria",
    )
    blocking_reasons: list[str] = Field(
        default_factory=list,
        description="Explanations if approval recommendation is blocked",
    )
    quality_score: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Explainable 0-100 quality score if computed",
    )


class ReviewReport(BaseModel):
    """Complete auditable report synthesizing all code review findings and gating."""

    review_id: str = Field(description="Unique review run identifier")
    repository_path: str = Field(description="Path to reviewed repository")
    repository_info: dict[str, Any] = Field(
        default_factory=dict, description="Repository metadata (e.g. branch, commit)"
    )
    reviewed_artifacts: dict[str, str | None] = Field(
        default_factory=dict,
        description="References to reviewed artifacts (generation, execution, repair)",
    )
    status: ReviewStatus = Field(
        default=ReviewStatus.PENDING, description="Review execution status"
    )
    summary: ReviewSummary = Field(
        default_factory=ReviewSummary,
        description="Aggregate metrics and gating summary",
    )
    findings: list[ReviewFinding] = Field(
        default_factory=list, description="Ordered list of review findings"
    )
    analysis_metadata: dict[str, Any] = Field(
        default_factory=dict, description="Metadata on analyzers executed and runtimes"
    )
    limitations: list[str] = Field(
        default_factory=list,
        description="Known limitations or caveats of the analysis",
    )
    generated_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
        description="ISO 8601 generation timestamp",
    )
