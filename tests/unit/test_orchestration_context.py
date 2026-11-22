"""Unit tests for workflow execution context and artifact reference tracking."""

from backend.app.orchestration.context import (
    WorkflowArtifactReferences,
    WorkflowContext,
)
from backend.app.orchestration.states import StageType


def test_workflow_artifact_references_defaults() -> None:
    """Verify artifact references default to None."""
    refs = WorkflowArtifactReferences()
    assert refs.repository_analysis_id is None
    assert refs.repository_analysis_hash is None
    assert refs.issue_analysis_id is None
    assert refs.implementation_plan_id is None
    assert refs.generation_id is None
    assert refs.execution_id is None
    assert refs.repair_id is None
    assert refs.review_id is None
    assert refs.approval_id is None
    assert refs.release_id is None
    assert refs.release_pr_number is None
    assert refs.release_pr_url is None


def test_workflow_context_initialization_and_helpers() -> None:
    """Verify WorkflowContext initialization, stage output tracking, and patch hash."""
    ctx = WorkflowContext(
        workflow_id="wf-ctx-test",
        repository_id="owner/repo",
        issue_reference="issue-123",
        target_branch="main",
    )
    assert ctx.workflow_id == "wf-ctx-test"
    assert ctx.repository_id == "owner/repo"
    assert ctx.issue_reference == "issue-123"
    assert ctx.target_branch == "main"
    assert ctx.current_stage is None
    assert ctx.stage_outputs == {}
    assert ctx.execution_history == []

    # Record stage outputs
    ctx.record_stage_output(
        StageType.REPOSITORY_ANALYSIS,
        {"files_count": 42, "language": "python"},
    )
    out = ctx.get_stage_output(StageType.REPOSITORY_ANALYSIS)
    assert out is not None
    assert out["files_count"] == 42
    assert len(ctx.execution_history) == 1
    assert ctx.execution_history[0]["stage"] == "repository_analysis"


def test_workflow_context_effective_patch_hash() -> None:
    """Verify effective patch hash prioritization (repair patch over initial patch)."""
    ctx = WorkflowContext(
        workflow_id="wf-patch-test",
        repository_id="owner/repo",
        issue_reference="issue-456",
    )

    # Initially None
    assert ctx.get_effective_patch_hash() is None

    # After generation
    ctx.artifacts.generation_patch_hash = "a" * 64
    assert ctx.get_effective_patch_hash() == "a" * 64

    # After repair, repair patch takes precedence
    ctx.artifacts.repair_patch_hash = "b" * 64
    assert ctx.get_effective_patch_hash() == "b" * 64


def test_workflow_context_has_artifact_check() -> None:
    """Verify has_artifact accurately checks artifact reference population."""
    ctx = WorkflowContext(
        workflow_id="wf-art-test",
        repository_id="owner/repo",
        issue_reference="issue-789",
    )

    assert not ctx.has_artifact("review_id")
    assert not ctx.has_artifact("approval_id")

    ctx.artifacts.review_id = "rev-999"
    assert ctx.has_artifact("review_id")
    assert not ctx.has_artifact("approval_id")

    ctx.artifacts.approval_id = "appr-888"
    assert ctx.has_artifact("approval_id")
