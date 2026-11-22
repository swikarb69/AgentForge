"""Unit tests for RepairStrategyEngine."""

from backend.app.debugging.models import (
    DiagnosticEvidence,
    FailureCategory,
    FailureClassification,
    RepairConfiguration,
    RepairStrategyType,
    RiskLevel,
    RootCauseAnalysis,
    RootCauseType,
)
from backend.app.debugging.repair_strategy import RepairStrategyEngine
from backend.app.execution.models import ExecutionStatus
from backend.app.planning.models import ChangeType, FileChange, ImplementationPlan


def _make_evidence(
    files: list[str] | None = None,
    symbols: list[str] | None = None,
) -> DiagnosticEvidence:
    """Helper to build test evidence."""
    return DiagnosticEvidence(
        execution_id="EXEC-TEST-002",
        execution_status=ExecutionStatus.FAILED,
        failed_test_names=["test_sample"],
        error_messages=[],
        traceback_snippets=[],
        relevant_file_paths=files or ["app/core.py"],
        relevant_symbols=symbols or ["run"],
    )


def test_strategy_for_syntax_error() -> None:
    """Syntax errors diagnosed as INCORRECT_LOGIC map to MODIFY_AFFECTED_LOGIC."""
    root_cause = RootCauseAnalysis(
        primary_cause=RootCauseType.INCORRECT_LOGIC,
        affected_files=["app/core.py"],
        affected_symbols=["run"],
        is_repairable_automatically=True,
    )
    classification = FailureClassification(
        category=FailureCategory.SYNTAX_ERROR,
        auto_repair_permitted=True,
    )
    evidence = _make_evidence()
    engine = RepairStrategyEngine()
    strategy = engine.select_strategy(root_cause, classification, evidence)

    assert strategy.strategy_type == RepairStrategyType.MODIFY_AFFECTED_LOGIC
    assert strategy.risk_level == RiskLevel.MEDIUM
    assert strategy.code_generation_permitted
    assert not strategy.human_approval_required


def test_strategy_for_missing_import() -> None:
    """Missing imports should map to ADD_OR_CORRECT_IMPORT with LOW risk."""
    root_cause = RootCauseAnalysis(
        primary_cause=RootCauseType.MISSING_IMPORT,
        affected_files=["app/core.py"],
        is_repairable_automatically=True,
    )
    classification = FailureClassification(
        category=FailureCategory.IMPORT_ERROR,
        auto_repair_permitted=True,
    )
    evidence = _make_evidence()
    engine = RepairStrategyEngine()
    strategy = engine.select_strategy(root_cause, classification, evidence)

    assert strategy.strategy_type == RepairStrategyType.ADD_OR_CORRECT_IMPORT
    assert strategy.risk_level == RiskLevel.LOW


def test_strategy_for_type_mismatch() -> None:
    """Type errors should map to CORRECT_TYPE_HANDLING with MEDIUM risk."""
    root_cause = RootCauseAnalysis(
        primary_cause=RootCauseType.TYPE_MISMATCH,
        affected_files=["app/core.py"],
        is_repairable_automatically=True,
    )
    classification = FailureClassification(
        category=FailureCategory.TYPE_ERROR,
        auto_repair_permitted=True,
    )
    evidence = _make_evidence()
    engine = RepairStrategyEngine()
    strategy = engine.select_strategy(root_cause, classification, evidence)

    assert strategy.strategy_type == RepairStrategyType.CORRECT_TYPE_HANDLING
    assert strategy.risk_level == RiskLevel.MEDIUM


def test_strategy_for_missing_dependency() -> None:
    """Missing dependencies map to RECOMMEND_DEPENDENCY_UPDATE and require approval."""
    root_cause = RootCauseAnalysis(
        primary_cause=RootCauseType.MISSING_DEPENDENCY,
        affected_files=["app/core.py"],
        is_repairable_automatically=False,
    )
    classification = FailureClassification(
        category=FailureCategory.DEPENDENCY_ERROR,
        auto_repair_permitted=False,
    )
    evidence = _make_evidence()
    engine = RepairStrategyEngine()
    strategy = engine.select_strategy(root_cause, classification, evidence)

    assert strategy.strategy_type == RepairStrategyType.RECOMMEND_DEPENDENCY_UPDATE
    assert strategy.risk_level == RiskLevel.HIGH
    assert not strategy.code_generation_permitted
    assert strategy.human_approval_required


def test_strategy_for_single_function_assertion() -> None:
    """Single-function assertion maps to MODIFY_AFFECTED_LOGIC with MEDIUM risk."""
    root_cause = RootCauseAnalysis(
        primary_cause=RootCauseType.TEST_EXPECTATION_MISMATCH,
        affected_files=["app/core.py"],
        affected_symbols=["run"],
        is_repairable_automatically=True,
    )
    classification = FailureClassification(
        category=FailureCategory.TEST_FAILURE,
        auto_repair_permitted=True,
    )
    evidence = _make_evidence(files=["app/core.py"], symbols=["run"])
    engine = RepairStrategyEngine()
    strategy = engine.select_strategy(root_cause, classification, evidence)

    assert strategy.strategy_type == RepairStrategyType.MODIFY_AFFECTED_LOGIC
    assert strategy.risk_level == RiskLevel.MEDIUM


def test_strategy_for_multi_file_assertion() -> None:
    """Multi-file assertion failure maps to MODIFY_AFFECTED_LOGIC with HIGH risk."""
    root_cause = RootCauseAnalysis(
        primary_cause=RootCauseType.TEST_EXPECTATION_MISMATCH,
        affected_files=["app/core.py", "app/utils.py"],
        affected_symbols=["run", "helper"],
        is_repairable_automatically=True,
    )
    classification = FailureClassification(
        category=FailureCategory.TEST_FAILURE,
        auto_repair_permitted=True,
    )
    evidence = _make_evidence(
        files=["app/core.py", "app/utils.py"],
        symbols=["run", "helper"],
    )
    engine = RepairStrategyEngine()
    strategy = engine.select_strategy(root_cause, classification, evidence)

    assert strategy.strategy_type == RepairStrategyType.MODIFY_AFFECTED_LOGIC
    assert strategy.risk_level == RiskLevel.HIGH


def test_strategy_high_risk_approval_required() -> None:
    """When config requires approval for high risk, human approval is flagged."""
    config = RepairConfiguration(require_approval_for_high_risk=True)
    root_cause = RootCauseAnalysis(
        primary_cause=RootCauseType.TEST_EXPECTATION_MISMATCH,
        affected_files=["app/core.py", "app/utils.py"],
        affected_symbols=["run", "helper"],
        is_repairable_automatically=True,
    )
    classification = FailureClassification(
        category=FailureCategory.TEST_FAILURE,
        auto_repair_permitted=True,
    )
    evidence = _make_evidence(
        files=["app/core.py", "app/utils.py"],
        symbols=["run", "helper"],
    )
    engine = RepairStrategyEngine(config=config)
    strategy = engine.select_strategy(root_cause, classification, evidence)

    assert strategy.risk_level == RiskLevel.HIGH
    assert strategy.human_approval_required


def test_strategy_for_unrepairable_environment_error() -> None:
    """Environment defects must trigger STOP_ENVIRONMENT_FAILURE and block code gen."""
    root_cause = RootCauseAnalysis(
        primary_cause=RootCauseType.ENVIRONMENT_ISSUE,
        affected_files=[],
        affected_symbols=[],
        is_repairable_automatically=False,
    )
    classification = FailureClassification(
        category=FailureCategory.ENVIRONMENT_ERROR,
        auto_repair_permitted=False,
        human_intervention_required=True,
    )
    evidence = _make_evidence()
    engine = RepairStrategyEngine()
    strategy = engine.select_strategy(root_cause, classification, evidence)

    assert strategy.strategy_type == RepairStrategyType.STOP_ENVIRONMENT_FAILURE
    assert strategy.risk_level == RiskLevel.CRITICAL
    assert not strategy.code_generation_permitted
    assert strategy.human_approval_required


def test_strategy_target_files_constrained_by_plan() -> None:
    """Target files are filtered to planned files when plan is provided."""
    root_cause = RootCauseAnalysis(
        primary_cause=RootCauseType.TEST_EXPECTATION_MISMATCH,
        affected_files=["app/core.py", "tests/test_core.py"],
        affected_symbols=["run"],
        is_repairable_automatically=True,
    )
    classification = FailureClassification(
        category=FailureCategory.TEST_FAILURE,
        auto_repair_permitted=True,
    )
    evidence = _make_evidence(files=["app/core.py", "tests/test_core.py"])
    plan = ImplementationPlan(
        id="PLAN-1",
        objective="Modify core",
        summary="Update core run logic",
        files_to_modify=[
            FileChange(
                path="app/core.py",
                change_type=ChangeType.MODIFY,
                reason="Update run function",
            )
        ],
    )
    engine = RepairStrategyEngine()
    strategy = engine.select_strategy(root_cause, classification, evidence, plan=plan)

    assert strategy.target_files == ["app/core.py"]
