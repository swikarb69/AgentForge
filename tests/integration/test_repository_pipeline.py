"""Integration test for complete repository intelligence pipeline and API."""

from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.repository.analyzer import RepositoryAnalyzer

client = TestClient(app)
FIXTURE_PATH = Path("agentforge-fixtures/sample-project").resolve()


def test_repository_analyzer_pipeline() -> None:
    """Verify end-to-end repository analysis on sample fixture project."""
    analyzer = RepositoryAnalyzer()
    info = analyzer.analyze(FIXTURE_PATH)

    assert info.repository_name == "sample-project"
    assert info.total_files >= 6
    assert info.total_source_files >= 6
    assert info.total_test_files >= 1

    file_paths = [f.relative_path for f in info.files]
    assert "app/main.py" in file_paths
    assert "app/auth.py" in file_paths
    assert "app/database.py" in file_paths
    assert "tests/test_auth.py" in file_paths
    assert "README.md" in file_paths
    assert "pyproject.toml" in file_paths

    # Ignored directory must be excluded
    assert not any("ignored" in p for p in file_paths)

    # Verify extracted symbols
    symbol_names = [s.name for s in info.symbols]
    assert "AuthService" in symbol_names
    assert "verify_token" in symbol_names
    assert "login" in symbol_names
    assert "get_db_connection" in symbol_names

    # Verify chunks generated
    assert len(info.chunks) > 0

    # Verify search
    search_results = analyzer.search("verify_token")
    assert len(search_results) > 0
    assert search_results[0].chunk.symbol_name == "verify_token"


def test_repositories_api_analyze() -> None:
    """Verify POST /api/v1/repositories/analyze endpoint."""
    payload = {"path": str(FIXTURE_PATH)}
    response = client.post("/api/v1/repositories/analyze", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["repository_name"] == "sample-project"
    assert data["total_files"] >= 6
    assert data["total_test_files"] >= 1
    assert data["symbols_count"] > 0
    assert data["chunks_count"] > 0


def test_repositories_api_search() -> None:
    """Verify POST /api/v1/repositories/search endpoint."""
    payload = {
        "repository_path": str(FIXTURE_PATH),
        "query": "authentication",
        "limit": 5,
    }
    response = client.post("/api/v1/repositories/search", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["query"] == "authentication"
    assert data["total_matches"] >= 1
    assert len(data["results"]) > 0


def test_repositories_api_invalid_path() -> None:
    """Verify API returns 404 for non-existent repository path."""
    payload = {"path": "/invalid/nonexistent/path/agentforge"}
    response = client.post("/api/v1/repositories/analyze", json=payload)

    assert response.status_code == 404
    data = response.json()
    assert "does not exist" in data["detail"]
