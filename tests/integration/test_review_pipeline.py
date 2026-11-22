"""Integration tests for code review pipeline, API, and immutability."""

import hashlib
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.review.models import (
    FindingCategory,
    FindingSeverity,
    ReviewStatus,
)

client = TestClient(app)


def _compute_directory_hashes(root: Path) -> dict[str, str]:
    """Calculate SHA-256 hashes of all files in directory to ensure immutability."""
    tree_hashes: dict[str, str] = {}
    for p in root.rglob("*"):
        if p.is_file():
            rel = str(p.relative_to(root)).replace("\\", "/")
            tree_hashes[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return tree_hashes


def test_clean_code_review_approved() -> None:
    """Verify POST /api/v1/reviews/create on clean code results in APPROVED status."""
    clean_code = (
        '"""Mathematical utilities."""\n\n'
        "def add(a: int, b: int) -> int:\n"
        '    """Return sum of two integers."""\n'
        "    return a + b\n"
    )
    payload = {
        "review_id": "REV-INT-CLEAN-001",
        "repository_path": "backend/app",
        "metadata": {"files": {"pkg/math_utils.py": clean_code}},
    }

    response = client.post("/api/v1/reviews/create", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["review_id"] == "REV-INT-CLEAN-001"
    assert data["status"] == ReviewStatus.APPROVED.value
    assert data["summary"]["quality_score"] == 100.0
    assert data["summary"]["total_findings"] == 0
    assert len(data["blockers"]) == 0


def test_security_vulnerability_blocks_approval() -> None:
    """Verify POST /api/v1/reviews/create blocks approval on critical issues."""
    vuln_code = (
        "import sqlite3\n\n"
        "def fetch_user(uid: str):\n"
        '    conn = sqlite3.connect("users.db")\n'
        "    cursor = conn.cursor()\n"
        '    cursor.execute(f"SELECT * FROM users WHERE id = {uid}")\n'
        "    return cursor.fetchall()\n"
    )
    payload = {
        "review_id": "REV-INT-SEC-002",
        "repository_path": "backend/app",
        "metadata": {"files": {"pkg/db.py": vuln_code}},
    }

    response = client.post("/api/v1/reviews/create", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["review_id"] == "REV-INT-SEC-002"
    assert data["status"] == ReviewStatus.BLOCKED.value
    assert data["summary"]["critical_count"] >= 1
    assert any(f["rule_id"] == "SEC-001" for f in data["findings"])
    assert len(data["blockers"]) >= 1


def test_review_retrieval_and_listing() -> None:
    """Verify GET /api/v1/reviews/{id} and GET /api/v1/reviews endpoints."""
    # 1. Retrieve existing review
    response = client.get("/api/v1/reviews/REV-INT-CLEAN-001")
    assert response.status_code == 200
    assert response.json()["review_id"] == "REV-INT-CLEAN-001"

    # 2. Retrieve nonexistent review returns 404
    missing_resp = client.get("/api/v1/reviews/REV-NONEXISTENT-999")
    assert missing_resp.status_code == 404
    assert "not found" in missing_resp.json()["detail"].lower()

    # 3. List all reviews
    list_resp = client.get("/api/v1/reviews")
    assert list_resp.status_code == 200
    reviews = list_resp.json()
    assert isinstance(reviews, list)
    assert any(r["review_id"] == "REV-INT-CLEAN-001" for r in reviews)


def test_review_findings_filtering() -> None:
    """Verify GET /api/v1/reviews/{id}/findings with severity and category filters."""
    # Create review with multiple findings
    mixed_code = (
        "import os\n"
        "import sqlite3\n\n"
        "def run_all(cmd, uid):\n"
        '    os.system("echo " + cmd)\n'
        '    conn = sqlite3.connect("db")\n'
        '    conn.cursor().execute(f"SELECT * FROM data WHERE x = {uid}")\n'
    )
    payload = {
        "review_id": "REV-INT-FILTER-003",
        "repository_path": "backend/app",
        "metadata": {"files": {"mixed.py": mixed_code}},
    }
    client.post("/api/v1/reviews/create", json=payload)

    # Filter by severity=critical
    crit_resp = client.get(
        "/api/v1/reviews/REV-INT-FILTER-003/findings?severity=critical"
    )
    assert crit_resp.status_code == 200
    crit_findings = crit_resp.json()
    assert len(crit_findings) >= 1
    assert all(f["severity"] == FindingSeverity.CRITICAL.value for f in crit_findings)

    # Filter by category=security
    sec_resp = client.get(
        "/api/v1/reviews/REV-INT-FILTER-003/findings?category=security"
    )
    assert sec_resp.status_code == 200
    sec_findings = sec_resp.json()
    assert len(sec_findings) >= 1
    assert all(f["category"] == FindingCategory.SECURITY.value for f in sec_findings)


def test_markdown_report_export() -> None:
    """Verify GET /api/v1/reviews/{id}/report?format=markdown."""
    response = client.get("/api/v1/reviews/REV-INT-CLEAN-001/report?format=markdown")
    assert response.status_code == 200
    data = response.json()
    assert data["format"] == "markdown"
    assert "# AgentForge Code Review Report" in data["content"]
    assert "REV-INT-CLEAN-001" in data["content"]
    assert "APPROVED" in data["content"]

    # Test format=json fallback
    json_resp = client.get("/api/v1/reviews/REV-INT-CLEAN-001/report?format=json")
    assert json_resp.status_code == 200
    assert json_resp.json()["review_id"] == "REV-INT-CLEAN-001"


def test_rules_catalog_endpoint() -> None:
    """Verify GET /api/v1/reviews/rules lists rules with metadata and filters."""
    # 1. All rules
    response = client.get("/api/v1/reviews/rules")
    assert response.status_code == 200
    rules = response.json()
    assert len(rules) >= 24
    rule_ids = {r["rule_id"] for r in rules}
    assert "SEC-001" in rule_ids
    assert "CORR-001" in rule_ids
    assert "ARCH-001" in rule_ids
    assert "REG-001" in rule_ids

    # 2. Filter by category=security
    sec_resp = client.get("/api/v1/reviews/rules?category=security")
    assert sec_resp.status_code == 200
    sec_rules = sec_resp.json()
    assert len(sec_rules) == 7
    assert all(r["category"] == "security" for r in sec_rules)

    # 3. Filter by severity=critical
    crit_resp = client.get("/api/v1/reviews/rules?severity=critical")
    assert crit_resp.status_code == 200
    crit_rules = crit_resp.json()
    assert len(crit_rules) >= 1
    assert all(r["severity"] == "critical" for r in crit_rules)


def test_repository_read_only_immutability() -> None:
    """Verify review execution never modifies or creates files in target repository."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_root = Path(tmpdir)
        (repo_root / "src").mkdir()
        src_file = repo_root / "src" / "code.py"
        src_file.write_text("x = 1\n", encoding="utf-8")

        hashes_before = _compute_directory_hashes(repo_root)

        payload = {
            "review_id": "REV-INT-IMMUTABLE-004",
            "repository_path": str(repo_root),
            "review_scope": "full_repository",
        }
        resp = client.post("/api/v1/reviews/create", json=payload)
        assert resp.status_code == 200

        hashes_after = _compute_directory_hashes(repo_root)

        # Repository must be 100% byte-for-byte unchanged
        assert hashes_before == hashes_after
