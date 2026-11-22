"""Unit tests for orchestration data models and validation."""

import pytest
from pydantic import ValidationError

from backend.app.orchestration.models import (
    ExecutionLimits,
    StateTransitionRecord,
    WorkflowConfiguration,
    WorkflowRequest,
    WorkflowRun,
    WorkflowStage,
)
from backend.app.orchestration.states import (
    StageStatus,
    StageType,
    WorkflowStatus,
)


def test_execution_limits_defaults_and_validation() -> None:
    """Verify default execution limits and range boundaries."""
    limits = ExecutionLimits()
    assert limits.max_stage_retries == 2
    assert limits.stage_timeout_seconds == 300
    assert limits.workflow_timeout_seconds == 1800
    assert limits.max_repair_attempts == 3

    # Test out of range max_stage_retries
    with pytest.raises(ValidationError):
        ExecutionLimits(max_stage_retries=10)

    with pytest.raises(ValidationError):
        ExecutionLimits(max_stage_retries=-1)

    # Test out of range timeouts
    with pytest.raises(ValidationError):
        ExecutionLimits(stage_timeout_seconds=5)

    with pytest.raises(ValidationError):
        ExecutionLimits(workflow_timeout_seconds=30)


def test_workflow_configuration_defaults() -> None:
    """Verify configuration flags and custom settings."""
    cfg = WorkflowConfiguration()
    assert cfg.auto_repair_enabled is True
    assert cfg.review_enabled is True
    assert cfg.dry_run is False
    assert cfg.target_branch == "main"
    assert cfg.custom_settings == {}

    custom_cfg = WorkflowConfiguration(
        auto_repair_enabled=False,
        dry_run=True,
        target_branch="develop",
        custom_settings={"timeout_scale": 1.5},
    )
    assert custom_cfg.auto_repair_enabled is False
    assert custom_cfg.dry_run is True
    assert custom_cfg.target_branch == "develop"
    assert custom_cfg.custom_settings["timeout_scale"] == 1.5


def test_workflow_request_validation() -> None:
    """Verify WorkflowRequest required fields and string validation."""
    req = WorkflowRequest(
        repository_id="repo-123",
        issue_reference="issue-456",
        requested_by="engineer-1",
    )
    assert req.workflow_id.startswith("wf-")
    assert req.repository_id == "repo-123"
    assert req.issue_reference == "issue-456"
    assert req.requested_by == "engineer-1"
    assert req.target_branch == "main"
    assert req.idempotency_key is None

    # Test empty or whitespace fields
    with pytest.raises(ValidationError):
        WorkflowRequest(
            repository_id="   ",
            issue_reference="issue-1",
            requested_by="user",
        )

    with pytest.raises(ValidationError):
        WorkflowRequest(
            repository_id="repo-1",
            issue_reference="",
            requested_by="user",
        )


def test_workflow_stage_initialization() -> None:
    """Verify WorkflowStage defaults and lifecycle properties."""
    stage = WorkflowStage(stage_type=StageType.CODE_GENERATION)
    assert stage.stage_id.startswith("stg-")
    assert stage.stage_type == StageType.CODE_GENERATION
    assert stage.status == StageStatus.PENDING
    assert stage.input_artifact_ids == []
    assert stage.output_artifact_ids == []
    assert stage.started_at is None
    assert stage.completed_at is None
    assert stage.attempt_count == 0
    assert stage.duration_seconds == 0.0
    assert stage.error is None


def test_workflow_run_lifecycle_helpers() -> None:
    """Verify WorkflowRun state inspectability and stage lookups."""
    run = WorkflowRun(
        workflow_id="wf-test-01",
        requested_by="lead_dev",
        repository_reference="swikarb69/AgentForge",
        issue_reference="42",
    )
    assert run.workflow_id == "wf-test-01"
    assert run.status == WorkflowStatus.CREATED
    assert run.is_terminal is False

    # Get non-existent stage
    assert run.get_stage(StageType.REPOSITORY_ANALYSIS) is None

    # Get or create stage
    stage = run.get_or_create_stage(StageType.REPOSITORY_ANALYSIS)
    assert stage.stage_type == StageType.REPOSITORY_ANALYSIS
    assert run.get_stage(StageType.REPOSITORY_ANALYSIS) == stage

    # Record stage completion idempotently
    run.add_completed_stage(StageType.REPOSITORY_ANALYSIS)
    run.add_completed_stage(StageType.REPOSITORY_ANALYSIS)
    assert run.completed_stages == [StageType.REPOSITORY_ANALYSIS]


def test_state_transition_record_fields() -> None:
    """Verify transition record fields and auto-generated IDs."""
    record = StateTransitionRecord(
        from_state=WorkflowStatus.CREATED,
        to_state=WorkflowStatus.QUEUED,
        reason="Scheduled by scheduler",
        actor="system",
    )
    assert record.transition_id.startswith("tr-")
    assert record.from_state == WorkflowStatus.CREATED
    assert record.to_state == WorkflowStatus.QUEUED
    assert record.reason == "Scheduled by scheduler"
    assert record.actor == "system"
    assert record.timestamp is not None
