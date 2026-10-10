"""Unit tests for planning domain models."""

import pytest
from pydantic import ValidationError

from backend.app.planning.models import (
    Assumption,
    ChangeType,
    CreatePlanRequest,
    Dependency,
    FileChange,
    ImplementationPlan,
    PlanStatus,
    PlanTask,
    PlanValidation,
    Risk,
    RiskSeverity,
    SymbolChange,
    TestPlan,
    ValidatePlanRequest,
    VerificationCase,
)
from backend.app.repository.models import SymbolType


def test_file_change_model() -> None:
    """Verify FileChange model fields and serialization."""
    fc = FileChange(
        path="backend/app/auth.py",
        change_type=ChangeType.MODIFY,
        reason="Update token verification",
        affected_symbols=["verify_token"],
    )
    assert fc.path == "backend/app/auth.py"
    assert fc.change_type == ChangeType.MODIFY
    assert fc.reason == "Update token verification"
    assert "verify_token" in fc.affected_symbols

    dumped = fc.model_dump()
    assert dumped["path"] == "backend/app/auth.py"
    assert dumped["change_type"] == "modify"


def test_symbol_change_model() -> None:
    """Verify SymbolChange model fields."""
    sc = SymbolChange(
        file_path="backend/app/auth.py",
        symbol_name="verify_token",
        symbol_type=SymbolType.FUNCTION,
        change_type=ChangeType.MODIFY,
        reason="Check token expiry",
    )
    assert sc.symbol_name == "verify_token"
    assert sc.symbol_type == "function"
    assert sc.change_type == ChangeType.MODIFY


def test_plan_task_model() -> None:
    """Verify PlanTask model fields and requirements traceability."""
    task = PlanTask(
        task_id="TASK-001",
        title="Reject expired JWT tokens",
        description="Raise 401 when token expiry is in past",
        order=1,
        target_files=["backend/app/auth.py"],
        target_symbols=["verify_token"],
        change_type=ChangeType.MODIFY,
        dependencies=[],
        verification="Unit test with expired timestamp",
        addressed_requirements=["REQ-1"],
        addressed_acceptance_criteria=["AC-1"],
    )
    assert task.task_id == "TASK-001"
    assert task.order == 1
    assert task.addressed_requirements == ["REQ-1"]
    assert task.addressed_acceptance_criteria == ["AC-1"]


def test_test_plan_and_verification_case() -> None:
    """Verify TestPlan and VerificationCase structures."""
    vc = VerificationCase(
        id="VC-001",
        description="Verify expired token rejection",
        expected_behavior="HTTP 401 Unauthorized",
        acceptance_criterion_id="AC-1",
    )
    tp = TestPlan(
        existing_tests=["tests/unit/test_auth.py"],
        tests_to_modify=["tests/unit/test_auth.py"],
        tests_to_add=[],
        verification_cases=[vc],
    )
    assert len(tp.verification_cases) == 1
    assert tp.verification_cases[0].id == "VC-001"
    assert tp.existing_tests == ["tests/unit/test_auth.py"]


def test_dependency_and_risk_models() -> None:
    """Verify Dependency and Risk models."""
    dep = Dependency(
        source_task_id="TASK-002",
        target_task_id="TASK-001",
        reason="Requires verify_token implementation first",
    )
    assert dep.source_task_id == "TASK-002"
    assert dep.target_task_id == "TASK-001"

    risk = Risk(
        risk="Regression in existing valid logins",
        severity=RiskSeverity.HIGH,
        reason="verify_token is used across all endpoints",
        mitigation="Add regression test suite for valid tokens",
    )
    assert risk.severity == RiskSeverity.HIGH


def test_implementation_plan_full_lifecycle() -> None:
    """Verify complete ImplementationPlan instantiation and validation report."""
    plan = ImplementationPlan(
        id="PLAN-101",
        issue_id="ISSUE-101",
        objective="Fix expired JWT validation",
        summary="Update verify_token logic and add comprehensive regression tests",
        status=PlanStatus.READY,
        assumptions=[
            Assumption(
                assumption="JWT signature algorithm remains HMAC-SHA256",
                reason="Specified in security policy",
            )
        ],
        files_to_modify=[
            FileChange(
                path="backend/app/auth.py",
                change_type=ChangeType.MODIFY,
                reason="Update verify_token",
                affected_symbols=["verify_token"],
            )
        ],
        tasks=[
            PlanTask(
                task_id="TASK-001",
                title="Update token verification",
                description="Check expiry timestamp",
                order=1,
                target_files=["backend/app/auth.py"],
                change_type=ChangeType.MODIFY,
                verification="Run pytest tests/unit/test_auth.py",
            )
        ],
        test_plan=TestPlan(
            existing_tests=["tests/unit/test_auth.py"],
            verification_cases=[
                VerificationCase(
                    id="VC-1",
                    description="Expired token raises error",
                    expected_behavior="401 Unauthorized",
                )
            ],
        ),
        risks=[
            Risk(
                risk="Auth failure",
                severity=RiskSeverity.MEDIUM,
                reason="Token format changes",
                mitigation="Preserve format",
            )
        ],
        validation=PlanValidation(
            valid=True,
            status=PlanStatus.READY,
            errors=[],
            warnings=[],
        ),
    )

    assert plan.id == "PLAN-101"
    assert plan.status == PlanStatus.READY
    assert len(plan.files_to_modify) == 1
    assert len(plan.tasks) == 1
    assert plan.validation.valid is True

    # Test JSON round-trip
    json_data = plan.model_dump_json()
    assert "PLAN-101" in json_data
    restored = ImplementationPlan.model_validate_json(json_data)
    assert restored.id == plan.id
    assert restored.objective == plan.objective


def test_implementation_plan_validation_errors() -> None:
    """Verify that required fields in plan raise ValidationError when missing."""
    with pytest.raises(ValidationError):
        # objective and summary are required
        ImplementationPlan()  # type: ignore[call-arg]


def test_api_requests() -> None:
    """Verify CreatePlanRequest and ValidatePlanRequest models."""
    req = CreatePlanRequest(
        repository_path="/workspace/project",
        title="Add feature",
        description="Detailed description",
    )
    assert req.repository_path == "/workspace/project"
    assert req.id is None

    plan = ImplementationPlan(
        objective="Test objective",
        summary="Test summary",
    )
    val_req = ValidatePlanRequest(plan=plan)
    assert val_req.plan.objective == "Test objective"
