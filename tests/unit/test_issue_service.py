"""Unit tests for IssueContextBuilder service pipeline."""

import pytest

from backend.app.issue.models import IssueSource
from backend.app.issue.service import IssueContextBuilder


def test_analyze_issue_success() -> None:
    """Verify issue analysis with explicit title, description, and requirements."""
    builder = IssueContextBuilder()
    analysis = builder.analyze_issue(
        title="Add JWT authentication endpoint",
        description=(
            "### Requirements\n"
            "- Implement POST /api/v1/auth/login endpoint in backend/app/auth.py.\n"
            "- Validate JWT token with verify_token() function.\n"
            "### Acceptance Criteria\n"
            "- Return 401 if invalid credentials provided.\n"
            "Must not break existing endpoints."
        ),
        issue_id="ISSUE-101",
        source=IssueSource.GITHUB,
    )

    assert analysis.issue.id == "ISSUE-101"
    assert analysis.issue.title == "Add JWT authentication endpoint"
    assert len(analysis.requirements) >= 1
    assert len(analysis.acceptance_criteria) >= 1
    assert len(analysis.references) >= 1
    assert len(analysis.endpoints) >= 1
    assert not analysis.is_ambiguous


def test_analyze_issue_ambiguous() -> None:
    """Verify detection of ambiguous issue text."""
    builder = IssueContextBuilder()
    analysis = builder.analyze_issue(
        title="Fix bug",
        description="it is broken",
    )

    assert analysis.is_ambiguous
    assert len(analysis.ambiguity_reasons) > 0


def test_analyze_issue_empty_title() -> None:
    """Verify error raised on empty issue title."""
    builder = IssueContextBuilder()
    with pytest.raises(ValueError, match="title cannot be empty"):
        builder.analyze_issue(title="", description="Some desc")


def test_build_context(tmp_path) -> None:
    """Verify build_context produces complete IssueContext over sample repo."""
    repo_dir = tmp_path / "sample_repo"
    repo_dir.mkdir()
    auth_file = repo_dir / "auth.py"
    auth_file.write_text(
        "def verify_token(token: str):\n"
        '    """Verify input JWT token string."""\n'
        "    return token == 'valid'\n"
    )

    builder = IssueContextBuilder()
    context = builder.build_context(
        repository_path=str(repo_dir),
        title="Fix verify_token in auth.py",
        description="Verify token validation logic.",
    )

    assert context.analysis.issue.title == "Fix verify_token in auth.py"
    assert "auth.py" in context.relevant_files
    assert any(s.name == "verify_token" for s in context.relevant_symbols)
    assert len(context.matches) > 0
