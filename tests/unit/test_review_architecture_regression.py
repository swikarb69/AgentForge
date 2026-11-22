"""Unit tests for ArchitectureAnalyzer and RegressionAnalyzer."""

from pathlib import Path

import pytest

from backend.app.debugging.models import RepairResult, RepairState
from backend.app.execution.models import (
    ExecutionResult,
    ExecutionStatus,
    TestSummary,
    VerificationReport,
)
from backend.app.generation.models import GeneratedFileChange
from backend.app.issue.models import (
    AcceptanceCriterion,
    Issue,
    IssueAnalysis,
    IssueContext,
)
from backend.app.planning.models import (
    ChangeType,
    FileChange,
    ImplementationPlan,
    PlanTask,
)
from backend.app.review.architecture_analyzer import ArchitectureAnalyzer
from backend.app.review.models import (
    FindingCategory,
    FindingSeverity,
    ReviewConfiguration,
)
from backend.app.review.regression_analyzer import RegressionAnalyzer


@pytest.fixture
def arch_analyzer() -> ArchitectureAnalyzer:
    """Fixture providing ArchitectureAnalyzer instance."""
    return ArchitectureAnalyzer()


@pytest.fixture
def reg_analyzer() -> RegressionAnalyzer:
    """Fixture providing RegressionAnalyzer instance."""
    return RegressionAnalyzer()


# ---------------------------------------------------------------------------
# Architecture Analyzer Tests
# ---------------------------------------------------------------------------


def test_clean_architecture_produces_no_findings(
    arch_analyzer: ArchitectureAnalyzer,
) -> None:
    files = {
        "backend/app/service.py": "import os\nfrom backend.app.models import User\n",
        "backend/app/models.py": "import json\nclass User: pass\n",
    }
    findings = arch_analyzer.analyze_files(files)
    assert len(findings) == 0


def test_circular_imports_detection(
    arch_analyzer: ArchitectureAnalyzer,
) -> None:
    files = {
        "backend/app/module_a.py": "from backend.app.module_b import func_b\n",
        "backend/app/module_b.py": "from backend.app.module_a import func_a\n",
    }
    findings = arch_analyzer.analyze_files(files)
    cycles = [f for f in findings if f.rule_id == "ARCH-001"]
    assert len(cycles) >= 1
    assert cycles[0].severity == FindingSeverity.HIGH
    assert "circular" in cycles[0].description.lower()
    assert cycles[0].requires_human_review is True


def test_layer_boundary_violation(arch_analyzer: ArchitectureAnalyzer) -> None:
    files = {
        "backend/app/models.py": (
            "from backend.app.api.routes import router\nclass M: pass\n"
        ),
    }
    findings = arch_analyzer.analyze_files(files)
    layer_findings = [f for f in findings if f.rule_id == "ARCH-002"]
    assert len(layer_findings) == 1
    assert layer_findings[0].severity == FindingSeverity.HIGH
    assert "layer boundary violation" in layer_findings[0].description.lower()


def test_unapproved_dependency_detection(
    arch_analyzer: ArchitectureAnalyzer,
) -> None:
    files = {
        "backend/app/service.py": (
            "import unapproved_random_sdk_xyz\nimport os\nimport fastapi\n"
        ),
    }
    findings = arch_analyzer.analyze_files(files)
    dep_findings = [f for f in findings if f.rule_id == "ARCH-003"]
    assert len(dep_findings) == 1
    assert "unapproved_random_sdk_xyz" in dep_findings[0].description


def test_excessive_module_coupling(arch_analyzer: ArchitectureAnalyzer) -> None:
    imports = "\n".join(f"import backend.app.submod_{i}" for i in range(18))
    files = {
        "backend/app/central.py": imports + "\n",
    }
    findings = arch_analyzer.analyze_files(files)
    coupling_findings = [f for f in findings if f.rule_id == "ARCH-004"]
    assert len(coupling_findings) == 1
    assert coupling_findings[0].severity == FindingSeverity.MEDIUM


def test_arch_analyzer_repository_scan(
    arch_analyzer: ArchitectureAnalyzer, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "models.py").write_text(
        "from backend.app.api.routes import r\n", encoding="utf-8"
    )
    findings = arch_analyzer.analyze_repository(repo_dir)
    assert len(findings) == 1
    assert findings[0].rule_id == "ARCH-002"


# ---------------------------------------------------------------------------
# Regression Analyzer Tests
# ---------------------------------------------------------------------------


def test_unauthorized_file_modification(reg_analyzer: RegressionAnalyzer) -> None:
    plan = ImplementationPlan(
        id="PLAN-42",
        objective="Update auth",
        summary="Auth enhancements",
        files_to_modify=[
            FileChange(
                path="backend/app/auth.py",
                change_type=ChangeType.MODIFY,
                reason="Update token logic",
            )
        ],
    )
    generated_changes = [
        GeneratedFileChange(
            path="backend/app/auth.py",
            change_type=ChangeType.MODIFY,
            content="",
        ),
        GeneratedFileChange(
            path="backend/app/payments.py",
            change_type=ChangeType.MODIFY,
            content="",
        ),
    ]

    findings = reg_analyzer.analyze_plan_compliance(plan, generated_changes)
    unauthorized = [f for f in findings if f.rule_id == "REG-001"]
    assert len(unauthorized) == 1
    assert "payments.py" in unauthorized[0].description
    assert unauthorized[0].severity == FindingSeverity.HIGH
    assert unauthorized[0].requires_human_review is True


def test_missing_planned_target_implementation(
    reg_analyzer: RegressionAnalyzer,
) -> None:
    plan = ImplementationPlan(
        id="PLAN-43",
        objective="Refactor users and orders",
        summary="User and order changes",
        files_to_modify=[
            FileChange(
                path="backend/app/users.py",
                change_type=ChangeType.MODIFY,
                reason="User update",
            ),
            FileChange(
                path="backend/app/orders.py",
                change_type=ChangeType.MODIFY,
                reason="Order update",
            ),
        ],
    )
    generated_changes = ["backend/app/users.py"]

    findings = reg_analyzer.analyze_plan_compliance(plan, generated_changes)
    missing = [f for f in findings if f.rule_id == "REG-002"]
    assert len(missing) == 1
    assert "orders.py" in missing[0].description
    assert missing[0].severity == FindingSeverity.MEDIUM


def test_verification_compliance_failures(
    reg_analyzer: RegressionAnalyzer,
) -> None:
    report = VerificationReport(
        execution_id="EXEC-99",
        execution_success=False,
        verification_passed=False,
        failures=["AssertionError: assert 1 == 2 in test_login"],
        execution_result=ExecutionResult(
            execution_id="EXEC-99",
            status=ExecutionStatus.FAILED,
            exit_code=1,
            test_summary=TestSummary(total=10, passed=9, failed=1, errors=0),
        ),
    )
    findings = reg_analyzer.analyze_verification_compliance(report)
    assert len(findings) == 1
    assert findings[0].rule_id == "REG-003"
    assert findings[0].severity == FindingSeverity.CRITICAL
    assert findings[0].requires_human_review is True


def test_repair_compliance_failures(reg_analyzer: RegressionAnalyzer) -> None:
    repair_result = RepairResult(
        repair_id="REP-101",
        repository_path="/repo",
        initial_execution_id="EXEC-99",
        current_state=RepairState.FAILED,
        error_message="All 3 repair attempts exhausted without resolution.",
    )
    findings = reg_analyzer.analyze_repair_compliance(repair_result)
    assert len(findings) == 1
    assert findings[0].rule_id == "REG-004"
    assert findings[0].severity == FindingSeverity.CRITICAL
    assert "All 3 repair attempts" in findings[0].description


def test_issue_acceptance_criteria_unmet(
    reg_analyzer: RegressionAnalyzer,
) -> None:
    analysis = IssueAnalysis(
        issue=Issue(
            id="ISSUE-55",
            title="Payment gateway",
            description="Add stripe payment integration",
        ),
        requirements=[],
        acceptance_criteria=[
            AcceptanceCriterion(
                id="AC-1",
                description="Webhook handles checkout.session.completed",
            ),
            AcceptanceCriterion(
                id="AC-2",
                description="Payment status saved to database",
            ),
        ],
        constraints=[],
        keywords=[],
        references=[],
        endpoints=[],
    )
    issue_context = IssueContext(
        analysis=analysis,
        relevant_files=[],
        relevant_symbols=[],
        relevant_chunks=[],
        matches=[],
    )
    plan = ImplementationPlan(
        id="PLAN-55",
        objective="Integrate Stripe",
        summary="Webhook handler",
        tasks=[
            PlanTask(
                task_id="TASK-1",
                title="Create webhook route",
                description="Route implementation",
                order=1,
                target_files=["backend/app/webhook.py"],
                change_type=ChangeType.CREATE,
                verification="Check route responds 200",
                addressed_acceptance_criteria=["AC-1"],
            )
        ],
    )

    findings = reg_analyzer.analyze_issue_compliance(issue_context, plan)
    assert len(findings) == 1
    assert "AC-2" in findings[0].description


def test_assertless_and_skipped_test_quality(
    reg_analyzer: RegressionAnalyzer,
) -> None:
    code = """
import pytest

def test_valid_assertion():
    assert 1 + 1 == 2

def test_empty_no_assertions():
    x = 10
    y = 20

@pytest.mark.skip(reason="Temporarily disabled")
def test_skipped_one():
    assert False

def test_with_pytest_raises():
    with pytest.raises(ValueError):
        raise ValueError("error")
"""
    files = {"tests/unit/test_sample.py": code}
    findings = reg_analyzer.analyze_test_quality(files)

    empty_findings = [f for f in findings if f.rule_id == "TEST-001"]
    skipped_findings = [f for f in findings if f.rule_id == "TEST-002"]

    assert len(empty_findings) == 1
    assert empty_findings[0].affected_symbol == "test_empty_no_assertions"

    assert len(skipped_findings) == 1
    assert skipped_findings[0].affected_symbol == "test_skipped_one"


def test_regression_config_filters(reg_analyzer: RegressionAnalyzer) -> None:
    report = VerificationReport(
        execution_id="EXEC-1",
        verification_passed=False,
        failures=["Test error"],
    )
    # Disabled category
    cfg = ReviewConfiguration(enabled_categories=[FindingCategory.MAINTAINABILITY])
    findings = reg_analyzer.analyze_verification_compliance(report, cfg)
    assert len(findings) == 0

    # Disabled rule
    cfg2 = ReviewConfiguration(disabled_rule_ids=["REG-003"])
    findings2 = reg_analyzer.analyze_verification_compliance(report, cfg2)
    assert len(findings2) == 0
