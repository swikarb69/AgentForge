"""Unit tests for issue domain models."""

from backend.app.issue.models import (
    AcceptanceCriterion,
    Issue,
    IssueAnalysis,
    IssueConstraint,
    IssueContext,
    IssueRequirement,
    IssueSource,
    RequirementType,
)


def test_issue_model_defaults() -> None:
    """Verify Issue model creation and default values."""
    issue = Issue(
        title="Add authentication endpoint", description="Implement JWT login"
    )
    assert issue.id == "ISSUE-LOCAL"
    assert issue.title == "Add authentication endpoint"
    assert issue.source == IssueSource.LOCAL


def test_issue_analysis_model() -> None:
    """Verify IssueAnalysis model structure."""
    issue = Issue(title="Fix bug", description="Fix issue in token validation")
    req = IssueRequirement(id="REQ-1", description="Reject expired tokens")
    ac = AcceptanceCriterion(id="AC-1", description="Expired token returns 401")
    con = IssueConstraint(description="Do not change DB schema")

    analysis = IssueAnalysis(
        issue=issue,
        requirements=[req],
        acceptance_criteria=[ac],
        constraints=[con],
        keywords=["token", "validation"],
        references=[],
        endpoints=[],
        is_ambiguous=False,
    )

    assert analysis.issue.title == "Fix bug"
    assert len(analysis.requirements) == 1
    assert len(analysis.acceptance_criteria) == 1
    assert analysis.requirements[0].requirement_type == RequirementType.FUNCTIONAL
    assert analysis.acceptance_criteria[0].is_explicit is True


def test_issue_context_model() -> None:
    """Verify IssueContext model structure."""
    issue = Issue(title="Fix bug", description="Fix issue in token validation")
    analysis = IssueAnalysis(
        issue=issue,
        requirements=[],
        acceptance_criteria=[],
        constraints=[],
        keywords=["token"],
        references=[],
        endpoints=[],
    )
    context = IssueContext(
        analysis=analysis,
        relevant_files=["app/auth.py"],
        relevant_symbols=[],
        relevant_chunks=[],
        matches=[],
        unresolved_references=["NonExistentSymbol"],
    )

    assert context.relevant_files == ["app/auth.py"]
    assert context.unresolved_references == ["NonExistentSymbol"]
