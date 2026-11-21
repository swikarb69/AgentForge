"""Unit tests for VerificationEngine."""

from backend.app.execution.models import (
    ExecutionResult,
    ExecutionStatus,
    PatchApplicationResult,
    TestSummary,
)
from backend.app.execution.verification import VerificationEngine


def test_verification_evaluate_clean_pass() -> None:
    """Verify clean passing patch and test run produces passing report."""
    patch_res = PatchApplicationResult(
        success=True,
        applied_files=["app/main.py"],
        modified_files=["app/main.py"],
    )
    exec_res = ExecutionResult(
        execution_id="exec-eval-01",
        status=ExecutionStatus.COMPLETED,
        exit_code=0,
        started_at="2026-11-21T12:00:00Z",
        completed_at="2026-11-21T12:00:02Z",
        test_summary=TestSummary(total=10, passed=10, failed=0),
    )

    report = VerificationEngine.evaluate(patch_res, exec_res)

    assert report.execution_id == "exec-eval-01"
    assert report.verification_passed
    assert report.execution_success
    assert report.patch_applied
    assert report.tests_executed
    assert report.failures == []


def test_verification_evaluate_patch_failure() -> None:
    """Verify patch application failure fails verification."""
    patch_res = PatchApplicationResult(
        success=False,
        errors=["Baseline integrity mismatch for core/auth.py"],
    )
    exec_res = ExecutionResult(
        execution_id="exec-eval-02",
        status=ExecutionStatus.FAILED,
        exit_code=1,
    )

    report = VerificationEngine.evaluate(patch_res, exec_res)

    assert not report.verification_passed
    assert not report.patch_applied
    assert any("Baseline integrity mismatch" in f for f in report.failures)


def test_verification_evaluate_test_failures() -> None:
    """Verify test failures in sandbox fail verification with descriptive reasons."""
    patch_res = PatchApplicationResult(success=True, applied_files=["app.py"])
    exec_res = ExecutionResult(
        execution_id="exec-eval-03",
        status=ExecutionStatus.FAILED,
        exit_code=1,
        started_at="2026-11-21T12:00:00Z",
        test_summary=TestSummary(total=10, passed=8, failed=2),
    )

    report = VerificationEngine.evaluate(patch_res, exec_res)

    assert not report.verification_passed
    assert not report.execution_success
    assert any("2 test(s) failed" in f for f in report.failures)


def test_verification_evaluate_test_errors() -> None:
    """Verify test collection errors are captured in report failures."""
    patch_res = PatchApplicationResult(success=True)
    exec_res = ExecutionResult(
        execution_id="exec-eval-04",
        status=ExecutionStatus.FAILED,
        exit_code=2,
        started_at="2026-11-21T12:00:00Z",
        test_summary=TestSummary(total=1, errors=1),
    )

    report = VerificationEngine.evaluate(patch_res, exec_res)

    assert not report.verification_passed
    assert any("1 test error(s) occurred" in f for f in report.failures)


def test_verification_evaluate_container_timeout() -> None:
    """Verify container timeout is synthesized into report failure."""
    patch_res = PatchApplicationResult(success=True)
    exec_res = ExecutionResult(
        execution_id="exec-eval-05",
        status=ExecutionStatus.TIMED_OUT,
        exit_code=124,
        started_at="2026-11-21T12:00:00Z",
        errors=["Execution timed out after 30 seconds"],
    )

    report = VerificationEngine.evaluate(patch_res, exec_res)

    assert not report.verification_passed
    assert any("timed out after 30 seconds" in f for f in report.failures)
