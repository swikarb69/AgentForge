"""Unit tests for RootCauseAnalyzer."""

from backend.app.debugging.models import (
    DiagnosticEvidence,
    FailureCategory,
    FailureClassification,
    RootCauseType,
)
from backend.app.debugging.root_cause_analyzer import RootCauseAnalyzer
from backend.app.execution.models import ExecutionStatus
from backend.app.planning.models import ChangeType, FileChange, ImplementationPlan


def _make_evidence(
    relevant_files: list[str] | None = None,
    relevant_symbols: list[str] | None = None,
    error_messages: list[str] | None = None,
) -> DiagnosticEvidence:
    """Helper to build DiagnosticEvidence fixtures."""
    return DiagnosticEvidence(
        execution_id="EXEC-TEST-001",
        execution_status=ExecutionStatus.FAILED,
        failed_test_names=["tests/test_calculator.py::test_add"],
        error_messages=error_messages or [],
        traceback_snippets=[],
        relevant_file_paths=relevant_files or ["app/calculator.py"],
        relevant_symbols=relevant_symbols or ["add"],
    )


def test_analyze_syntax_error() -> None:
    """Syntax error should be diagnosed as INCORRECT_LOGIC and marked repairable."""
    classification = FailureClassification(
        category=FailureCategory.SYNTAX_ERROR,
        confidence=1.0,
        error_messages=["SyntaxError: invalid syntax"],
    )
    evidence = _make_evidence()
    analyzer = RootCauseAnalyzer()
    result = analyzer.analyze(classification, evidence)

    assert result.primary_cause == RootCauseType.INCORRECT_LOGIC
    assert result.is_repairable_automatically
    assert "app/calculator.py" in result.affected_files


def test_analyze_missing_import() -> None:
    """ModuleNotFoundError should be diagnosed as MISSING_IMPORT."""
    classification = FailureClassification(
        category=FailureCategory.IMPORT_ERROR,
        confidence=0.95,
        error_messages=["ModuleNotFoundError: No module named 'math_utils'"],
    )
    evidence = _make_evidence()
    analyzer = RootCauseAnalyzer()
    result = analyzer.analyze(classification, evidence)

    assert result.primary_cause == RootCauseType.MISSING_IMPORT
    assert result.is_repairable_automatically


def test_analyze_wrong_import() -> None:
    """ImportError with cannot import name should be diagnosed as MISSING_IMPORT."""
    classification = FailureClassification(
        category=FailureCategory.IMPORT_ERROR,
        confidence=0.95,
        error_messages=["ImportError: cannot import name 'old_helper' from 'utils'"],
    )
    evidence = _make_evidence()
    analyzer = RootCauseAnalyzer()
    result = analyzer.analyze(classification, evidence)

    assert result.primary_cause == RootCauseType.MISSING_IMPORT
    assert result.is_repairable_automatically


def test_analyze_type_error() -> None:
    """Type error should be diagnosed as TYPE_MISMATCH."""
    classification = FailureClassification(
        category=FailureCategory.TYPE_ERROR,
        confidence=0.9,
        error_messages=["TypeError: unsupported operand type"],
    )
    evidence = _make_evidence()
    analyzer = RootCauseAnalyzer()
    result = analyzer.analyze(classification, evidence)

    assert result.primary_cause == RootCauseType.TYPE_MISMATCH
    assert result.is_repairable_automatically


def test_analyze_lint_error() -> None:
    """Lint violation should be diagnosed as INCORRECT_LOGIC."""
    classification = FailureClassification(
        category=FailureCategory.LINT_ERROR,
        confidence=0.9,
        error_messages=["E501 Line too long"],
    )
    evidence = _make_evidence()
    analyzer = RootCauseAnalyzer()
    result = analyzer.analyze(classification, evidence)

    assert result.primary_cause == RootCauseType.INCORRECT_LOGIC
    assert result.is_repairable_automatically


def test_analyze_assertion_failure() -> None:
    """Pure test assertion should be diagnosed as TEST_EXPECTATION_MISMATCH."""
    classification = FailureClassification(
        category=FailureCategory.TEST_FAILURE,
        confidence=0.95,
        error_messages=["AssertionError: assert 4 == 5"],
    )
    evidence = _make_evidence(error_messages=["AssertionError: assert 4 == 5"])
    analyzer = RootCauseAnalyzer()
    result = analyzer.analyze(classification, evidence)

    assert result.primary_cause == RootCauseType.TEST_EXPECTATION_MISMATCH
    assert result.is_repairable_automatically


def test_analyze_unhandled_exception_in_test() -> None:
    """Runtime exceptions during test run should be diagnosed as INCORRECT_LOGIC."""
    classification = FailureClassification(
        category=FailureCategory.TEST_FAILURE,
        confidence=0.95,
        error_messages=["KeyError: 'user_id'"],
    )
    evidence = _make_evidence(error_messages=["KeyError: 'user_id'"])
    analyzer = RootCauseAnalyzer()
    result = analyzer.analyze(classification, evidence)

    assert result.primary_cause == RootCauseType.INCORRECT_LOGIC
    assert RootCauseType.TEST_EXPECTATION_MISMATCH in result.alternative_causes
    assert result.is_repairable_automatically


def test_analyze_environment_defect() -> None:
    """Environment failure should be diagnosed as ENVIRONMENT_ISSUE."""
    classification = FailureClassification(
        category=FailureCategory.ENVIRONMENT_ERROR,
        confidence=1.0,
        error_messages=["Docker daemon not reachable"],
        auto_repair_permitted=False,
        human_intervention_required=True,
    )
    evidence = _make_evidence()
    analyzer = RootCauseAnalyzer()
    result = analyzer.analyze(classification, evidence)

    assert result.primary_cause == RootCauseType.ENVIRONMENT_ISSUE
    assert not result.is_repairable_automatically


def test_analyze_timeout() -> None:
    """Execution timeout should be diagnosed as ENVIRONMENT_ISSUE."""
    classification = FailureClassification(
        category=FailureCategory.TIMEOUT,
        confidence=0.95,
        error_messages=["Execution timed out after 300 seconds"],
        auto_repair_permitted=False,
    )
    evidence = _make_evidence()
    analyzer = RootCauseAnalyzer()
    result = analyzer.analyze(classification, evidence)

    assert result.primary_cause == RootCauseType.ENVIRONMENT_ISSUE
    assert not result.is_repairable_automatically


def test_analyze_resource_exhaustion() -> None:
    """Resource exhaustion should be diagnosed as ENVIRONMENT_ISSUE."""
    classification = FailureClassification(
        category=FailureCategory.RESOURCE_EXHAUSTION,
        confidence=0.95,
        error_messages=["OOMKilled"],
    )
    evidence = _make_evidence()
    analyzer = RootCauseAnalyzer()
    result = analyzer.analyze(classification, evidence)

    assert result.primary_cause == RootCauseType.ENVIRONMENT_ISSUE
    assert not result.is_repairable_automatically


def test_analyze_patch_application_error() -> None:
    """Patch failure should be diagnosed as INCORRECT_FILE_PATH."""
    classification = FailureClassification(
        category=FailureCategory.PATCH_APPLICATION_ERROR,
        confidence=1.0,
        error_messages=["Failed to apply patch"],
    )
    evidence = _make_evidence()
    analyzer = RootCauseAnalyzer()
    result = analyzer.analyze(classification, evidence)

    assert result.primary_cause == RootCauseType.INCORRECT_FILE_PATH
    assert not result.is_repairable_automatically


def test_out_of_scope_implicated_files_detection() -> None:
    """Implicated files not in plan should be flagged in uncertainty_notes."""
    classification = FailureClassification(
        category=FailureCategory.TEST_FAILURE,
        confidence=0.95,
        error_messages=["AssertionError: assert 1 == 2"],
    )
    evidence = _make_evidence(
        relevant_files=["app/other.py", "app/calculator.py"],
    )
    plan = ImplementationPlan(
        id="PLAN-1",
        objective="Fix calculator",
        summary="Update calculator logic",
        files_to_modify=[
            FileChange(
                path="app/calculator.py",
                change_type=ChangeType.MODIFY,
                reason="Fix add function",
            )
        ],
    )
    analyzer = RootCauseAnalyzer()
    result = analyzer.analyze(classification, evidence, plan=plan)

    assert result.uncertainty_notes is not None
    assert "app/other.py" in result.uncertainty_notes
    assert RootCauseType.INCORRECT_FILE_PATH in result.alternative_causes
