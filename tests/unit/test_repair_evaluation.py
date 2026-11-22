"""Unit tests for RepairEvaluator."""

from datetime import UTC, datetime

from backend.app.debugging.evaluation import RepairEvaluator
from backend.app.debugging.models import (
    RepairEvaluationVerdict,
    RepairProposal,
    RepairStrategy,
    RepairStrategyType,
    RootCauseAnalysis,
    RootCauseType,
)
from backend.app.execution.models import (
    ExecutionResult,
    ExecutionStatus,
    TestSummary,
    VerificationReport,
)
from backend.app.generation.models import GenerationStatus, PatchValidation
from backend.app.planning.models import ImplementationPlan, TestPlan, VerificationCase


def _make_report(
    success: bool,
    passed: bool,
    failures: list[str] | None = None,
    failed_test_names: list[str] | None = None,
    status: ExecutionStatus = ExecutionStatus.COMPLETED,
    patch_applied: bool = True,
) -> VerificationReport:
    """Helper to build test VerificationReport fixtures."""
    now_str = datetime.now(UTC).isoformat()
    stdout = ""
    if failed_test_names:
        stdout = "\n".join(f"FAILED {t} - AssertionError" for t in failed_test_names)

    exec_result = ExecutionResult(
        execution_id="exec-eval-test",
        status=status,
        exit_code=0 if passed else 1,
        started_at=now_str,
        completed_at=now_str,
        stdout=stdout,
        test_summary=TestSummary(
            total=10,
            passed=10 if passed else 9,
            failed=0 if passed else len(failed_test_names or ["test_fail"]),
        ),
    )

    return VerificationReport(
        execution_id="rep-eval-test",
        patch_applied=patch_applied,
        execution_success=success,
        verification_passed=passed,
        execution_result=exec_result,
        failures=failures or [],
        created_at=now_str,
    )


def test_evaluator_verdict_fixed() -> None:
    """When all previous failures are resolved, verdict is FIXED."""
    pre_report = _make_report(
        success=False,
        passed=False,
        failed_test_names=["tests/test_math.py::test_sum"],
        failures=["tests/test_math.py::test_sum"],
    )
    post_report = _make_report(
        success=True,
        passed=True,
        failed_test_names=[],
        failures=[],
    )

    evaluator = RepairEvaluator()
    evaluation = evaluator.evaluate(pre_report, post_report)

    assert evaluation.verdict == RepairEvaluationVerdict.FIXED
    assert evaluation.original_failures_resolved
    assert evaluation.remaining_failures == []
    assert evaluation.new_failures_introduced == []
    assert not evaluation.regressions_detected


def test_evaluator_verdict_regression_detected() -> None:
    """When a new failure is introduced, verdict is REGRESSION_DETECTED."""
    pre_report = _make_report(
        success=False,
        passed=False,
        failed_test_names=["tests/test_math.py::test_sum"],
    )
    post_report = _make_report(
        success=False,
        passed=False,
        failed_test_names=[
            "tests/test_math.py::test_sum",
            "tests/test_math.py::test_multiply",
        ],
    )

    evaluator = RepairEvaluator()
    evaluation = evaluator.evaluate(pre_report, post_report)

    assert evaluation.verdict == RepairEvaluationVerdict.REGRESSION_DETECTED
    assert evaluation.regressions_detected
    assert "tests/test_math.py::test_multiply" in evaluation.new_failures_introduced


def test_evaluator_verdict_partially_fixed() -> None:
    """When some failures are resolved and others remain, verdict is PARTIALLY_FIXED."""
    pre_report = _make_report(
        success=False,
        passed=False,
        failed_test_names=[
            "tests/test_math.py::test_sum",
            "tests/test_math.py::test_diff",
        ],
    )
    post_report = _make_report(
        success=False,
        passed=False,
        failed_test_names=["tests/test_math.py::test_diff"],
    )

    plan = ImplementationPlan(
        id="PLAN-1",
        objective="Math fix",
        summary="Fix math operations",
        test_plan=TestPlan(
            verification_cases=[
                VerificationCase(
                    id="VC-1",
                    description="Sum should be correct",
                    expected_behavior="4",
                ),
                VerificationCase(
                    id="VC-2",
                    description="Diff should be correct",
                    expected_behavior="0",
                ),
            ]
        ),
    )

    evaluator = RepairEvaluator()
    evaluation = evaluator.evaluate(pre_report, post_report, plan=plan)

    assert evaluation.verdict == RepairEvaluationVerdict.PARTIALLY_FIXED
    assert not evaluation.original_failures_resolved
    assert "tests/test_math.py::test_diff" in evaluation.remaining_failures
    assert len(evaluation.acceptance_criteria_verified) > 0
    assert len(evaluation.unverified_criteria) > 0


def test_evaluator_verdict_not_fixed() -> None:
    """When all previous failures persist without resolution, verdict is NOT_FIXED."""
    pre_report = _make_report(
        success=False,
        passed=False,
        failed_test_names=["tests/test_math.py::test_sum"],
    )
    post_report = _make_report(
        success=False,
        passed=False,
        failed_test_names=["tests/test_math.py::test_sum"],
    )

    evaluator = RepairEvaluator()
    evaluation = evaluator.evaluate(pre_report, post_report)

    assert evaluation.verdict == RepairEvaluationVerdict.NOT_FIXED
    assert not evaluation.original_failures_resolved
    assert evaluation.remaining_failures == ["tests/test_math.py::test_sum"]


def test_evaluator_verdict_inconclusive() -> None:
    """When post-repair timed out or patch failed, verdict is INCONCLUSIVE."""
    pre_report = _make_report(
        success=False,
        passed=False,
        failed_test_names=["tests/test_math.py::test_sum"],
    )
    post_report = _make_report(
        success=False,
        passed=False,
        status=ExecutionStatus.TIMED_OUT,
        patch_applied=False,
    )

    evaluator = RepairEvaluator()
    evaluation = evaluator.evaluate(pre_report, post_report)

    assert evaluation.verdict == RepairEvaluationVerdict.INCONCLUSIVE


def test_evaluator_scope_violation() -> None:
    """When tests pass but validation was invalid, verdict is NEEDS_HUMAN_REVIEW."""
    pre_report = _make_report(
        success=False,
        passed=False,
        failed_test_names=["tests/test_math.py::test_sum"],
    )
    post_report = _make_report(
        success=True,
        passed=True,
        failed_test_names=[],
    )

    proposal = RepairProposal(
        repair_id="REP-001",
        strategy=RepairStrategy(
            strategy_type=RepairStrategyType.MODIFY_AFFECTED_LOGIC,
            reason="Fix logic",
        ),
        root_cause=RootCauseAnalysis(
            primary_cause=RootCauseType.TEST_EXPECTATION_MISMATCH,
        ),
        validation=PatchValidation(
            valid=False,
            status=GenerationStatus.REJECTED,
            errors=["Scope violation: modified unauthorized file"],
        ),
    )

    evaluator = RepairEvaluator()
    evaluation = evaluator.evaluate(pre_report, post_report, proposal=proposal)

    assert evaluation.verdict == RepairEvaluationVerdict.NEEDS_HUMAN_REVIEW
    assert not evaluation.scope_preserved
