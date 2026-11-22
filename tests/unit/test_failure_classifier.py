"""Unit tests for FailureClassifier."""

import uuid
from datetime import UTC, datetime

from backend.app.debugging.failure_classifier import FailureClassifier
from backend.app.debugging.models import FailureCategory
from backend.app.execution.models import (
    ExecutionResult,
    ExecutionStatus,
    TestSummary,
    VerificationReport,
)


def _make_report(
    patch_applied: bool = True,
    execution_success: bool = False,
    verification_passed: bool = False,
    exit_code: int = 1,
    stdout: str = "",
    stderr: str = "",
    failures: list[str] | None = None,
    status: ExecutionStatus = ExecutionStatus.FAILED,
    test_summary: TestSummary | None = None,
    errors: list[str] | None = None,
) -> VerificationReport:
    """Helper to build VerificationReport fixtures."""
    now_str = datetime.now(UTC).isoformat()
    exec_id = str(uuid.uuid4())
    exec_result = ExecutionResult(
        execution_id=exec_id,
        status=status,
        exit_code=exit_code,
        started_at=now_str,
        completed_at=now_str,
        duration_seconds=1.0,
        stdout=stdout,
        stderr=stderr,
        test_summary=test_summary,
        errors=errors or [],
    )
    return VerificationReport(
        execution_id=exec_id,
        patch_applied=patch_applied,
        execution_success=execution_success,
        verification_passed=verification_passed,
        execution_result=exec_result,
        failures=failures or [],
        created_at=now_str,
    )


def test_clean_verification_pass() -> None:
    """Clean pass report should yield UNKNOWN with no repair required."""
    report = _make_report(
        execution_success=True,
        verification_passed=True,
        exit_code=0,
        stdout="All tests passed",
    )
    classifier = FailureClassifier()
    result = classifier.classify(report)
    assert result.category == FailureCategory.UNKNOWN
    assert result.confidence == 1.0
    assert not result.auto_repair_permitted
    assert not result.human_intervention_required


def test_patch_application_failure() -> None:
    """Patch failure should be classified as PATCH_APPLICATION_ERROR."""
    report = _make_report(
        patch_applied=False,
        failures=["patch: **** malformed patch at line 42"],
    )
    classifier = FailureClassifier()
    result = classifier.classify(report)
    assert result.category == FailureCategory.PATCH_APPLICATION_ERROR
    assert result.confidence == 1.0
    assert not result.auto_repair_permitted
    assert result.human_intervention_required


def test_syntax_error_classification() -> None:
    """Syntax errors should be classified and permit auto-repair."""
    report = _make_report(
        stderr="SyntaxError: invalid syntax (main.py, line 10)",
        failures=["SyntaxError: invalid syntax"],
    )
    classifier = FailureClassifier()
    result = classifier.classify(report)
    assert result.category == FailureCategory.SYNTAX_ERROR
    assert result.confidence == 1.0
    assert result.auto_repair_permitted
    assert not result.human_intervention_required


def test_import_error_classification() -> None:
    """ModuleNotFoundError and ImportError should be classified as IMPORT_ERROR."""
    report = _make_report(
        stderr="ModuleNotFoundError: No module named 'backend.app.services.missing'",
    )
    classifier = FailureClassifier()
    result = classifier.classify(report)
    assert result.category == FailureCategory.IMPORT_ERROR
    assert result.auto_repair_permitted
    assert not result.human_intervention_required


def test_type_error_classification() -> None:
    """TypeError should be classified as TYPE_ERROR and permit auto-repair."""
    report = _make_report(
        stderr="TypeError: unsupported operand type(s) for +: 'int' and 'str'",
    )
    classifier = FailureClassifier()
    result = classifier.classify(report)
    assert result.category == FailureCategory.TYPE_ERROR
    assert result.auto_repair_permitted
    assert not result.human_intervention_required


def test_lint_error_classification() -> None:
    """Ruff/linter violations should be classified as LINT_ERROR."""
    report = _make_report(
        stdout="app/core.py:12:1: E501 line too long (92 > 88)\nruff failed",
    )
    classifier = FailureClassifier()
    result = classifier.classify(report)
    assert result.category == FailureCategory.LINT_ERROR
    assert result.auto_repair_permitted
    assert not result.human_intervention_required


def test_test_assertion_failure_classification() -> None:
    """Pytest failures with summary should be classified as TEST_FAILURE."""
    summary = TestSummary(total=10, passed=9, failed=1, skipped=0, errors=0)
    report = _make_report(
        stdout="FAILED tests/unit/test_app.py::test_calculate - assert 4 == 5",
        test_summary=summary,
    )
    classifier = FailureClassifier()
    result = classifier.classify(report)
    assert result.category == FailureCategory.TEST_FAILURE
    assert result.confidence == 0.95
    assert result.auto_repair_permitted
    assert not result.human_intervention_required
    assert "tests/unit/test_app.py::test_calculate" in result.evidence_references


def test_assertion_error_in_output() -> None:
    """Generic AssertionError without summary should be classified as TEST_FAILURE."""
    report = _make_report(
        stderr="AssertionError: Expected 200, got 404",
    )
    classifier = FailureClassifier()
    result = classifier.classify(report)
    assert result.category == FailureCategory.TEST_FAILURE
    assert result.auto_repair_permitted


def test_timeout_classification() -> None:
    """Execution timeout should be classified as TIMEOUT."""
    report = _make_report(
        status=ExecutionStatus.TIMED_OUT,
        exit_code=124,
        errors=["Execution timed out after 300 seconds"],
    )
    classifier = FailureClassifier()
    result = classifier.classify(report)
    assert result.category == FailureCategory.TIMEOUT
    assert not result.auto_repair_permitted
    assert result.human_intervention_required


def test_resource_exhaustion_classification() -> None:
    """Exit code 137 / OOM should be classified as RESOURCE_EXHAUSTION."""
    report = _make_report(
        exit_code=137,
        stderr="Killed",
    )
    classifier = FailureClassifier()
    result = classifier.classify(report)
    assert result.category == FailureCategory.RESOURCE_EXHAUSTION
    assert not result.auto_repair_permitted
    assert result.human_intervention_required


def test_environment_error_docker_missing() -> None:
    """Missing Docker daemon or CLI should be classified as ENVIRONMENT_ERROR."""
    report = _make_report(
        exit_code=127,
        stderr="Cannot connect to the Docker daemon at unix:///var/run/docker.sock",
    )
    classifier = FailureClassifier()
    result = classifier.classify(report)
    assert result.category == FailureCategory.ENVIRONMENT_ERROR
    assert not result.auto_repair_permitted
    assert result.human_intervention_required


def test_dependency_error_classification() -> None:
    """Package resolution errors should be classified as DEPENDENCY_ERROR."""
    report = _make_report(
        stderr="DistributionNotFound: The 'cryptography' distribution was not found",
    )
    classifier = FailureClassifier()
    result = classifier.classify(report)
    assert result.category == FailureCategory.DEPENDENCY_ERROR
    assert not result.auto_repair_permitted
    assert result.human_intervention_required


def test_unknown_failure_fallback() -> None:
    """Unrecognized failures fall back to UNKNOWN and require human intervention."""
    report = _make_report(
        exit_code=1,
        stdout="Some opaque failure without typical patterns",
    )
    classifier = FailureClassifier()
    result = classifier.classify(report)
    assert result.category == FailureCategory.UNKNOWN
    assert result.confidence < 0.5
    assert not result.auto_repair_permitted
    assert result.human_intervention_required
