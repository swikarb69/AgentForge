"""Unit tests for DiagnosticEvidenceCollector."""

import uuid
from datetime import UTC, datetime

from backend.app.debugging.evidence_collector import DiagnosticEvidenceCollector
from backend.app.debugging.models import RepairConfiguration
from backend.app.execution.models import (
    ExecutionResult,
    ExecutionStatus,
    TestSummary,
    VerificationReport,
)
from backend.app.planning.models import (
    ImplementationPlan,
    TestPlan,
    VerificationCase,
)


def _make_report_with_output(
    stdout: str = "",
    stderr: str = "",
    failures: list[str] | None = None,
    failed_tests: int = 1,
) -> VerificationReport:
    """Helper to create a VerificationReport with custom output."""
    now_str = datetime.now(UTC).isoformat()
    exec_id = str(uuid.uuid4())
    summary = TestSummary(total=10, passed=9, failed=failed_tests, skipped=0, errors=0)
    exec_result = ExecutionResult(
        execution_id=exec_id,
        status=ExecutionStatus.FAILED,
        exit_code=1,
        started_at=now_str,
        completed_at=now_str,
        duration_seconds=1.2,
        stdout=stdout,
        stderr=stderr,
        test_summary=summary,
    )
    return VerificationReport(
        execution_id=exec_id,
        patch_applied=True,
        execution_success=False,
        verification_passed=False,
        execution_result=exec_result,
        failures=failures or [],
        created_at=now_str,
    )


def test_strip_ansi_escapes() -> None:
    """ANSI color codes should be stripped completely."""
    collector = DiagnosticEvidenceCollector()
    text = "\x1b[31mError: \x1b[0m\x1b[1mCritical failure\x1b[0m"
    clean = collector.strip_ansi(text)
    assert clean == "Error: Critical failure"


def test_secret_redaction() -> None:
    """API keys, tokens, and private keys should be sanitized."""
    collector = DiagnosticEvidenceCollector()
    sensitive_text = (
        "Using API key: sk-1234567890abcdef1234567890 and "
        "AIzaSyDemoSecretKeyForTestingGoogle123456789 "
        "bearer "
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.doNotLeakThis"
    )
    sanitized, count = collector.redact_secrets(sensitive_text)

    assert "[REDACTED_SECRET]" in sanitized
    assert "sk-1234567890abcdef" not in sanitized
    assert "AIzaSy" not in sanitized
    assert "eyJhbGciOi" not in sanitized
    assert count >= 3


def test_traceback_extraction_and_symbol_resolution() -> None:
    """Traceback snippets, implicated files and symbols should be properly parsed."""
    traceback_log = """
Traceback (most recent call last):
  File "backend/app/services/calculator.py", line 42, in compute_sum
    result = a + b
TypeError: unsupported operand type(s) for +: 'int' and 'str'
FAILED tests/unit/test_calc.py::test_compute_sum - TypeError
"""
    report = _make_report_with_output(stderr=traceback_log)
    collector = DiagnosticEvidenceCollector()
    evidence = collector.collect(report)

    assert "backend/app/services/calculator.py" in evidence.relevant_file_paths
    assert "compute_sum" in evidence.relevant_symbols
    assert "tests/unit/test_calc.py::test_compute_sum" in evidence.failed_test_names
    assert any("TypeError" in m for m in evidence.error_messages)
    assert not evidence.truncated


def test_traceback_truncation() -> None:
    """Tracebacks exceeding max_traceback_lines should be truncated."""
    config = RepairConfiguration(max_traceback_lines=5)
    collector = DiagnosticEvidenceCollector(config=config)

    long_tb = (
        "Traceback (most recent call last):\n"
        + "\n".join(
            f'  File "app/module_{i}.py", line {i}, in func_{i}\n    x = {i}'
            for i in range(10)
        )
        + "\nValueError: too many steps"
    )

    report = _make_report_with_output(stderr=long_tb)
    evidence = collector.collect(report)

    assert evidence.truncated
    assert any("Traceback exceeded" in reason for reason in evidence.truncation_reasons)
    assert len(evidence.traceback_snippets) <= 5


def test_raw_log_byte_truncation() -> None:
    """Output exceeding max_evidence_bytes should be capped and marked truncated."""
    config = RepairConfiguration(max_evidence_bytes=1024)
    collector = DiagnosticEvidenceCollector(config=config)

    huge_log = "A" * 2048
    report = _make_report_with_output(stdout=huge_log)
    evidence = collector.collect(report)

    assert evidence.truncated
    assert any(
        "Raw log output exceeded" in reason for reason in evidence.truncation_reasons
    )


def test_plan_acceptance_criteria_and_patch_summary() -> None:
    """Acceptance criteria and patch summary should be extracted."""
    collector = DiagnosticEvidenceCollector()

    report = _make_report_with_output(stdout="FAILED tests/unit/test_foo.py::test_bar")

    plan = ImplementationPlan(
        id="PLAN-42",
        issue_id="ISSUE-42",
        objective="Add calculation feature",
        summary="Calculates total with tax",
        test_plan=TestPlan(
            verification_cases=[
                VerificationCase(
                    id="VC-1",
                    description="Should return total sum correctly",
                    expected_behavior="Return sum of inputs",
                ),
                VerificationCase(
                    id="VC-2",
                    description="Should handle negative numbers",
                    expected_behavior="Return proper negative sum",
                ),
            ]
        ),
    )

    patch_diff = """--- a/app/calc.py
+++ b/app/calc.py
@@ -1,2 +1,3 @@
+def new_calc(): pass
"""
    evidence = collector.collect(
        report=report,
        plan=plan,
        metadata={"patch_content": patch_diff},
    )

    assert evidence.plan_id == plan.id
    assert "Should return total sum correctly" in evidence.acceptance_criteria
    assert "Should handle negative numbers" in evidence.acceptance_criteria
    assert evidence.patch_summary is not None
    assert "Patch modified 1 files" in evidence.patch_summary


def test_empty_or_minimal_report() -> None:
    """Collector should handle minimal report without throwing exceptions."""
    collector = DiagnosticEvidenceCollector()
    now_str = datetime.now(UTC).isoformat()
    report = VerificationReport(
        execution_id=str(uuid.uuid4()),
        patch_applied=True,
        execution_success=False,
        verification_passed=False,
        execution_result=None,
        failures=[],
        created_at=now_str,
    )

    evidence = collector.collect(report)
    assert evidence.execution_status == ExecutionStatus.FAILED
    assert evidence.test_summary is None
    assert evidence.failed_test_names == []
    assert evidence.traceback_snippets == []
    assert not evidence.truncated
