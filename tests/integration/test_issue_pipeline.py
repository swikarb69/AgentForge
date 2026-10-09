"""Integration test for complete issue intelligence pipeline and API routes."""

from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import app

client = TestClient(app)
FIXTURE_PATH = Path("agentforge-fixtures/sample-project").resolve()


def test_api_analyze_issue_endpoint() -> None:
    """Test POST /api/v1/issues/analyze endpoint."""
    response = client.post(
        "/api/v1/issues/analyze",
        json={
            "title": "Fix verify_token authentication in app/auth.py",
            "description": (
                "### Requirements\n"
                "- Refactor `verify_token` method in AuthService class.\n"
                "- Support POST /api/v1/auth endpoint.\n"
                "### Acceptance Criteria\n"
                "- Token validation must succeed for valid JWT format."
            ),
            "id": "ISSUE-TEST-1",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["issue"]["id"] == "ISSUE-TEST-1"
    assert data["issue"]["title"] == "Fix verify_token authentication in app/auth.py"
    assert len(data["requirements"]) >= 1
    assert len(data["acceptance_criteria"]) >= 1
    assert len(data["references"]) >= 1
    assert len(data["endpoints"]) >= 1
    assert not data["is_ambiguous"]


def test_api_analyze_issue_ambiguous() -> None:
    """Test POST /api/v1/issues/analyze with ambiguous input."""
    response = client.post(
        "/api/v1/issues/analyze",
        json={
            "title": "Broken app",
            "description": "it fails",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["is_ambiguous"]
    assert len(data["ambiguity_reasons"]) > 0


def test_api_analyze_issue_validation_error() -> None:
    """Test POST /api/v1/issues/analyze with empty title."""
    response = client.post(
        "/api/v1/issues/analyze",
        json={"title": "   ", "description": "some text"},
    )
    assert response.status_code == 400
    assert "title cannot be empty" in response.json()["detail"]


def test_api_issue_context_endpoint() -> None:
    """Test POST /api/v1/issues/context endpoint mapping issue to codebase."""
    response = client.post(
        "/api/v1/issues/context",
        json={
            "repository_path": str(FIXTURE_PATH),
            "title": "Fix AuthService.verify_token in app/auth.py",
            "description": (
                "Refactor `verify_token()` function in AuthService class.\n"
                "Check `app/auth.py` implementation."
            ),
            "id": "ISSUE-TEST-2",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["analysis"]["issue"]["id"] == "ISSUE-TEST-2"
    assert "app/auth.py" in data["relevant_files"]
    assert len(data["matches"]) > 0

    top_match = data["matches"][0]
    assert top_match["file_path"] == "app/auth.py"
    assert top_match["score"] > 0.0
    assert len(top_match["reasons"]) > 0


def test_api_issue_context_invalid_repo() -> None:
    """Test POST /api/v1/issues/context with invalid repository path."""
    response = client.post(
        "/api/v1/issues/context",
        json={
            "repository_path": "/invalid/nonexistent/repo/path",
            "title": "Fix auth",
            "description": "desc",
        },
    )
    assert response.status_code == 400
    assert "does not exist" in response.json()["detail"]
