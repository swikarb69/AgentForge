"""Unit tests for RequirementExtractor."""

from backend.app.issue.extractor import RequirementExtractor


def test_extract_requirements_from_section() -> None:
    """Verify requirement extraction from explicit section."""
    title = "Add refresh token support"
    description = """
## Requirements
- Add refresh token generation.
- Store refresh tokens securely.
- Reject expired refresh tokens.
"""
    extractor = RequirementExtractor()
    reqs = extractor.extract_requirements(title, description)

    assert len(reqs) >= 3
    req_texts = [r.description for r in reqs]
    assert "Add refresh token generation." in req_texts
    assert "Store refresh tokens securely." in req_texts


def test_extract_acceptance_criteria_explicit() -> None:
    """Verify acceptance criteria extraction when section is present."""
    description = """
## Acceptance Criteria
- POST /auth/refresh returns 200 for valid tokens.
- Expired tokens return 401.
"""
    extractor = RequirementExtractor()
    criteria = extractor.extract_acceptance_criteria(description)

    assert len(criteria) == 2
    assert criteria[0].is_explicit is True
    assert "POST /auth/refresh" in criteria[0].description


def test_extract_acceptance_criteria_absent() -> None:
    """Verify acceptance criteria returns empty list when no section is present."""
    description = "Fix bug in authentication token validation."
    extractor = RequirementExtractor()
    criteria = extractor.extract_acceptance_criteria(description)

    assert len(criteria) == 0
