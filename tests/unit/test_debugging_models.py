"""Unit tests for Debugging domain models and enums."""

import pytest
from pydantic import ValidationError

from backend.app.debugging.models import (
    DiagnosticEvidence,
    FailureCategory,
    FailureClassification,
    RepairAttempt,
    RepairConfiguration,
    RepairEvaluation,
    RepairEvaluationVerdict,
    RepairProposal,
    RepairRequest,
    RepairResult,
    RepairState,
    RepairStrategy,
    RepairStrategyType,
    RiskLevel,
    RootCauseAnalysis,
    RootCauseType,
    StateTransition,
)
from backend.app.execution.models import ExecutionStatus, TestSummary
from backend.app.generation.models import ChangeType, GeneratedFileChange


def test_failure_category_enum() -> None:
    """Verify all expected failure categories exist."""
    assert FailureCategory.TEST_FAILURE == "test_failure"
    assert FailureCategory.IMPORT_ERROR == "import_error"
    assert FailureCategory.SYNTAX_ERROR == "syntax_error"
    assert FailureCategory.TYPE_ERROR == "type_error"
    assert FailureCategory.LINT_ERROR == "lint_error"
    assert FailureCategory.DEPENDENCY_ERROR == "dependency_error"
    assert FailureCategory.CONFIGURATION_ERROR == "configuration_error"
    assert FailureCategory.TIMEOUT == "timeout"
    assert FailureCategory.RESOURCE_EXHAUSTION == "resource_exhaustion"
    assert FailureCategory.PATCH_APPLICATION_ERROR == "patch_application_error"
    assert FailureCategory.ENVIRONMENT_ERROR == "environment_error"
    assert FailureCategory.UNKNOWN == "unknown"


def test_root_cause_type_enum() -> None:
    """Verify all expected root cause types exist."""
    assert RootCauseType.INCORRECT_LOGIC == "incorrect_logic"
    assert RootCauseType.MISSING_IMPORT == "missing_import"
    assert RootCauseType.INCORRECT_SIGNATURE == "incorrect_signature"
    assert RootCauseType.TYPE_MISMATCH == "type_mismatch"
    assert RootCauseType.MISSING_DEPENDENCY == "missing_dependency"
    assert RootCauseType.INVALID_CONFIGURATION == "invalid_configuration"
    assert RootCauseType.INCORRECT_FILE_PATH == "incorrect_file_path"
    assert RootCauseType.BROKEN_INTEGRATION == "broken_integration"
    assert RootCauseType.TEST_EXPECTATION_MISMATCH == "test_expectation_mismatch"
    assert RootCauseType.ENVIRONMENT_ISSUE == "environment_issue"
    assert RootCauseType.UNKNOWN_CAUSE == "unknown_cause"


def test_repair_strategy_type_enum() -> None:
    """Verify repair strategy types."""
    assert RepairStrategyType.ADD_OR_CORRECT_IMPORT == "add_or_correct_import"
    assert RepairStrategyType.MODIFY_AFFECTED_LOGIC == "modify_affected_logic"
    assert RepairStrategyType.STOP_INFRASTRUCTURE_ISSUE == "stop_infrastructure_issue"
    assert RepairStrategyType.REQUEST_HUMAN_REVIEW == "request_human_review"


def test_repair_state_enum() -> None:
    """Verify repair state machine states."""
    assert RepairState.PENDING == "pending"
    assert RepairState.ANALYZING == "analyzing"
    assert RepairState.DIAGNOSING == "diagnosing"
    assert RepairState.AWAITING_APPROVAL == "awaiting_approval"
    assert RepairState.GENERATING_REPAIR == "generating_repair"
    assert RepairState.VALIDATING_REPAIR == "validating_repair"
    assert RepairState.EXECUTING_REPAIR == "executing_repair"
    assert RepairState.EVALUATING == "evaluating"
    assert RepairState.SUCCEEDED == "succeeded"
    assert RepairState.PARTIALLY_SUCCEEDED == "partially_succeeded"
    assert RepairState.FAILED == "failed"
    assert RepairState.NEEDS_HUMAN_REVIEW == "needs_human_review"
    assert RepairState.CANCELLED == "cancelled"
    assert RepairState.MAX_ATTEMPTS_REACHED == "max_attempts_reached"


def test_failure_classification_valid() -> None:
    """Verify FailureClassification model validation and defaults."""
    fc = FailureClassification(
        category=FailureCategory.IMPORT_ERROR,
        confidence=0.95,
        evidence_references=["tests/test_auth.py:4"],
        error_messages=["ModuleNotFoundError: No module named 'auth'"],
        explanation="Missing import in auth test",
        auto_repair_permitted=True,
        human_intervention_required=False,
    )
    assert fc.category == FailureCategory.IMPORT_ERROR
    assert fc.confidence == 0.95
    assert len(fc.evidence_references) == 1
    assert fc.auto_repair_permitted is True


def test_failure_classification_invalid_confidence() -> None:
    """Verify confidence out of [0, 1] raises validation error."""
    with pytest.raises(ValidationError):
        FailureClassification(
            category=FailureCategory.SYNTAX_ERROR,
            confidence=1.5,
        )
    with pytest.raises(ValidationError):
        FailureClassification(
            category=FailureCategory.SYNTAX_ERROR,
            confidence=-0.1,
        )


def test_diagnostic_evidence_defaults() -> None:
    """Verify DiagnosticEvidence initialization and fields."""
    summary = TestSummary(
        total=5,
        passed=4,
        failed=1,
        skipped=0,
        errors=0,
    )
    evidence = DiagnosticEvidence(
        execution_id="EXEC-1234",
        execution_status=ExecutionStatus.FAILED,
        test_summary=summary,
        failed_test_names=["test_login_failure"],
        error_messages=["AssertionError: 401 != 200"],
        traceback_snippets=["File 'test_login.py', line 12"],
        relevant_file_paths=["app/auth.py"],
        relevant_symbols=["login_user"],
        sanitized_secret_count=1,
    )
    assert evidence.execution_id == "EXEC-1234"
    assert evidence.execution_status == ExecutionStatus.FAILED
    assert evidence.test_summary is not None
    assert evidence.test_summary.failed == 1
    assert evidence.sanitized_secret_count == 1
    assert evidence.truncated is False


def test_root_cause_analysis_model() -> None:
    """Verify RootCauseAnalysis creation."""
    rca = RootCauseAnalysis(
        primary_cause=RootCauseType.INCORRECT_LOGIC,
        alternative_causes=[RootCauseType.TYPE_MISMATCH],
        confidence=0.85,
        supporting_evidence=["Condition inverted on line 42"],
        affected_files=["app/auth.py"],
        affected_symbols=["verify_password"],
        related_requirements=["REQ-1"],
        related_acceptance_criteria=["AC-1"],
        is_repairable_automatically=True,
        explanation="Password verification condition was inverted.",
    )
    assert rca.primary_cause == RootCauseType.INCORRECT_LOGIC
    assert len(rca.alternative_causes) == 1
    assert rca.confidence == 0.85
    assert rca.is_repairable_automatically is True


def test_repair_strategy_model() -> None:
    """Verify RepairStrategy creation and defaults."""
    strategy = RepairStrategy(
        strategy_type=RepairStrategyType.MODIFY_AFFECTED_LOGIC,
        reason="Correct inverted logic in auth check",
        target_files=["app/auth.py"],
        target_symbols=["verify_password"],
        expected_changes=["Fix if condition branch"],
        risk_level=RiskLevel.LOW,
        code_generation_permitted=True,
        human_approval_required=False,
    )
    assert strategy.strategy_type == RepairStrategyType.MODIFY_AFFECTED_LOGIC
    assert strategy.risk_level == RiskLevel.LOW
    assert strategy.code_generation_permitted is True


def test_repair_proposal_model() -> None:
    """Verify RepairProposal model instantiation."""
    strategy = RepairStrategy(
        strategy_type=RepairStrategyType.ADD_OR_CORRECT_IMPORT,
        reason="Missing import of jwt",
    )
    rca = RootCauseAnalysis(
        primary_cause=RootCauseType.MISSING_IMPORT,
        confidence=1.0,
    )
    change = GeneratedFileChange(
        path="app/auth.py",
        change_type=ChangeType.MODIFY,
        content="import jwt\n",
        explanation="Add missing jwt import",
    )
    proposal = RepairProposal(
        repair_id="REP-001",
        attempt_number=1,
        strategy=strategy,
        root_cause=rca,
        file_changes=[change],
        patch_content=(
            "--- a/app/auth.py\n+++ b/app/auth.py\n@@ -1 +1,2 @@\n+import jwt\n"
        ),
        explanation="Added import jwt to app/auth.py",
    )
    assert proposal.repair_id == "REP-001"
    assert proposal.attempt_number == 1
    assert len(proposal.file_changes) == 1


def test_repair_evaluation_model() -> None:
    """Verify RepairEvaluation model values."""
    evaluation = RepairEvaluation(
        verdict=RepairEvaluationVerdict.FIXED,
        original_failures_resolved=True,
        remaining_failures=[],
        new_failures_introduced=[],
        regressions_detected=False,
        acceptance_criteria_verified=["AC-1"],
        unverified_criteria=[],
        scope_preserved=True,
        summary="All tests passed and original failure resolved cleanly.",
        duration_seconds=0.25,
    )
    assert evaluation.verdict == RepairEvaluationVerdict.FIXED
    assert evaluation.original_failures_resolved is True
    assert evaluation.regressions_detected is False


def test_repair_configuration_validation() -> None:
    """Verify RepairConfiguration enforces boundaries."""
    cfg = RepairConfiguration(max_attempts=3)
    assert cfg.max_attempts == 3

    # max_attempts cannot exceed 5
    with pytest.raises(ValidationError):
        RepairConfiguration(max_attempts=6)

    # max_attempts cannot be less than 1
    with pytest.raises(ValidationError):
        RepairConfiguration(max_attempts=0)

    # max_evidence_bytes limits
    with pytest.raises(ValidationError):
        RepairConfiguration(max_evidence_bytes=100)


def test_repair_request_model() -> None:
    """Verify RepairRequest instantiation and defaults."""
    req = RepairRequest(
        repository_path="agentforge-fixtures/sample-project",
        execution_id="EXEC-1234",
    )
    assert req.repository_path == "agentforge-fixtures/sample-project"
    assert req.execution_id == "EXEC-1234"
    assert req.config.max_attempts == 3
    assert req.generation_id is None


def test_repair_attempt_and_result_models() -> None:
    """Verify RepairAttempt and RepairResult lifecycles."""
    transition = StateTransition(
        from_state=RepairState.PENDING,
        to_state=RepairState.ANALYZING,
        reason="Starting failure analysis",
    )
    attempt = RepairAttempt(
        attempt_number=1,
        state_transitions=[transition],
    )
    assert attempt.attempt_number == 1
    assert len(attempt.state_transitions) == 1

    result = RepairResult(
        repair_id="REP-999",
        repository_path="tests/fixtures",
        initial_execution_id="EXEC-001",
        current_state=RepairState.SUCCEEDED,
        attempts=[attempt],
        total_attempts=1,
        final_verdict=RepairEvaluationVerdict.FIXED,
        final_patch="diff --git...",
    )
    assert result.repair_id == "REP-999"
    assert result.total_attempts == 1
    assert result.current_state == RepairState.SUCCEEDED
    assert result.final_verdict == RepairEvaluationVerdict.FIXED
