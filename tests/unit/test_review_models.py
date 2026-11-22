"""Unit tests for Review data models, enums, evidence, and validation."""

import pytest
from pydantic import ValidationError

from backend.app.review.models import (
    CodeEvidence,
    FindingCategory,
    FindingConfidence,
    FindingSeverity,
    ReviewConfiguration,
    ReviewFinding,
    ReviewReport,
    ReviewRequest,
    ReviewScope,
    ReviewStatus,
    ReviewSummary,
    redact_secrets,
)


def test_finding_category_enum() -> None:
    """Verify all expected categories are present."""
    assert FindingCategory.CORRECTNESS.value == "correctness"
    assert FindingCategory.SECURITY.value == "security"
    assert FindingCategory.RELIABILITY.value == "reliability"
    assert FindingCategory.MAINTAINABILITY.value == "maintainability"
    assert FindingCategory.PERFORMANCE.value == "performance"
    assert FindingCategory.ARCHITECTURE.value == "architecture"
    assert FindingCategory.REGRESSION.value == "regression"
    assert FindingCategory.TESTING.value == "testing"
    assert FindingCategory.CONFIGURATION.value == "configuration"


def test_finding_severity_enum() -> None:
    """Verify severity levels."""
    assert FindingSeverity.CRITICAL.value == "critical"
    assert FindingSeverity.HIGH.value == "high"
    assert FindingSeverity.MEDIUM.value == "medium"
    assert FindingSeverity.LOW.value == "low"
    assert FindingSeverity.INFORMATIONAL.value == "informational"


def test_finding_confidence_enum() -> None:
    """Verify confidence levels."""
    assert FindingConfidence.HIGH.value == "high"
    assert FindingConfidence.MEDIUM.value == "medium"
    assert FindingConfidence.LOW.value == "low"


def test_review_status_enum() -> None:
    """Verify review lifecycle statuses."""
    assert ReviewStatus.PENDING.value == "pending"
    assert ReviewStatus.ANALYZING.value == "analyzing"
    assert ReviewStatus.COMPLETED.value == "completed"
    assert ReviewStatus.COMPLETED_WITH_WARNINGS.value == "completed_with_warnings"
    assert ReviewStatus.FAILED.value == "failed"
    assert ReviewStatus.BLOCKED.value == "blocked"


def test_review_scope_enum() -> None:
    """Verify review scope enum."""
    assert ReviewScope.PATCH_ONLY.value == "patch_only"
    assert ReviewScope.AFFECTED_FILES.value == "affected_files"
    assert ReviewScope.FULL_REPOSITORY.value == "full_repository"


def test_review_configuration_defaults() -> None:
    """Verify ReviewConfiguration default values and constraints."""
    config = ReviewConfiguration()
    assert len(config.enabled_categories) == 9
    assert config.min_severity == FindingSeverity.INFORMATIONAL
    assert config.max_findings_per_file == 50
    assert config.max_excerpt_lines == 10
    assert config.max_file_size_bytes == 500_000
    assert config.block_on_critical is True
    assert config.require_human_on_high is True
    assert config.allow_llm_explanations is False


def test_review_request_valid() -> None:
    """Verify ReviewRequest model initialization."""
    req = ReviewRequest(
        review_id="rev-123",
        repository_path="/repo/path",
        generation_id="gen-456",
        execution_id="exec-789",
        repair_id="rep-101",
    )
    assert req.review_id == "rev-123"
    assert req.repository_path == "/repo/path"
    assert req.generation_id == "gen-456"
    assert req.execution_id == "exec-789"
    assert req.repair_id == "rep-101"
    assert req.review_scope == ReviewScope.PATCH_ONLY


def test_review_request_empty_path_rejected() -> None:
    """Verify empty repository_path is rejected."""
    with pytest.raises(ValidationError):
        ReviewRequest(review_id="rev-123", repository_path="   ")


def test_code_evidence_valid() -> None:
    """Verify CodeEvidence initialization and line ranges."""
    evidence = CodeEvidence(
        evidence_id="ev-1",
        file_path="backend/app/auth.py",
        start_line=10,
        end_line=15,
        code_excerpt="token = request.headers.get('Authorization')",
    )
    assert evidence.start_line == 10
    assert evidence.end_line == 15
    assert evidence.file_path == "backend/app/auth.py"


def test_code_evidence_invalid_line_range() -> None:
    """Verify start_line > end_line raises ValidationError."""
    with pytest.raises(ValidationError, match="start_line .* cannot be greater than"):
        CodeEvidence(
            evidence_id="ev-1",
            file_path="app.py",
            start_line=20,
            end_line=10,
            code_excerpt="some code",
        )


def test_code_evidence_path_traversal_rejected() -> None:
    """Verify path traversal in evidence file_path is rejected."""
    with pytest.raises(ValidationError, match="Path traversal"):
        CodeEvidence(
            evidence_id="ev-1",
            file_path="../etc/passwd",
            start_line=1,
            end_line=2,
            code_excerpt="root:x:0:0",
        )


def test_code_evidence_absolute_path_rejected() -> None:
    """Verify absolute paths in evidence file_path are rejected."""
    with pytest.raises(ValidationError, match="Path must be repository-relative"):
        CodeEvidence(
            evidence_id="ev-1",
            file_path="/etc/passwd",
            start_line=1,
            end_line=2,
            code_excerpt="root:x:0:0",
        )


def test_code_evidence_secret_redaction() -> None:
    """Verify secrets and tokens are redacted automatically in excerpts."""
    raw = (
        'api_key = "dummy_mock_secret_key_1234567890"\n'
        'auth_token = "mock_auth_token_value_987654321"'
    )
    evidence = CodeEvidence(
        evidence_id="ev-sec",
        file_path="config.py",
        start_line=1,
        end_line=2,
        code_excerpt=raw,
    )
    assert "dummy_mock_secret_key" not in evidence.code_excerpt
    assert "mock_auth_token" not in evidence.code_excerpt
    assert "[REDACTED]" in evidence.code_excerpt


def test_redact_secrets_helper() -> None:
    """Verify redact_secrets function directly."""
    secret_line = 'password="supersecretpassword123"'  # noqa: S105
    redacted = redact_secrets(secret_line)
    assert "supersecretpassword123" not in redacted
    assert "[REDACTED]" in redacted


def test_code_evidence_excerpt_truncation() -> None:
    """Verify oversized excerpts are truncated cleanly."""
    huge_code = "x = 1\n" * 500  # 3000 chars
    evidence = CodeEvidence(
        evidence_id="ev-huge",
        file_path="large.py",
        start_line=1,
        end_line=500,
        code_excerpt=huge_code,
    )
    assert len(evidence.code_excerpt) <= 2000
    assert evidence.code_excerpt.endswith("...")


def test_review_finding_valid() -> None:
    """Verify ReviewFinding model instantiation."""
    evidence = CodeEvidence(
        evidence_id="ev-10",
        file_path="backend/app/auth.py",
        start_line=25,
        end_line=26,
        code_excerpt="cursor.execute(f'SELECT * FROM users WHERE id = {user_id}')",
    )
    finding = ReviewFinding(
        finding_id="find-001",
        rule_id="SEC-001",
        title="SQL Injection Detected",
        description="F-string formatted query passed directly to execute.",
        category=FindingCategory.SECURITY,
        severity=FindingSeverity.CRITICAL,
        confidence=FindingConfidence.HIGH,
        file_path="backend/app/auth.py",
        start_line=25,
        end_line=26,
        evidence=evidence,
        impact="Database compromise through arbitrary SQL injection",
        remediation="Use parameterized query placeholders.",
        requires_human_review=True,
    )
    assert finding.finding_id == "find-001"
    assert finding.severity == FindingSeverity.CRITICAL
    assert finding.requires_human_review is True


def test_review_summary_defaults() -> None:
    """Verify ReviewSummary defaults and gating flags."""
    summary = ReviewSummary(
        total_findings=2,
        critical_findings=1,
        approval_recommended=False,
        blocking_reasons=["Critical SQL injection finding present."],
    )
    assert summary.total_findings == 2
    assert summary.critical_findings == 1
    assert summary.approval_recommended is False
    assert len(summary.blocking_reasons) == 1


def test_review_report_valid() -> None:
    """Verify ReviewReport composition."""
    report = ReviewReport(
        review_id="rev-999",
        repository_path="/workspace/repo",
        reviewed_artifacts={"generation_id": "gen-1", "execution_id": "exec-1"},
        status=ReviewStatus.COMPLETED,
        summary=ReviewSummary(total_findings=0, approval_recommended=True),
        findings=[],
        limitations=["Analysis limited to Python files."],
    )
    assert report.review_id == "rev-999"
    assert report.status == ReviewStatus.COMPLETED
    assert report.summary.approval_recommended is True
    assert len(report.limitations) == 1
