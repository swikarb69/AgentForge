"""Unit tests for RepairProposalEngine."""

import pytest

from backend.app.debugging.models import (
    DiagnosticEvidence,
    RepairStrategy,
    RepairStrategyType,
    RiskLevel,
    RootCauseAnalysis,
    RootCauseType,
)
from backend.app.debugging.repair_engine import RepairProposalEngine
from backend.app.execution.models import ExecutionStatus
from backend.app.generation.models import (
    FileContext,
    GeneratedFileChange,
    GenerationStatus,
    SourceContext,
)
from backend.app.generation.provider import MockCodeGenerationProvider
from backend.app.planning.models import ChangeType, FileChange, ImplementationPlan


def _make_plan() -> ImplementationPlan:
    """Fixture implementation plan authorizing app/calculator.py."""
    return ImplementationPlan(
        id="PLAN-TEST-1",
        objective="Fix calculator addition",
        summary="Fix calculator implementation logic",
        files_to_modify=[
            FileChange(
                path="app/calculator.py",
                change_type=ChangeType.MODIFY,
                reason="Fix add function logic",
            )
        ],
    )


def _make_source_context() -> SourceContext:
    """Fixture source context containing app/calculator.py."""
    original_code = "def add(a, b):\n    return a - b\n"
    return SourceContext(
        repository_path="/mock/repo",
        target_files={
            "app/calculator.py": FileContext(
                path="app/calculator.py",
                change_type=ChangeType.MODIFY,
                original_content=original_code,
                size_bytes=len(original_code),
            )
        },
    )


def _make_evidence() -> DiagnosticEvidence:
    """Fixture diagnostic evidence."""
    return DiagnosticEvidence(
        execution_id="EXEC-REPAIR-001",
        execution_status=ExecutionStatus.FAILED,
        failed_test_names=["tests/test_calculator.py::test_add"],
        error_messages=["AssertionError: assert -1 == 3"],
        traceback_snippets=[],
        relevant_file_paths=["app/calculator.py"],
        relevant_symbols=["add"],
        acceptance_criteria=["add(1, 2) == 3"],
    )


@pytest.mark.anyio
async def test_generate_proposal_blocked_by_strategy() -> None:
    """When strategy disallows code generation, an unexecuted proposal is returned."""
    strategy = RepairStrategy(
        strategy_type=RepairStrategyType.STOP_ENVIRONMENT_FAILURE,
        reason="Environment failure requires Docker check",
        target_files=[],
        target_symbols=[],
        expected_changes=["Docker check"],
        risk_level=RiskLevel.CRITICAL,
        code_generation_permitted=False,
        human_approval_required=True,
    )
    root_cause = RootCauseAnalysis(
        primary_cause=RootCauseType.ENVIRONMENT_ISSUE,
        is_repairable_automatically=False,
    )
    evidence = _make_evidence()
    plan = _make_plan()
    context = _make_source_context()

    engine = RepairProposalEngine()
    proposal = await engine.generate_proposal(
        repair_id="REP-001",
        attempt_number=1,
        strategy=strategy,
        root_cause=root_cause,
        evidence=evidence,
        plan=plan,
        source_context=context,
    )

    assert not proposal.strategy.code_generation_permitted
    assert proposal.file_changes == []
    assert proposal.patch_content == ""
    assert proposal.validation is not None
    assert not proposal.validation.valid
    assert proposal.validation.status == GenerationStatus.REJECTED


@pytest.mark.anyio
async def test_generate_proposal_successful() -> None:
    """Provider produces valid fix, patch is generated, validation passes."""
    strategy = RepairStrategy(
        strategy_type=RepairStrategyType.MODIFY_AFFECTED_LOGIC,
        reason="Fix add function logic",
        target_files=["app/calculator.py"],
        target_symbols=["add"],
        expected_changes=["Return a + b"],
        risk_level=RiskLevel.MEDIUM,
        code_generation_permitted=True,
        human_approval_required=False,
    )
    root_cause = RootCauseAnalysis(
        primary_cause=RootCauseType.TEST_EXPECTATION_MISMATCH,
        affected_files=["app/calculator.py"],
        affected_symbols=["add"],
        is_repairable_automatically=True,
    )
    evidence = _make_evidence()
    plan = _make_plan()
    context = _make_source_context()

    fixed_code = "def add(a, b):\n    return a + b\n"
    mock_provider = MockCodeGenerationProvider(
        custom_changes=[
            GeneratedFileChange(
                path="app/calculator.py",
                change_type=ChangeType.MODIFY,
                content=fixed_code,
            )
        ]
    )

    engine = RepairProposalEngine(provider=mock_provider)
    proposal = await engine.generate_proposal(
        repair_id="REP-002",
        attempt_number=1,
        strategy=strategy,
        root_cause=root_cause,
        evidence=evidence,
        plan=plan,
        source_context=context,
    )

    assert proposal.validation is not None
    assert proposal.validation.valid
    assert len(proposal.file_changes) == 1
    assert "return a + b" in proposal.file_changes[0].content
    assert proposal.patch_content != ""
    assert "--- a/app/calculator.py" in proposal.patch_content
    assert "+++ b/app/calculator.py" in proposal.patch_content


@pytest.mark.anyio
async def test_generate_proposal_unauthorized_scope_rejected() -> None:
    """Changes touching files outside the plan must be rejected by validator."""
    strategy = RepairStrategy(
        strategy_type=RepairStrategyType.MODIFY_AFFECTED_LOGIC,
        reason="Fix logic",
        target_files=["app/unauthorized.py"],
        code_generation_permitted=True,
    )
    root_cause = RootCauseAnalysis(
        primary_cause=RootCauseType.TEST_EXPECTATION_MISMATCH,
        is_repairable_automatically=True,
    )
    evidence = _make_evidence()
    plan = _make_plan()
    context = _make_source_context()

    mock_provider = MockCodeGenerationProvider(
        custom_changes=[
            GeneratedFileChange(
                path="app/unauthorized.py",
                change_type=ChangeType.MODIFY,
                content="def rogue(): pass\n",
            )
        ]
    )

    engine = RepairProposalEngine(provider=mock_provider)
    proposal = await engine.generate_proposal(
        repair_id="REP-003",
        attempt_number=1,
        strategy=strategy,
        root_cause=root_cause,
        evidence=evidence,
        plan=plan,
        source_context=context,
    )

    assert proposal.validation is not None
    assert not proposal.validation.valid
    assert len(proposal.validation.plan_scope_violations) > 0
    assert proposal.patch_content == ""


@pytest.mark.anyio
async def test_generate_proposal_syntax_error_rejected() -> None:
    """Changes with Python syntax errors must be caught and rejected."""
    strategy = RepairStrategy(
        strategy_type=RepairStrategyType.MODIFY_AFFECTED_LOGIC,
        reason="Fix logic",
        target_files=["app/calculator.py"],
        code_generation_permitted=True,
    )
    root_cause = RootCauseAnalysis(
        primary_cause=RootCauseType.TEST_EXPECTATION_MISMATCH,
        is_repairable_automatically=True,
    )
    evidence = _make_evidence()
    plan = _make_plan()
    context = _make_source_context()

    mock_provider = MockCodeGenerationProvider(
        custom_changes=[
            GeneratedFileChange(
                path="app/calculator.py",
                change_type=ChangeType.MODIFY,
                content="def broken_syntax(\n",  # Unclosed paren / invalid syntax
            )
        ]
    )

    engine = RepairProposalEngine(provider=mock_provider)
    proposal = await engine.generate_proposal(
        repair_id="REP-004",
        attempt_number=1,
        strategy=strategy,
        root_cause=root_cause,
        evidence=evidence,
        plan=plan,
        source_context=context,
    )

    assert proposal.validation is not None
    assert not proposal.validation.valid
    assert any("syntax" in err.lower() for err in proposal.validation.errors)
    assert proposal.patch_content == ""
