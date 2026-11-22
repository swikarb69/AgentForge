"""Unit tests for EvidenceCollector and ReviewAggregator."""

from backend.app.review.aggregator import ReviewAggregator
from backend.app.review.evidence import EvidenceCollector
from backend.app.review.models import (
    CodeEvidence,
    FindingCategory,
    FindingConfidence,
    FindingSeverity,
    ReviewConfiguration,
    ReviewFinding,
    ReviewScope,
    ReviewStatus,
)


def _make_finding(
    rule_id: str,
    file_path: str,
    line: int,
    severity: FindingSeverity,
    category: FindingCategory = FindingCategory.CORRECTNESS,
    remediation: str = "Fix issue",
    requires_human: bool = False,
) -> ReviewFinding:
    """Helper creating a test ReviewFinding."""
    evidence = CodeEvidence(
        evidence_id="ev-12345678",
        file_path=file_path,
        start_line=line,
        end_line=line,
        code_excerpt="sample code",
        related_rule=rule_id,
        content_hash="mockhash",
    )
    return ReviewFinding(
        finding_id=f"find-{rule_id}-{line}",
        rule_id=rule_id,
        title=f"Finding {rule_id}",
        description=f"Issue {rule_id} at line {line}",
        category=category,
        severity=severity,
        confidence=FindingConfidence.HIGH,
        file_path=file_path,
        start_line=line,
        end_line=line,
        evidence=evidence,
        remediation=remediation,
        source="unit_test",
        requires_human_review=requires_human,
    )


# ---------------------------------------------------------------------------
# Evidence Collector Tests
# ---------------------------------------------------------------------------


def test_evidence_collector_excerpt_bounded() -> None:
    lines = [f"line {i}" for i in range(1, 30)]
    excerpt = EvidenceCollector.extract_excerpt(lines, 1, 25, max_lines=10)
    assert len(excerpt.splitlines()) == 11
    assert "truncated excerpt" in excerpt


def test_evidence_collector_redaction_and_hash() -> None:
    lines = [
        "def connect():",
        '    token = "dummy_mock_secret_key_1234567890"',
        "    return token",
    ]
    evidence = EvidenceCollector.build_evidence(
        file_path="auth.py",
        start_line=1,
        end_line=3,
        source_lines=lines,
        related_rule="SEC-004",
    )
    assert evidence.file_path == "auth.py"
    assert evidence.start_line == 1
    assert evidence.end_line == 3
    assert "REDACTED" in evidence.code_excerpt
    assert evidence.content_hash is not None
    assert len(evidence.content_hash) == 64


def test_evidence_collector_synthetic() -> None:
    evidence = EvidenceCollector.build_synthetic_evidence(
        file_path="plan.md",
        description="Missing requirement AC-1",
        related_rule="REG-002",
        source_artifact="PLAN-1",
    )
    assert evidence.file_path == "plan.md"
    assert evidence.start_line == 1
    assert evidence.related_rule == "REG-002"
    assert evidence.source_artifact == "PLAN-1"


# ---------------------------------------------------------------------------
# Review Aggregator Tests
# ---------------------------------------------------------------------------


def test_deduplicate_findings() -> None:
    f1 = _make_finding("CORR-001", "app.py", 10, FindingSeverity.LOW)
    f2 = _make_finding("CORR-001", "app.py", 10, FindingSeverity.HIGH)
    f3 = _make_finding("CORR-001", "app.py", 20, FindingSeverity.LOW)

    deduped = ReviewAggregator.deduplicate_findings([f1, f2, f3])
    assert len(deduped) == 2
    # f2 has higher severity, should replace f1 for line 10
    line_10_finding = next(f for f in deduped if f.start_line == 10)
    assert line_10_finding.severity == FindingSeverity.HIGH


def test_sort_findings_order() -> None:
    f_low = _make_finding("CORR-001", "app.py", 1, FindingSeverity.LOW)
    f_crit = _make_finding("SEC-001", "db.py", 5, FindingSeverity.CRITICAL)
    f_high = _make_finding("ARCH-001", "core.py", 2, FindingSeverity.HIGH)
    f_med = _make_finding("MAINT-001", "util.py", 3, FindingSeverity.MEDIUM)

    sorted_list = ReviewAggregator.sort_findings([f_low, f_crit, f_high, f_med])
    assert [f.severity for f in sorted_list] == [
        FindingSeverity.CRITICAL,
        FindingSeverity.HIGH,
        FindingSeverity.MEDIUM,
        FindingSeverity.LOW,
    ]


def test_quality_score_weighting() -> None:
    # 0 findings -> 100.0
    assert ReviewAggregator.calculate_quality_score(0, 0, 0, 0) == 100.0

    # 1 critical (-25) -> 75.0
    assert ReviewAggregator.calculate_quality_score(1, 0, 0, 0) == 75.0

    # 1 high (-10) -> 90.0
    assert ReviewAggregator.calculate_quality_score(0, 1, 0, 0) == 90.0

    # 1 medium (-3), 2 low (-2) -> 95.0
    assert ReviewAggregator.calculate_quality_score(0, 0, 1, 2) == 95.0

    # Excessive penalties clamped at 0.0
    assert ReviewAggregator.calculate_quality_score(10, 10, 10, 10) == 0.0


def test_gating_status_blocked() -> None:
    cfg = ReviewConfiguration(block_on_critical=True)
    status, approved, reasons = ReviewAggregator.evaluate_gating(
        critical_count=1,
        high_count=0,
        medium_count=0,
        low_count=0,
        quality_score=75.0,
        config=cfg,
    )
    assert status == ReviewStatus.BLOCKED
    assert approved is False
    assert len(reasons) >= 1
    assert "critical" in reasons[0].lower()


def test_gating_status_requires_changes() -> None:
    cfg = ReviewConfiguration()
    # High finding present
    status, approved, reasons = ReviewAggregator.evaluate_gating(
        critical_count=0,
        high_count=1,
        medium_count=0,
        low_count=0,
        quality_score=90.0,
        config=cfg,
    )
    assert status == ReviewStatus.REQUIRES_CHANGES
    assert approved is False

    # Low quality score below 70
    status2, approved2, reasons2 = ReviewAggregator.evaluate_gating(
        critical_count=0,
        high_count=0,
        medium_count=11,  # 100 - 33 = 67.0
        low_count=0,
        quality_score=67.0,
        config=cfg,
    )
    assert status2 == ReviewStatus.REQUIRES_CHANGES
    assert approved2 is False


def test_gating_status_approved_with_comments() -> None:
    cfg = ReviewConfiguration()
    status, approved, reasons = ReviewAggregator.evaluate_gating(
        critical_count=0,
        high_count=0,
        medium_count=2,
        low_count=3,
        quality_score=91.0,
        config=cfg,
    )
    assert status == ReviewStatus.APPROVED_WITH_COMMENTS
    assert approved is True
    assert len(reasons) == 0


def test_gating_status_clean_approved() -> None:
    cfg = ReviewConfiguration()
    status, approved, reasons = ReviewAggregator.evaluate_gating(
        critical_count=0,
        high_count=0,
        medium_count=0,
        low_count=0,
        quality_score=100.0,
        config=cfg,
    )
    assert status == ReviewStatus.APPROVED
    assert approved is True
    assert len(reasons) == 0


def test_human_review_flag_logic() -> None:
    cfg = ReviewConfiguration(require_human_on_high=True)

    # High severity triggers human review
    f_high = _make_finding("ARCH-001", "a.py", 1, FindingSeverity.HIGH)
    report_high = ReviewAggregator.aggregate([f_high], ["a.py"], cfg)
    assert report_high.summary.requires_human_review is True

    # Low severity without flag does not trigger human review
    f_low = _make_finding("MAINT-001", "b.py", 1, FindingSeverity.LOW)
    report_low = ReviewAggregator.aggregate([f_low], ["b.py"], cfg)
    assert report_low.summary.requires_human_review is False

    # Explicit flag on low finding triggers human review
    f_low_flagged = _make_finding(
        "MAINT-001", "b.py", 1, FindingSeverity.LOW, requires_human=True
    )
    report_flagged = ReviewAggregator.aggregate([f_low_flagged], ["b.py"], cfg)
    assert report_flagged.summary.requires_human_review is True


def test_report_aggregation_complete() -> None:
    f1 = _make_finding(
        "SEC-001",
        "db.py",
        10,
        FindingSeverity.CRITICAL,
        category=FindingCategory.SECURITY,
        remediation="Use parameterized queries",
    )
    f2 = _make_finding(
        "MAINT-001",
        "util.py",
        5,
        FindingSeverity.LOW,
        category=FindingCategory.MAINTAINABILITY,
        remediation="Refactor long function",
    )

    cfg = ReviewConfiguration()
    report = ReviewAggregator.aggregate(
        findings=[f1, f2],
        files_reviewed=["db.py", "util.py", "clean.py"],
        config=cfg,
        review_id="REV-TEST-1",
        repository_path="/workspace/repo",
        review_scope=ReviewScope.FULL_REPOSITORY,
    )

    assert report.review_id == "REV-TEST-1"
    assert report.status == ReviewStatus.BLOCKED
    assert report.summary.total_findings == 2
    assert report.summary.critical_count == 1
    assert report.summary.low_count == 1
    assert report.summary.files_reviewed == ["clean.py", "db.py", "util.py"]
    assert report.summary.files_with_findings == ["db.py", "util.py"]
    assert len(report.blockers) == 1
    assert "Use parameterized queries" in report.recommendations
    assert "Refactor long function" in report.recommendations
    assert report.duration_ms >= 0.0
