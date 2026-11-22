"""Data models and enums for the Debugging and Automatic Repair Engine."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from backend.app.execution.models import (
    ExecutionStatus,
    TestSummary,
    VerificationReport,
)
from backend.app.generation.models import GeneratedFileChange, PatchValidation


class FailureCategory(StrEnum):
    """Classification of test, execution, or environment failure."""

    TEST_FAILURE = "test_failure"
    IMPORT_ERROR = "import_error"
    SYNTAX_ERROR = "syntax_error"
    TYPE_ERROR = "type_error"
    LINT_ERROR = "lint_error"
    DEPENDENCY_ERROR = "dependency_error"
    CONFIGURATION_ERROR = "configuration_error"
    TIMEOUT = "timeout"
    RESOURCE_EXHAUSTION = "resource_exhaustion"
    PATCH_APPLICATION_ERROR = "patch_application_error"
    ENVIRONMENT_ERROR = "environment_error"
    UNKNOWN = "unknown"


class RootCauseType(StrEnum):
    """Categorized root causes identified for execution failures."""

    INCORRECT_LOGIC = "incorrect_logic"
    INCORRECT_SIGNATURE = "incorrect_signature"
    MISSING_IMPORT = "missing_import"
    TYPE_MISMATCH = "type_mismatch"
    MISSING_DEPENDENCY = "missing_dependency"
    INVALID_CONFIGURATION = "invalid_configuration"
    INCORRECT_FILE_PATH = "incorrect_file_path"
    BROKEN_INTEGRATION = "broken_integration"
    TEST_EXPECTATION_MISMATCH = "test_expectation_mismatch"
    ENVIRONMENT_ISSUE = "environment_issue"
    UNKNOWN_CAUSE = "unknown_cause"


class RepairStrategyType(StrEnum):
    """Targeted strategy for repairing diagnosed defects."""

    ADD_OR_CORRECT_IMPORT = "add_or_correct_import"
    UPDATE_SIGNATURE_AND_CALLERS = "update_signature_and_callers"
    CORRECT_TYPE_HANDLING = "correct_type_handling"
    MODIFY_AFFECTED_LOGIC = "modify_affected_logic"
    RECOMMEND_DEPENDENCY_UPDATE = "recommend_dependency_update"
    PROPOSE_CONFIGURATION_CORRECTION = "propose_configuration_correction"
    STOP_ENVIRONMENT_FAILURE = "stop_environment_failure"
    STOP_INFRASTRUCTURE_ISSUE = "stop_infrastructure_issue"
    REQUEST_HUMAN_REVIEW = "request_human_review"


class RiskLevel(StrEnum):
    """Risk rating for proposed repair operations."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RepairState(StrEnum):
    """Explicit state machine states for the bounded automatic repair controller."""

    PENDING = "pending"
    ANALYZING = "analyzing"
    DIAGNOSING = "diagnosing"
    AWAITING_APPROVAL = "awaiting_approval"
    GENERATING_REPAIR = "generating_repair"
    VALIDATING_REPAIR = "validating_repair"
    EXECUTING_REPAIR = "executing_repair"
    EVALUATING = "evaluating"
    SUCCEEDED = "succeeded"
    PARTIALLY_SUCCEEDED = "partially_succeeded"
    FAILED = "failed"
    NEEDS_HUMAN_REVIEW = "needs_human_review"
    CANCELLED = "cancelled"
    MAX_ATTEMPTS_REACHED = "max_attempts_reached"


class RepairEvaluationVerdict(StrEnum):
    """Outcome verdict comparing pre-repair and post-repair execution."""

    FIXED = "fixed"
    PARTIALLY_FIXED = "partially_fixed"
    NOT_FIXED = "not_fixed"
    REGRESSION_DETECTED = "regression_detected"
    INCONCLUSIVE = "inconclusive"
    NEEDS_HUMAN_REVIEW = "needs_human_review"


class FailureClassification(BaseModel):
    """Structured classification of execution failure evidence."""

    category: FailureCategory = Field(description="Primary failure category")
    confidence: float = Field(
        default=1.0, ge=0.0, le=1.0, description="Classification confidence (0 to 1)"
    )
    evidence_references: list[str] = Field(
        default_factory=list, description="Log lines, symbols, or test names cited"
    )
    error_messages: list[str] = Field(
        default_factory=list, description="Extracted error messages"
    )
    explanation: str = Field(
        default="", description="Human-readable explanation of failure category"
    )
    auto_repair_permitted: bool = Field(
        default=True,
        description="Whether automatic code repair is allowed for this failure",
    )
    human_intervention_required: bool = Field(
        default=False,
        description="Whether human intervention is required before proceeding",
    )


class DiagnosticEvidence(BaseModel):
    """Sanitized and bounded evidence collected from execution runs."""

    execution_id: str = Field(description="Sprint 5 execution ID")
    execution_status: ExecutionStatus = Field(description="Overall execution status")
    test_summary: TestSummary | None = Field(
        default=None, description="Test summary from execution if present"
    )
    failed_test_names: list[str] = Field(
        default_factory=list, description="Names of failed test functions"
    )
    error_messages: list[str] = Field(
        default_factory=list, description="Captured exception or error lines"
    )
    traceback_snippets: list[str] = Field(
        default_factory=list, description="Bounded traceback frames"
    )
    relevant_file_paths: list[str] = Field(
        default_factory=list, description="File paths implicated in stack trace"
    )
    relevant_symbols: list[str] = Field(
        default_factory=list, description="Symbols implicated in failure"
    )
    patch_summary: str | None = Field(
        default=None, description="Summary of patch that was under test"
    )
    previous_attempt_count: int = Field(
        default=0, description="Number of previous repair attempts"
    )
    plan_id: str | None = Field(default=None, description="Plan ID associated with run")
    acceptance_criteria: list[str] = Field(
        default_factory=list, description="Criteria that must be satisfied"
    )
    truncated: bool = Field(
        default=False, description="True if evidence was truncated due to size limits"
    )
    truncation_reasons: list[str] = Field(
        default_factory=list, description="Specific details on what was truncated"
    )
    sanitized_secret_count: int = Field(
        default=0, description="Count of detected secrets redacted from evidence"
    )


class RootCauseAnalysis(BaseModel):
    """Diagnosed root cause for execution failure."""

    primary_cause: RootCauseType = Field(description="Primary diagnosed root cause")
    alternative_causes: list[RootCauseType] = Field(
        default_factory=list, description="Secondary or alternative possible causes"
    )
    confidence: float = Field(
        default=1.0, ge=0.0, le=1.0, description="Diagnosis confidence rating"
    )
    supporting_evidence: list[str] = Field(
        default_factory=list, description="Facts or traces supporting diagnosis"
    )
    affected_files: list[str] = Field(
        default_factory=list, description="Repository files that require remediation"
    )
    affected_symbols: list[str] = Field(
        default_factory=list, description="Specific symbols requiring repair"
    )
    related_requirements: list[str] = Field(
        default_factory=list, description="Requirement IDs impacted"
    )
    related_acceptance_criteria: list[str] = Field(
        default_factory=list, description="Acceptance criteria IDs impacted"
    )
    is_repairable_automatically: bool = Field(
        default=True, description="True if automated code repair can remediate"
    )
    explanation: str = Field(
        default="", description="Detailed explanation of the root cause"
    )
    uncertainty_notes: str | None = Field(
        default=None, description="Notes on diagnostic ambiguity or uncertainty"
    )


class RepairStrategy(BaseModel):
    """Selected strategy and boundaries for automated code repair."""

    strategy_type: RepairStrategyType = Field(description="Type of repair strategy")
    reason: str = Field(description="Rationale for choosing strategy")
    target_files: list[str] = Field(
        default_factory=list, description="Authorized target files to modify"
    )
    target_symbols: list[str] = Field(
        default_factory=list, description="Target symbols to modify or add"
    )
    expected_changes: list[str] = Field(
        default_factory=list, description="Expected nature of code changes"
    )
    risk_level: RiskLevel = Field(
        default=RiskLevel.MEDIUM, description="Architectural risk assessment"
    )
    required_evidence: list[str] = Field(
        default_factory=list, description="Prerequisite evidence required"
    )
    code_generation_permitted: bool = Field(
        default=True, description="True if code generation is allowed"
    )
    human_approval_required: bool = Field(
        default=False, description="True if human approval is mandatory before apply"
    )


class RepairProposal(BaseModel):
    """Proposed code repair changes and validation status."""

    repair_id: str = Field(description="Unique identifier for repair run")
    attempt_number: int = Field(
        default=1, ge=1, description="Sequential attempt number"
    )
    strategy: RepairStrategy = Field(description="Repair strategy employed")
    root_cause: RootCauseAnalysis = Field(description="Root cause being addressed")
    file_changes: list[GeneratedFileChange] = Field(
        default_factory=list, description="Proposed file modifications"
    )
    patch_content: str = Field(
        default="", description="Unified diff text of proposed repair"
    )
    explanation: str = Field(
        default="", description="Explanation of what was changed and why"
    )
    expected_behavior: str = Field(
        default="", description="Expected outcome after applying repair"
    )
    verification_criteria: list[str] = Field(
        default_factory=list, description="Criteria used to verify fix"
    )
    validation: PatchValidation | None = Field(
        default=None, description="Patch validation report"
    )
    created_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
        description="ISO 8601 creation timestamp",
    )


class RepairEvaluation(BaseModel):
    """Evaluation comparing pre-repair and post-repair execution outcomes."""

    verdict: RepairEvaluationVerdict = Field(description="Evaluation verdict")
    original_failures_resolved: bool = Field(
        default=False, description="True if original failure did not reoccur"
    )
    remaining_failures: list[str] = Field(
        default_factory=list, description="Failures still present after repair"
    )
    new_failures_introduced: list[str] = Field(
        default_factory=list, description="New failures introduced by repair"
    )
    regressions_detected: bool = Field(
        default=False, description="True if previously passing tests now fail"
    )
    acceptance_criteria_verified: list[str] = Field(
        default_factory=list, description="Acceptance criteria verified"
    )
    unverified_criteria: list[str] = Field(
        default_factory=list, description="Criteria not yet verified"
    )
    scope_preserved: bool = Field(
        default=True, description="True if repair stayed within authorized scope"
    )
    summary: str = Field(
        default="", description="Overall evaluation summary description"
    )
    duration_seconds: float = Field(
        default=0.0, description="Duration of evaluation check in seconds"
    )


class StateTransition(BaseModel):
    """Audit log entry capturing a repair state machine transition."""

    from_state: RepairState = Field(description="State transitioning from")
    to_state: RepairState = Field(description="State transitioning to")
    timestamp: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
        description="ISO 8601 transition timestamp",
    )
    reason: str = Field(
        default="", description="Explanation or event triggering transition"
    )


class RepairAttempt(BaseModel):
    """Single complete iteration in the repair loop."""

    attempt_number: int = Field(
        default=1, ge=1, description="Sequential attempt number"
    )
    started_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
        description="ISO 8601 start timestamp",
    )
    completed_at: str | None = Field(
        default=None, description="ISO 8601 completion timestamp"
    )
    state_transitions: list[StateTransition] = Field(
        default_factory=list, description="State transitions for this attempt"
    )
    classification: FailureClassification | None = Field(
        default=None, description="Failure classification"
    )
    root_cause: RootCauseAnalysis | None = Field(
        default=None, description="Diagnosed root cause"
    )
    strategy: RepairStrategy | None = Field(
        default=None, description="Selected repair strategy"
    )
    proposal: RepairProposal | None = Field(
        default=None, description="Proposed code repair"
    )
    execution_report: VerificationReport | None = Field(
        default=None, description="Verification report from running repair"
    )
    evaluation: RepairEvaluation | None = Field(
        default=None, description="Outcome evaluation"
    )
    patch_hash: str | None = Field(
        default=None, description="SHA-256 hash of proposed repair patch"
    )
    error_message: str | None = Field(
        default=None, description="Error encountered during attempt"
    )


class RepairConfiguration(BaseModel):
    """Runtime limits and configuration for repair controller."""

    max_attempts: int = Field(
        default=3,
        ge=1,
        le=5,
        description="Max repair attempts allowed (capped between 1 and 5)",
    )
    allow_provider_diagnosis: bool = Field(
        default=False,
        description="Allow LLM provider assisted root cause diagnosis",
    )
    auto_execute_repairs: bool = Field(
        default=True,
        description="Automatically execute repair inside sandbox",
    )
    max_evidence_bytes: int = Field(
        default=100_000,
        ge=1024,
        le=1_000_000,
        description="Max raw evidence bytes collected",
    )
    max_traceback_lines: int = Field(
        default=50,
        ge=5,
        le=200,
        description="Max traceback lines included in diagnostic evidence",
    )
    require_approval_for_high_risk: bool = Field(
        default=True,
        description="Require explicit human approval for HIGH or CRITICAL risk repairs",
    )


class RepairRequest(BaseModel):
    """API request payload to initiate an automated repair session."""

    repository_path: str = Field(description="Target repository path")
    execution_id: str = Field(description="Sprint 5 execution ID that failed")
    generation_id: str | None = Field(
        default=None, description="Sprint 4 generation ID if applicable"
    )
    plan_id: str | None = Field(
        default=None, description="Sprint 3 implementation plan ID if applicable"
    )
    config: RepairConfiguration = Field(
        default_factory=RepairConfiguration, description="Repair runtime configuration"
    )
    metadata: dict[str, Any] = Field(default_factory=dict, description="Audit metadata")


class RepairResult(BaseModel):
    """High-level status and outcome report of a repair session."""

    repair_id: str = Field(description="Unique repair identifier")
    repository_path: str = Field(description="Target repository path")
    initial_execution_id: str = Field(description="Initial failed execution ID")
    current_state: RepairState = Field(
        default=RepairState.PENDING, description="Current lifecycle state"
    )
    attempts: list[RepairAttempt] = Field(
        default_factory=list, description="Historical repair attempts"
    )
    total_attempts: int = Field(default=0, description="Total attempts executed")
    final_verdict: RepairEvaluationVerdict | None = Field(
        default=None, description="Final evaluation verdict if complete"
    )
    final_patch: str | None = Field(
        default=None, description="Final validated unified diff patch if fixed"
    )
    human_intervention_required: bool = Field(
        default=False, description="True if human review or approval is required"
    )
    human_intervention_reason: str | None = Field(
        default=None, description="Reason human intervention is needed"
    )
    started_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
        description="ISO 8601 start timestamp",
    )
    completed_at: str | None = Field(
        default=None, description="ISO 8601 completion timestamp"
    )
    error_message: str | None = Field(
        default=None, description="Error message if repair failed or cancelled"
    )
