"""Unit tests for BoundedRepairController and DebuggingService."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.app.debugging.models import (
    FailureCategory,
    FailureClassification,
    RepairConfiguration,
    RepairEvaluation,
    RepairEvaluationVerdict,
    RepairProposal,
    RepairRequest,
    RepairState,
    RepairStrategy,
    RepairStrategyType,
    RiskLevel,
    RootCauseAnalysis,
    RootCauseType,
)
from backend.app.debugging.repair_controller import BoundedRepairController
from backend.app.debugging.service import DebuggingService
from backend.app.execution.models import (
    ExecutionResult,
    ExecutionStatus,
    VerificationReport,
)
from backend.app.generation.models import (
    GeneratedFileChange,
    GenerationStatus,
    PatchValidation,
)
from backend.app.planning.models import ChangeType


def _create_mock_report(
    success: bool = False,
    stdout: str = "FAILED test_sample.py::test_err",
    stderr: str = "",
    errors: list[str] | None = None,
) -> VerificationReport:
    """Helper to create a mock verification report."""
    return VerificationReport(
        execution_id="exec-test-123",
        execution_success=success,
        patch_applied=True,
        tests_executed=True,
        verification_passed=success,
        failures=[] if success else ["test_sample.py::test_err failed"],
        execution_result=ExecutionResult(
            execution_id="exec-test-123",
            status=ExecutionStatus.COMPLETED if success else ExecutionStatus.FAILED,
            exit_code=0 if success else 1,
            stdout=stdout,
            stderr=stderr,
            errors=errors or [],
        ),
    )


def _create_mock_proposal(
    repair_id: str = "repair-test-1",
    patch_content: str = "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-broken\n+fixed\n",
    valid: bool = True,
) -> RepairProposal:
    """Helper to create a mock repair proposal."""
    strat = RepairStrategy(
        strategy_type=RepairStrategyType.MODIFY_AFFECTED_LOGIC,
        reason="Fix broken logic",
        target_files=["app.py"],
        risk_level=RiskLevel.LOW,
        code_generation_permitted=True,
        human_approval_required=False,
    )
    rc = RootCauseAnalysis(
        primary_cause=RootCauseType.INCORRECT_LOGIC,
        is_repairable_automatically=True,
        explanation="Logic defect in app.py",
    )
    return RepairProposal(
        repair_id=repair_id,
        attempt_number=1,
        strategy=strat,
        root_cause=rc,
        file_changes=[
            GeneratedFileChange(
                path="app.py",
                change_type=ChangeType.MODIFY,
                content="fixed",
            )
        ],
        patch_content=patch_content,
        validation=PatchValidation(
            valid=valid,
            status=GenerationStatus.VALIDATED if valid else GenerationStatus.REJECTED,
            errors=[] if valid else ["Invalid syntax"],
        ),
    )


@pytest.mark.anyio
async def test_repair_controller_successful_repair(tmp_path: Path) -> None:
    """Test successful single-iteration automated repair flow."""
    initial_report = _create_mock_report(success=False)
    fixed_report = _create_mock_report(success=True, stdout="1 passed")

    mock_classifier = MagicMock()
    mock_classifier.classify.return_value = FailureClassification(
        category=FailureCategory.TEST_FAILURE,
        explanation="Assertion failed",
        auto_repair_permitted=True,
        human_intervention_required=False,
    )

    mock_collector = MagicMock()
    mock_collector.collect.return_value = MagicMock()

    mock_analyzer = MagicMock()
    mock_analyzer.analyze.return_value = RootCauseAnalysis(
        primary_cause=RootCauseType.INCORRECT_LOGIC,
        is_repairable_automatically=True,
        explanation="Fix assertion failure",
    )

    mock_strategy = MagicMock()
    mock_strategy.select_strategy.return_value = RepairStrategy(
        strategy_type=RepairStrategyType.MODIFY_AFFECTED_LOGIC,
        reason="Modify logic",
        risk_level=RiskLevel.LOW,
        code_generation_permitted=True,
        human_approval_required=False,
    )

    mock_engine = MagicMock()
    mock_engine.generate_proposal = AsyncMock(return_value=_create_mock_proposal())

    mock_evaluator = MagicMock()
    mock_evaluator.evaluate.return_value = RepairEvaluation(
        verdict=RepairEvaluationVerdict.FIXED,
        original_failures_resolved=True,
        remaining_failures=[],
        new_failures_introduced=[],
        regressions_detected=False,
        summary="All tests passed",
    )

    mock_exec_service = MagicMock()
    mock_exec_service.execute_and_verify = AsyncMock(return_value=fixed_report)

    controller = BoundedRepairController(
        failure_classifier=mock_classifier,
        evidence_collector=mock_collector,
        root_cause_analyzer=mock_analyzer,
        repair_strategy_engine=mock_strategy,
        repair_proposal_engine=mock_engine,
        repair_evaluator=mock_evaluator,
        execution_service=mock_exec_service,
    )

    req = RepairRequest(
        repository_path=str(tmp_path),
        execution_id="exec-test-123",
        config=RepairConfiguration(max_attempts=3),
    )

    result = await controller.run_repair(request=req, initial_report=initial_report)

    assert result.current_state == RepairState.SUCCEEDED
    assert result.final_verdict == RepairEvaluationVerdict.FIXED
    assert result.total_attempts == 1
    assert result.final_patch is not None
    assert not result.human_intervention_required


@pytest.mark.anyio
async def test_repair_controller_environment_defect_halts(tmp_path: Path) -> None:
    """Test environment/infrastructure defects halt without code gen."""
    initial_report = _create_mock_report(
        success=False, stderr="Cannot connect to the Docker daemon"
    )

    mock_classifier = MagicMock()
    mock_classifier.classify.return_value = FailureClassification(
        category=FailureCategory.ENVIRONMENT_ERROR,
        explanation="Docker daemon unavailable",
        auto_repair_permitted=False,
        human_intervention_required=True,
    )

    mock_engine = MagicMock()
    mock_engine.generate_proposal = AsyncMock()

    controller = BoundedRepairController(
        failure_classifier=mock_classifier,
        repair_proposal_engine=mock_engine,
    )

    req = RepairRequest(
        repository_path=str(tmp_path),
        execution_id="exec-test-env",
    )

    result = await controller.run_repair(request=req, initial_report=initial_report)

    assert result.current_state == RepairState.NEEDS_HUMAN_REVIEW
    assert result.human_intervention_required is True
    assert "Docker" in (result.human_intervention_reason or "")
    mock_engine.generate_proposal.assert_not_called()


@pytest.mark.anyio
async def test_repair_controller_high_risk_pause(tmp_path: Path) -> None:
    """Test that high risk strategies pause for human approval when configured."""
    initial_report = _create_mock_report(success=False)

    mock_classifier = MagicMock()
    mock_classifier.classify.return_value = FailureClassification(
        category=FailureCategory.TEST_FAILURE,
        explanation="Multi-file impact",
        auto_repair_permitted=True,
        human_intervention_required=False,
    )

    mock_analyzer = MagicMock()
    mock_analyzer.analyze.return_value = RootCauseAnalysis(
        primary_cause=RootCauseType.BROKEN_INTEGRATION,
        is_repairable_automatically=True,
        explanation="Architecture change required",
    )

    mock_strategy = MagicMock()
    mock_strategy.select_strategy.return_value = RepairStrategy(
        strategy_type=RepairStrategyType.MODIFY_AFFECTED_LOGIC,
        reason="Requires touching multiple core files",
        risk_level=RiskLevel.HIGH,
        code_generation_permitted=True,
        human_approval_required=True,
    )

    mock_engine = MagicMock()
    mock_engine.generate_proposal = AsyncMock()

    controller = BoundedRepairController(
        failure_classifier=mock_classifier,
        root_cause_analyzer=mock_analyzer,
        repair_strategy_engine=mock_strategy,
        repair_proposal_engine=mock_engine,
    )

    req = RepairRequest(
        repository_path=str(tmp_path),
        execution_id="exec-test-risk",
        config=RepairConfiguration(require_approval_for_high_risk=True),
    )

    result = await controller.run_repair(request=req, initial_report=initial_report)

    assert result.current_state == RepairState.AWAITING_APPROVAL
    assert result.human_intervention_required is True
    mock_engine.generate_proposal.assert_not_called()


@pytest.mark.anyio
async def test_repair_controller_loop_detection(tmp_path: Path) -> None:
    """Test identical patches across iterations trigger loop detection."""
    initial_report = _create_mock_report(success=False)
    still_failing_report = _create_mock_report(success=False)

    mock_classifier = MagicMock()
    mock_classifier.classify.return_value = FailureClassification(
        category=FailureCategory.TEST_FAILURE,
        explanation="Persistent failure",
        auto_repair_permitted=True,
        human_intervention_required=False,
    )

    mock_analyzer = MagicMock()
    mock_analyzer.analyze.return_value = RootCauseAnalysis(
        primary_cause=RootCauseType.INCORRECT_LOGIC,
        is_repairable_automatically=True,
    )

    mock_strategy = MagicMock()
    mock_strategy.select_strategy.return_value = RepairStrategy(
        strategy_type=RepairStrategyType.MODIFY_AFFECTED_LOGIC,
        reason="Modify logic",
        risk_level=RiskLevel.LOW,
        code_generation_permitted=True,
        human_approval_required=False,
    )

    # Identical proposal returned every time
    mock_engine = MagicMock()
    proposal = _create_mock_proposal(patch_content="--- identical patch ---")
    mock_engine.generate_proposal = AsyncMock(return_value=proposal)

    mock_evaluator = MagicMock()
    mock_evaluator.evaluate.return_value = RepairEvaluation(
        verdict=RepairEvaluationVerdict.NOT_FIXED,
        original_failures_resolved=False,
        summary="Not fixed",
    )

    mock_exec_service = MagicMock()
    mock_exec_service.execute_and_verify = AsyncMock(return_value=still_failing_report)

    controller = BoundedRepairController(
        failure_classifier=mock_classifier,
        root_cause_analyzer=mock_analyzer,
        repair_strategy_engine=mock_strategy,
        repair_proposal_engine=mock_engine,
        repair_evaluator=mock_evaluator,
        execution_service=mock_exec_service,
    )

    req = RepairRequest(
        repository_path=str(tmp_path),
        execution_id="exec-test-loop",
        config=RepairConfiguration(max_attempts=3),
    )

    result = await controller.run_repair(request=req, initial_report=initial_report)

    assert result.current_state == RepairState.FAILED
    assert "infinite loop" in (result.error_message or "").lower()
    assert result.total_attempts == 2


@pytest.mark.anyio
async def test_repair_controller_max_attempts_reached(tmp_path: Path) -> None:
    """Test that controller halts cleanly when max attempts are exhausted."""
    initial_report = _create_mock_report(success=False)
    still_failing_report = _create_mock_report(success=False)

    mock_classifier = MagicMock()
    mock_classifier.classify.return_value = FailureClassification(
        category=FailureCategory.TEST_FAILURE,
        explanation="Persistent failure",
        auto_repair_permitted=True,
        human_intervention_required=False,
    )

    mock_analyzer = MagicMock()
    mock_analyzer.analyze.return_value = RootCauseAnalysis(
        primary_cause=RootCauseType.INCORRECT_LOGIC,
        is_repairable_automatically=True,
    )

    mock_strategy = MagicMock()
    mock_strategy.select_strategy.return_value = RepairStrategy(
        strategy_type=RepairStrategyType.MODIFY_AFFECTED_LOGIC,
        reason="Modify logic",
        risk_level=RiskLevel.LOW,
        code_generation_permitted=True,
        human_approval_required=False,
    )

    # Varying proposals so loop detection isn't triggered
    attempts_called = 0

    async def _different_proposals(**kwargs: object) -> RepairProposal:
        nonlocal attempts_called
        attempts_called += 1
        return _create_mock_proposal(
            patch_content=f"--- patch attempt {attempts_called} ---"
        )

    mock_engine = MagicMock()
    mock_engine.generate_proposal = AsyncMock(side_effect=_different_proposals)

    mock_evaluator = MagicMock()
    mock_evaluator.evaluate.return_value = RepairEvaluation(
        verdict=RepairEvaluationVerdict.NOT_FIXED,
        original_failures_resolved=False,
        summary="Not fixed",
    )

    mock_exec_service = MagicMock()
    mock_exec_service.execute_and_verify = AsyncMock(return_value=still_failing_report)

    controller = BoundedRepairController(
        failure_classifier=mock_classifier,
        root_cause_analyzer=mock_analyzer,
        repair_strategy_engine=mock_strategy,
        repair_proposal_engine=mock_engine,
        repair_evaluator=mock_evaluator,
        execution_service=mock_exec_service,
    )

    req = RepairRequest(
        repository_path=str(tmp_path),
        execution_id="exec-test-max",
        config=RepairConfiguration(max_attempts=2),
    )

    result = await controller.run_repair(request=req, initial_report=initial_report)

    assert result.current_state == RepairState.MAX_ATTEMPTS_REACHED
    assert result.total_attempts == 2
    assert result.final_verdict == RepairEvaluationVerdict.NOT_FIXED


@pytest.mark.anyio
async def test_debugging_service_validation(tmp_path: Path) -> None:
    """Test DebuggingService input validation and report lookup."""
    mock_exec_service = MagicMock()
    mock_exec_service.get_report.return_value = None

    service = DebuggingService(execution_service=mock_exec_service)

    # 1. Invalid repo
    with pytest.raises(ValueError, match="Repository directory does not exist"):
        await service.start_repair(
            RepairRequest(
                repository_path=str(tmp_path / "nonexistent"),
                execution_id="exec-123",
            )
        )

    # 2. Nonexistent execution report
    with pytest.raises(ValueError, match="Execution report 'exec-123' not found"):
        await service.start_repair(
            RepairRequest(
                repository_path=str(tmp_path),
                execution_id="exec-123",
            )
        )


@pytest.mark.anyio
async def test_debugging_service_session_persistence(tmp_path: Path) -> None:
    """Test that DebuggingService tracks and returns sessions."""
    initial_report = _create_mock_report(success=False)

    mock_exec_service = MagicMock()
    mock_exec_service.get_report.return_value = initial_report

    mock_controller = MagicMock()
    mock_result = MagicMock()
    mock_result.repair_id = "repair-test-persist"
    mock_result.attempts = []
    mock_controller.run_repair = AsyncMock(return_value=mock_result)

    service = DebuggingService(
        controller=mock_controller,
        execution_service=mock_exec_service,
    )

    req = RepairRequest(
        repository_path=str(tmp_path),
        execution_id="exec-exist",
    )

    res = await service.start_repair(req)
    assert res.repair_id == "repair-test-persist"

    assert service.get_repair("repair-test-persist") is mock_result
    assert service.get_repair("nonexistent") is None
    assert len(service.list_repairs()) == 1
    assert service.get_repair_history("repair-test-persist") == []
    assert service.get_repair_history("nonexistent") is None
