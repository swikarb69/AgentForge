"""Unit tests for execution subsystem data models."""

import pytest
from pydantic import ValidationError

from backend.app.execution.models import (
    CreateExecutionRequest,
    ExecutionPolicy,
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
    ResourceUsage,
    TestSummary,
    VerificationProfile,
    VerificationReport,
    WorkspaceMetadata,
)


def test_execution_status_enum() -> None:
    """Verify ExecutionStatus enum values."""
    assert ExecutionStatus.PENDING.value == "pending"
    assert ExecutionStatus.PREPARING.value == "preparing"
    assert ExecutionStatus.RUNNING.value == "running"
    assert ExecutionStatus.COMPLETED.value == "completed"
    assert ExecutionStatus.FAILED.value == "failed"
    assert ExecutionStatus.TIMED_OUT.value == "timed_out"
    assert ExecutionStatus.REJECTED.value == "rejected"
    assert ExecutionStatus.CANCELLED.value == "cancelled"


def test_verification_profile_enum() -> None:
    """Verify VerificationProfile enum values."""
    assert VerificationProfile.PYTHON_UNIT_TESTS.value == "python-unit-tests"
    assert VerificationProfile.PYTHON_QUALITY_CHECKS.value == "python-quality-checks"


def test_test_summary_logic() -> None:
    """Verify TestSummary calculations and is_all_passed property."""
    # Zero tests ran
    empty = TestSummary()
    assert empty.total == 0
    assert not empty.is_all_passed

    # All passed
    passed = TestSummary(total=10, passed=10, failed=0, errors=0, skipped=2)
    assert passed.is_all_passed

    # Some failed
    failed = TestSummary(total=10, passed=8, failed=2, errors=0)
    assert not failed.is_all_passed

    # Collection error
    errored = TestSummary(total=1, passed=0, failed=0, errors=1)
    assert not errored.is_all_passed


def test_workspace_metadata() -> None:
    """Verify WorkspaceMetadata defaults and properties."""
    meta = WorkspaceMetadata(
        workspace_id="ws-123",
        workspace_path="/var/agentforge/workspaces/ws-123",
        file_count=42,
        size_bytes=1048576,
    )
    assert meta.workspace_id == "ws-123"
    assert not meta.cleaned_up
    assert meta.file_count == 42
    assert meta.size_bytes == 1048576
    assert meta.created_at is not None


def test_resource_usage() -> None:
    """Verify ResourceUsage optional fields."""
    usage = ResourceUsage(
        cpu_percent=12.5,
        memory_bytes=52428800,
        peak_memory_bytes=62914560,
        duration_seconds=4.32,
    )
    assert usage.cpu_percent == 12.5
    assert usage.memory_bytes == 52428800
    assert usage.peak_memory_bytes == 62914560
    assert usage.duration_seconds == 4.32


def test_execution_policy_defaults() -> None:
    """Verify ExecutionPolicy default settings match security requirements."""
    policy = ExecutionPolicy()
    assert policy.timeout_seconds == 30
    assert policy.memory_limit == "512m"
    assert policy.cpu_limit == 1.0
    assert policy.process_limit == 64
    assert policy.maximum_output_bytes == 100_000
    assert not policy.network_enabled
    assert policy.read_only_root_filesystem
    assert policy.drop_capabilities == ["ALL"]
    assert "pytest" in policy.allowed_commands
    assert "sudo" in policy.denied_command_patterns


def test_execution_policy_memory_limit_validation() -> None:
    """Verify memory limit validator accepts valid units and rejects invalid formats."""
    # Valid formats
    assert ExecutionPolicy(memory_limit="256m").memory_limit == "256m"
    assert ExecutionPolicy(memory_limit="1g").memory_limit == "1g"
    assert ExecutionPolicy(memory_limit="1024k").memory_limit == "1024k"
    assert ExecutionPolicy(memory_limit=" 512M ").memory_limit == "512m"

    # Missing unit
    with pytest.raises(ValidationError):
        ExecutionPolicy(memory_limit="512")

    # Invalid unit
    with pytest.raises(ValidationError):
        ExecutionPolicy(memory_limit="512x")

    # Non-numeric
    with pytest.raises(ValidationError):
        ExecutionPolicy(memory_limit="abcm")

    # Negative / zero numeric
    with pytest.raises(ValidationError):
        ExecutionPolicy(memory_limit="0m")


def test_execution_policy_bounds() -> None:
    """Verify bounds validation on ExecutionPolicy integer/float limits."""
    # Timeout out of bounds
    with pytest.raises(ValidationError):
        ExecutionPolicy(timeout_seconds=0)
    with pytest.raises(ValidationError):
        ExecutionPolicy(timeout_seconds=500)

    # CPU limit out of bounds
    with pytest.raises(ValidationError):
        ExecutionPolicy(cpu_limit=0.0)
    with pytest.raises(ValidationError):
        ExecutionPolicy(cpu_limit=5.0)

    # Process limit out of bounds
    with pytest.raises(ValidationError):
        ExecutionPolicy(process_limit=0)
    with pytest.raises(ValidationError):
        ExecutionPolicy(process_limit=500)


def test_execution_request() -> None:
    """Verify ExecutionRequest instantiation and defaults."""
    req = ExecutionRequest(
        execution_id="exec-456",
        repository_path="/workspace/repo",
    )
    assert req.execution_id == "exec-456"
    assert req.repository_path == "/workspace/repo"
    assert req.requested_verification_profile == VerificationProfile.PYTHON_UNIT_TESTS
    assert req.policy.timeout_seconds == 30
    assert req.generation_id is None
    assert req.generation_result is None


def test_execution_result() -> None:
    """Verify ExecutionResult construction."""
    res = ExecutionResult(
        execution_id="exec-789",
        status=ExecutionStatus.COMPLETED,
        exit_code=0,
        stdout="5 passed in 0.12s\n",
        stderr="",
        test_summary=TestSummary(total=5, passed=5),
        duration_seconds=1.25,
    )
    assert res.execution_id == "exec-789"
    assert res.status == ExecutionStatus.COMPLETED
    assert res.exit_code == 0
    assert res.test_summary is not None
    assert res.test_summary.is_all_passed


def test_verification_report() -> None:
    """Verify VerificationReport synthesis model."""
    report = VerificationReport(
        execution_id="exec-999",
        execution_success=True,
        patch_applied=True,
        tests_executed=True,
        verification_passed=True,
        execution_result=ExecutionResult(
            execution_id="exec-999",
            status=ExecutionStatus.COMPLETED,
            exit_code=0,
            test_summary=TestSummary(total=5, passed=5),
        ),
    )
    assert report.execution_id == "exec-999"
    assert report.execution_success
    assert report.patch_applied
    assert report.tests_executed
    assert report.verification_passed
    assert report.execution_result is not None
    assert report.execution_result.exit_code == 0
    assert report.failures == []


def test_create_execution_request() -> None:
    """Verify CreateExecutionRequest API schema."""
    api_req = CreateExecutionRequest(
        repository_path="/repos/sample",
        generation_id="gen-100",
        verification_profile=VerificationProfile.PYTHON_QUALITY_CHECKS,
    )
    assert api_req.repository_path == "/repos/sample"
    assert api_req.generation_id == "gen-100"
    assert (
        api_req.verification_profile
        == VerificationProfile.PYTHON_QUALITY_CHECKS
    )
    assert api_req.policy is None
