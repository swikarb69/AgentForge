"""Unit tests for ReviewService coordination, file extraction, and reporting."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backend.app.generation.models import (
    GeneratedFileChange,
    GenerationResult,
    GenerationStatus,
    PatchValidation,
)
from backend.app.planning.models import ChangeType
from backend.app.review.models import (
    FindingCategory,
    FindingConfidence,
    FindingSeverity,
    ReviewRequest,
    ReviewScope,
    ReviewStatus,
)
from backend.app.review.service import (
    ReviewService,
    extract_files_from_unified_diff,
)


def test_review_service_initialization() -> None:
    """Verify ReviewService initializes cleanly with default analyzers."""
    service = ReviewService()
    assert service.static_analyzer is not None
    assert service.security_analyzer is not None
    assert service.architecture_analyzer is not None
    assert service.regression_analyzer is not None
    assert service.aggregator is not None
    assert service.list_reviews() == []


def test_review_service_scan_repository_files() -> None:
    """Verify scan_repository_files performs read-only Python discovery."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "pkg").mkdir()
        (root / "pkg" / "module.py").write_text("x = 1\n", encoding="utf-8")
        (root / "venv").mkdir()
        (root / "venv" / "ignored.py").write_text("y = 2\n", encoding="utf-8")
        (root / ".git").mkdir()
        (root / ".git" / "git_script.py").write_text("z = 3\n", encoding="utf-8")

        service = ReviewService()
        files = service.scan_repository_files(root)

        assert "pkg/module.py" in files
        assert files["pkg/module.py"] == "x = 1\n"
        assert "venv/ignored.py" not in files
        assert ".git/git_script.py" not in files


def test_review_service_scan_nonexistent_directory() -> None:
    """Verify scan_repository_files raises ValueError for invalid path."""
    service = ReviewService()
    with pytest.raises(ValueError, match="Repository directory does not exist"):
        service.scan_repository_files(Path("nonexistent_path_xyz_12345"))


@pytest.mark.anyio
async def test_review_service_create_review_direct_files() -> None:
    """Verify review execution on direct file dictionary with clean code."""
    service = ReviewService()
    clean_code = (
        '"""Clean utility module."""\n\n'
        "def compute_sum(a: int, b: int) -> int:\n"
        '    """Add two numbers."""\n'
        "    return a + b\n"
    )
    request = ReviewRequest(
        review_id="REV-UNIT-001",
        repository_path="backend/app",
        metadata={"files": {"app/utils.py": clean_code}},
    )

    report = await service.create_review(request)
    assert report.review_id == "REV-UNIT-001"
    assert report.status == ReviewStatus.APPROVED
    assert report.summary.quality_score == 100.0
    assert report.summary.total_findings == 0
    assert service.get_review("REV-UNIT-001") == report


@pytest.mark.anyio
async def test_review_service_create_review_with_security_defect() -> None:
    """Verify review execution flags critical security defects and blocks."""
    service = ReviewService()
    vuln_code = (
        "import sqlite3\n\n"
        "def get_user(uid: str):\n"
        '    conn = sqlite3.connect("test.db")\n'
        "    cursor = conn.cursor()\n"
        '    cursor.execute(f"SELECT * FROM users WHERE id = {uid}")\n'
        "    return cursor.fetchall()\n"
    )
    request = ReviewRequest(
        review_id="REV-UNIT-002",
        repository_path="backend/app",
        metadata={"files": {"app/db.py": vuln_code}},
    )

    report = await service.create_review(request)
    assert report.review_id == "REV-UNIT-002"
    assert report.status == ReviewStatus.BLOCKED
    assert report.summary.critical_count >= 1
    assert any(f.rule_id == "SEC-001" for f in report.findings)
    assert len(report.blockers) >= 1


@pytest.mark.anyio
async def test_review_service_create_review_with_unified_diff() -> None:
    """Verify review execution parsing patch_content unified diff."""
    service = ReviewService()
    diff = (
        "diff --git a/backend/calculator.py b/backend/calculator.py\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/backend/calculator.py\n"
        "@@ -0,0 +1,5 @@\n"
        "+def eval_expr(expr: str):\n"
        "+    return eval(expr)\n"
    )
    request = ReviewRequest(
        review_id="REV-UNIT-003",
        repository_path="backend/app",
        patch_content=diff,
    )

    report = await service.create_review(request)
    assert report.review_id == "REV-UNIT-003"
    assert any(f.rule_id == "SEC-003" for f in report.findings)
    assert report.status == ReviewStatus.BLOCKED


@pytest.mark.anyio
async def test_review_service_upstream_generation_resolution() -> None:
    """Verify ReviewService resolves file changes from GenerationService."""
    mock_gen_service = MagicMock()
    mock_gen_result = GenerationResult(
        generation_id="GEN-123",
        status=GenerationStatus.VALIDATED,
        validation=PatchValidation(valid=True, status=GenerationStatus.VALIDATED),
        provider_type="mock",
        changes=[
            GeneratedFileChange(
                change_type=ChangeType.CREATE,
                path="service/calc.py",
                content="def compute():\n    return 42\n",
            )
        ],
    )
    mock_gen_service.get_generation.return_value = mock_gen_result

    service = ReviewService(generation_service=mock_gen_service)
    request = ReviewRequest(
        review_id="REV-UNIT-004",
        repository_path="backend/app",
        generation_id="GEN-123",
    )

    report = await service.create_review(request)
    assert report.review_id == "REV-UNIT-004"
    assert report.reviewed_artifacts["generation_id"] == "GEN-123"
    mock_gen_service.get_generation.assert_called_once_with("GEN-123")


@pytest.mark.anyio
async def test_review_service_empty_review_target() -> None:
    """Verify ReviewService returns clean report when no files are found."""
    with tempfile.TemporaryDirectory() as empty_dir:
        service = ReviewService()
        request = ReviewRequest(
            review_id="REV-UNIT-005",
            repository_path=empty_dir,
            review_scope=ReviewScope.PATCH_ONLY,
        )

        report = await service.create_review(request)
        assert report.review_id == "REV-UNIT-005"
        assert report.status == ReviewStatus.APPROVED
        assert report.summary.total_findings == 0
        assert "No files identified" in report.recommendations[0]


def test_review_service_format_markdown_report() -> None:
    """Verify format_markdown_report generates expected sections."""
    service = ReviewService()
    clean_code = (
        '"""Doc."""\ndef foo(uid):\n    import os\n    os.system("echo " + uid)\n'
    )
    extracted = extract_files_from_unified_diff(
        "diff --git a/a.py b/a.py\n--- /dev/null\n+++ b/a.py\n@@ -0,0 +1,4 @@\n"
        + "\n".join("+" + ln for ln in clean_code.splitlines())
    )
    request = ReviewRequest(
        review_id="REV-UNIT-006",
        repository_path=".",
    )
    report = service.aggregator.aggregate(
        findings=[],
        files_reviewed=list(extracted.keys()),
        config=request.config,
        review_id=request.review_id,
        analyzers_run=["DeterministicStaticAnalyzer"],
    )
    report.blockers = ["Critical security vulnerability detected"]
    report.recommendations = ["Sanitize input variables"]

    md = service.format_markdown_report(report)
    assert "# AgentForge Code Review Report" in md
    assert "**Review ID:** `REV-UNIT-006`" in md
    assert "## Blockers" in md
    assert "⛔ Critical security vulnerability detected" in md
    assert "## Recommendations" in md
    assert "💡 Sanitize input variables" in md


def test_extract_files_from_unified_diff_with_existing_file() -> None:
    """Verify unified diff applies in memory to existing file content."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        target = root / "module.py"
        target.write_text("def hello():\n    return 'old'\n", encoding="utf-8")

        diff = (
            "diff --git a/module.py b/module.py\n"
            "--- a/module.py\n"
            "+++ b/module.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def hello():\n"
            "-    return 'old'\n"
            "+    return 'new'\n"
        )
        extracted = extract_files_from_unified_diff(diff, repo_path=root)
        assert "module.py" in extracted
        assert "return 'new'" in extracted["module.py"]


@pytest.mark.anyio
async def test_review_service_upstream_repair_resolution() -> None:
    """Verify ReviewService extracts files from DebuggingService repair result."""
    mock_debug_service = MagicMock()
    mock_repair = MagicMock()
    mock_repair.winning_patch = (
        "diff --git a/fixed.py b/fixed.py\n"
        "--- /dev/null\n"
        "+++ b/fixed.py\n"
        "@@ -0,0 +1,2 @@\n"
        "+def fixed():\n"
        "+    return True\n"
    )
    mock_debug_service.get_repair.return_value = mock_repair

    service = ReviewService(debugging_service=mock_debug_service)
    request = ReviewRequest(
        review_id="REV-UNIT-007",
        repository_path="backend/app",
        repair_id="REP-001",
    )
    report = await service.create_review(request)
    assert report.review_id == "REV-UNIT-007"
    assert report.reviewed_artifacts["repair_id"] == "REP-001"
    mock_debug_service.get_repair.assert_called_once_with("REP-001")


def test_format_markdown_report_with_findings() -> None:
    """Verify format_markdown_report renders findings with evidence and remediation."""
    from backend.app.review.models import CodeEvidence, ReviewFinding

    service = ReviewService()
    finding = ReviewFinding(
        finding_id="find-md-001",
        rule_id="SEC-001",
        title="SQL Injection",
        description="Raw SQL query formatted using f-string.",
        category=FindingCategory.SECURITY,
        severity=FindingSeverity.CRITICAL,
        confidence=FindingConfidence.HIGH,
        file_path="app/db.py",
        start_line=10,
        end_line=12,
        evidence=CodeEvidence(
            evidence_id="ev-001",
            file_path="app/db.py",
            start_line=10,
            end_line=12,
            code_excerpt='cursor.execute(f"SELECT * FROM users WHERE id = {uid}")',
        ),
        remediation="Use query parameters instead of string formatting.",
    )
    request = ReviewRequest(review_id="REV-UNIT-008", repository_path=".")
    report = service.aggregator.aggregate(
        findings=[finding],
        files_reviewed=["app/db.py"],
        config=request.config,
        review_id="REV-UNIT-008",
    )
    md = service.format_markdown_report(report)
    assert "### [CRITICAL] SEC-001: SQL Injection" in md
    assert "`app/db.py:10`" in md
    assert "cursor.execute" in md
    assert "Use query parameters" in md
