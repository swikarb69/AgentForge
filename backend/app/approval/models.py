"""Data models for human approval and controlled release engine."""

import re
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class ApprovalStatus(StrEnum):
    """Lifecycle status of a human approval request."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CHANGES_REQUESTED = "changes_requested"
    EXPIRED = "expired"
    REVOKED = "revoked"
    BLOCKED = "blocked"


class DecisionOutcome(StrEnum):
    """Specific outcome of an individual approval decision."""

    APPROVED = "approved"
    REJECTED = "rejected"
    CHANGES_REQUESTED = "changes_requested"
    REVOKED = "revoked"


class ApprovalRiskLevel(StrEnum):
    """Risk tier evaluating release sensitivity and policy requirements."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ApprovalRole(StrEnum):
    """Role classification of actors interacting with the approval subsystem."""

    REQUESTER = "requester"
    REVIEWER = "reviewer"
    APPROVER = "approver"
    ADMIN = "admin"
    RELEASE_OPERATOR = "release_operator"


HEX_64_PATTERN = re.compile(r"^[a-fA-F0-9]{64}$")
GIT_HASH_PATTERN = re.compile(r"^[a-fA-F0-9]{7,64}$")


class ArtifactBinding(BaseModel):
    """Immutable hash bindings securing approval against upstream artifacts."""

    patch_hash: str = Field(
        description="SHA-256 hash of the generated or repaired patch"
    )
    plan_id: str | None = Field(
        default=None, description="Identifier of the implementation plan"
    )
    plan_hash: str | None = Field(
        default=None, description="SHA-256 hash of the implementation plan"
    )
    verification_id: str | None = Field(
        default=None, description="Identifier of the sandbox verification report"
    )
    verification_hash: str | None = Field(
        default=None, description="SHA-256 hash of sandbox verification report"
    )
    review_id: str | None = Field(
        default=None, description="Identifier of the code review report"
    )
    review_hash: str | None = Field(
        default=None, description="SHA-256 hash of the code review report"
    )
    base_commit: str | None = Field(
        default=None, description="Git commit SHA the patch applies against"
    )
    target_branch: str = Field(
        default="main", description="Target git branch intended for release"
    )

    @field_validator("patch_hash")
    @classmethod
    def validate_patch_hash(cls, value: str) -> str:
        """Validate patch hash is a non-empty 64-char hex SHA-256 string."""
        cleaned = value.strip().lower()
        if not HEX_64_PATTERN.match(cleaned):
            raise ValueError(
                "patch_hash must be a valid 64-character hexadecimal SHA-256 string"
            )
        return cleaned

    @field_validator("plan_hash", "verification_hash", "review_hash")
    @classmethod
    def validate_optional_sha256(cls, value: str | None) -> str | None:
        """Validate optional artifact hashes conform to 64-character SHA-256."""
        if value is None:
            return None
        cleaned = value.strip().lower()
        if not HEX_64_PATTERN.match(cleaned):
            raise ValueError(
                "Artifact hash must be a valid 64-character hexadecimal SHA-256 string"
            )
        return cleaned

    @field_validator("base_commit")
    @classmethod
    def validate_base_commit(cls, value: str | None) -> str | None:
        """Validate optional git commit hash format."""
        if value is None:
            return None
        cleaned = value.strip().lower()
        if not GIT_HASH_PATTERN.match(cleaned):
            raise ValueError("base_commit must be a 7 to 64 character hex string")
        return cleaned


class ApprovalPolicy(BaseModel):
    """Configurable governance policy dictating requirements for release approval."""

    policy_id: str = Field(
        default="default", description="Unique policy configuration identifier"
    )
    name: str = Field(default="Default Approval Policy", description="Policy name")
    minimum_approvers: int = Field(
        default=1, ge=1, description="Minimum distinct approver count required"
    )
    required_roles: list[ApprovalRole] = Field(
        default_factory=lambda: [ApprovalRole.APPROVER],
        description="Roles eligible to satisfy approval threshold",
    )
    separation_of_duties: bool = Field(
        default=True,
        description="True if requester cannot approve their own request",
    )
    block_on_critical_findings: bool = Field(
        default=True,
        description="True if critical security or review findings block approval",
    )
    block_on_unresolved_repairs: bool = Field(
        default=True,
        description="True if unresolved repair failures block approval",
    )
    allow_self_approval: bool = Field(
        default=False,
        description="Explicit override allowing requester to approve own request",
    )
    reapproval_on_material_change: bool = Field(
        default=True,
        description="True if changes to bound hashes invalidate prior approval",
    )
    timeout_hours: float = Field(
        default=24.0,
        gt=0.0,
        description="Duration in hours before an unapproved request expires",
    )
    allowed_target_branches: list[str] = Field(
        default_factory=lambda: ["main", "master", "release/*"],
        description="Permitted release branch patterns",
    )


class ApprovalDecision(BaseModel):
    """Explicit human decision submitted for an approval request."""

    decision_id: str = Field(description="Unique decision record identifier")
    approval_id: str = Field(description="Target approval request identifier")
    actor_id: str = Field(description="Identifier of the human making the decision")
    actor_role: ApprovalRole = Field(description="Role of the actor at decision time")
    outcome: DecisionOutcome = Field(description="Outcome of the decision")
    reason: str | None = Field(
        default=None,
        description="Mandatory justification for rejection, revocation, or changes",
    )
    associated_review_hash: str | None = Field(
        default=None,
        description="SHA-256 hash of review report inspected when deciding",
    )
    associated_patch_hash: str | None = Field(
        default=None,
        description="SHA-256 hash of patch inspected when deciding",
    )
    timestamp: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
        description="ISO 8601 decision timestamp",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Supplementary contextual metadata"
    )

    @field_validator("decision_id", "approval_id", "actor_id")
    @classmethod
    def validate_non_empty_identifiers(cls, value: str) -> str:
        """Ensure critical identifiers are non-empty strings."""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Identifier must not be empty or whitespace")
        return cleaned

    @field_validator("associated_patch_hash", "associated_review_hash")
    @classmethod
    def validate_decision_hashes(cls, value: str | None) -> str | None:
        """Validate optional associated artifact hashes."""
        if value is None:
            return None
        cleaned = value.strip().lower()
        if not HEX_64_PATTERN.match(cleaned):
            raise ValueError("Hash must be a valid 64-character hex SHA-256 string")
        return cleaned

    @model_validator(mode="after")
    def validate_reason_requirements(self) -> "ApprovalDecision":
        """Enforce mandatory reasons for negative or disruptive decisions."""
        disruptive_outcomes = {
            DecisionOutcome.REJECTED,
            DecisionOutcome.CHANGES_REQUESTED,
            DecisionOutcome.REVOKED,
        }
        if self.outcome in disruptive_outcomes:
            if not self.reason or not self.reason.strip():
                raise ValueError(
                    f"A non-empty reason is required for outcome '{self.outcome.value}'"
                )
        return self


class ApprovalAuditEvent(BaseModel):
    """Immutable audit log entry recording state transitions and actions."""

    event_id: str = Field(description="Unique audit event identifier")
    approval_id: str = Field(description="Target approval request identifier")
    event_type: str = Field(
        description="Type of event (e.g. created, approved, rejected, expired)"
    )
    actor_id: str = Field(description="Actor initiating the action")
    timestamp: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
        description="ISO 8601 event timestamp",
    )
    details: dict[str, Any] = Field(
        default_factory=dict, description="Event payload and contextual details"
    )


class ApprovalRequest(BaseModel):
    """Complete governance request binding upstream artifacts to human approval."""

    approval_id: str = Field(description="Unique approval request identifier")
    repository_id: str = Field(description="Target repository identifier or path")
    release_candidate_id: str = Field(description="Unique release candidate identifier")
    issue_id: str | None = Field(
        default=None, description="Associated upstream issue identifier"
    )
    plan_id: str | None = Field(
        default=None, description="Associated implementation plan identifier"
    )
    generation_id: str | None = Field(
        default=None, description="Associated code generation identifier"
    )
    execution_id: str | None = Field(
        default=None, description="Associated sandbox execution identifier"
    )
    repair_id: str | None = Field(
        default=None, description="Associated repair session identifier"
    )
    review_id: str | None = Field(
        default=None, description="Associated code review identifier"
    )
    requested_by: str = Field(
        description="Actor or agent requesting release authorization"
    )
    required_approvers: int = Field(
        default=1,
        ge=1,
        description="Effective minimum distinct approver count",
    )
    approval_policy: ApprovalPolicy = Field(
        default_factory=ApprovalPolicy,
        description="Policy governing authorization requirements",
    )
    risk_level: ApprovalRiskLevel = Field(
        default=ApprovalRiskLevel.LOW,
        description="Assessed risk level of the release candidate",
    )
    status: ApprovalStatus = Field(
        default=ApprovalStatus.PENDING,
        description="Current lifecycle status of the approval request",
    )
    artifact_binding: ArtifactBinding = Field(
        description="Immutable cryptographic bindings to candidate artifacts"
    )
    decisions: list[ApprovalDecision] = Field(
        default_factory=list,
        description="Chronological log of decisions submitted for this request",
    )
    audit_events: list[ApprovalAuditEvent] = Field(
        default_factory=list,
        description="Chronological immutable audit log of request lifecycle",
    )
    blocking_reasons: list[str] = Field(
        default_factory=list,
        description="Active blockers preventing approval or release",
    )
    created_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
        description="ISO 8601 creation timestamp",
    )
    updated_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
        description="ISO 8601 update timestamp",
    )
    expires_at: str | None = Field(
        default=None,
        description="ISO 8601 timestamp after which the request automatically expires",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Supplementary contextual metadata",
    )

    @field_validator(
        "approval_id", "repository_id", "release_candidate_id", "requested_by"
    )
    @classmethod
    def validate_non_empty_strings(cls, value: str) -> str:
        """Validate critical string fields are not blank."""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Field must not be empty or whitespace")
        return cleaned

    @model_validator(mode="after")
    def compute_defaults_and_expiration(self) -> "ApprovalRequest":
        """Sync required approvers with policy and compute expiration if omitted."""
        if (
            self.approval_policy.minimum_approvers
            and self.required_approvers < self.approval_policy.minimum_approvers
        ):
            self.required_approvers = self.approval_policy.minimum_approvers

        if self.expires_at is None:
            try:
                created_dt = datetime.fromisoformat(self.created_at)
            except Exception:
                created_dt = datetime.now(UTC)
            expiry_dt = created_dt + timedelta(hours=self.approval_policy.timeout_hours)
            self.expires_at = expiry_dt.isoformat()

        return self
