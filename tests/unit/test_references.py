"""Unit tests for ReferenceExtractor."""

from backend.app.issue.references import ReferenceExtractor


def test_extract_file_references() -> None:
    """Verify extraction of file path references."""
    text = "Please modify `backend/app/auth.py` and update tests in tests/test_auth.py."
    extractor = ReferenceExtractor()
    refs = extractor.extract_file_references(text)

    ref_values = [r.value for r in refs]
    assert "backend/app/auth.py" in ref_values
    assert "tests/test_auth.py" in ref_values


def test_extract_symbol_references() -> None:
    """Verify extraction of function, method, and class symbols."""
    text = (
        "Fix `verify_token()` method in AuthService class and check "
        "RepositoryIndex.search."
    )
    extractor = ReferenceExtractor()
    refs = extractor.extract_symbol_references(text)

    ref_values = [r.value for r in refs]
    assert "verify_token()" in ref_values or "verify_token" in ref_values
    assert "AuthService" in ref_values
    assert "RepositoryIndex.search" in ref_values


def test_extract_endpoint_references() -> None:
    """Verify extraction of HTTP endpoint references."""
    text = "Add endpoint POST /api/v1/auth/refresh and GET /api/v1/health."
    extractor = ReferenceExtractor()
    endpoints = extractor.extract_endpoint_references(text)

    assert len(endpoints) == 2
    ep_map = {e.method: e.path for e in endpoints}
    assert ep_map["POST"] == "/api/v1/auth/refresh"
    assert ep_map["GET"] == "/api/v1/health"


def test_extract_constraints() -> None:
    """Verify constraint extraction."""
    text = """
Requirements:
- Add refresh token endpoint.

Constraints:
- Must not break backward compatibility
- Do not modify database schema
"""
    extractor = ReferenceExtractor()
    constraints = extractor.extract_constraints(text)

    assert len(constraints) >= 2
    c_texts = [c.description for c in constraints]
    assert any("Must not break" in c for c in c_texts)
    assert any("Do not modify" in c for c in c_texts)


def test_extract_keywords() -> None:
    """Verify technical keyword extraction."""
    text = "Implement `jwt` authentication using FastAPI and Redis."
    extractor = ReferenceExtractor()
    keywords = extractor.extract_keywords(text)

    assert "jwt" in keywords
    assert "fastapi" in keywords
    assert "redis" in keywords
    assert "authentication" in keywords
