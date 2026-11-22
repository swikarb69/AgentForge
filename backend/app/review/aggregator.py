"""Review finding aggregation, quality scoring, and gating engine."""

import time
import uuid
from datetime import UTC, datetime
from typing import Any

from backend.app.review.models import (
    FindingCategory,
    FindingSeverity,
    ReviewConfiguration,
    ReviewFinding,
    ReviewReport,
    ReviewScope,
    ReviewStatus,
    ReviewSummary,
)


class ReviewAggregator:
    """Aggregates findings, calculates explainable scores, and
    determines approval gating.
    """

    @classmethod
    def aggregate(
        cls,
        findings: list[ReviewFinding],
        files_reviewed: list[str],
        config: ReviewConfiguration,
        review_id: str | None = None,
        repository_path: str = "",
        review_scope: ReviewScope = ReviewScope.PATCH_ONLY,
        reviewed_artifacts: dict[str, str | None] | None = None,
        start_time: float | None = None,
        repository_info: dict[str, Any] | None = None,
    ) -> ReviewReport:
        """Synthesize findings into an auditable ReviewReport with gating verdicts."""
        eff_review_id = review_id or f"rev-{uuid.uuid4().hex[:8]}"
        now_iso = datetime.now(UTC).isoformat()
        elapsed_ms = (
            (time.perf_counter() - start_time) * 1000.0
            if start_time is not None
            else 0.0
        )

        # Step 1: Deduplicate findings
        deduped = cls.deduplicate_findings(findings)

        # Step 2: Order findings by severity (CRITICAL -> HIGH -> MEDIUM -> LOW)
        ordered_findings = cls.sort_findings(deduped)

        # Step 3: Compute severity and category distributions
        sev_counts: dict[str, int] = {s.value: 0 for s in FindingSeverity}
        cat_counts: dict[str, int] = {c.value: 0 for c in FindingCategory}
        files_with_findings: set[str] = set()

        for f in ordered_findings:
            sev_counts[f.severity.value] = sev_counts.get(f.severity.value, 0) + 1
            cat_counts[f.category.value] = cat_counts.get(f.category.value, 0) + 1
            files_with_findings.add(f.file_path)

        critical_count = sev_counts.get(FindingSeverity.CRITICAL.value, 0)
        high_count = sev_counts.get(FindingSeverity.HIGH.value, 0)
        medium_count = sev_counts.get(FindingSeverity.MEDIUM.value, 0)
        low_count = sev_counts.get(FindingSeverity.LOW.value, 0)

        # Step 4: Compute quality score
        quality_score = cls.calculate_quality_score(
            critical_count=critical_count,
            high_count=high_count,
            medium_count=medium_count,
            low_count=low_count,
        )

        # Step 5: Evaluate gating status and blocking reasons
        status, approval_recommended, blocking_reasons = cls.evaluate_gating(
            critical_count=critical_count,
            high_count=high_count,
            medium_count=medium_count,
            low_count=low_count,
            quality_score=quality_score,
            config=config,
        )

        # Step 6: Compute human review requirement
        requires_human = False
        if critical_count > 0:
            requires_human = True
        elif config.require_human_on_high and high_count > 0:
            requires_human = True
        elif any(f.requires_human_review for f in ordered_findings):
            requires_human = True

        # Step 7: Extract blockers and recommendations
        blockers: list[str] = [
            f.description
            for f in ordered_findings
            if f.severity in {FindingSeverity.CRITICAL, FindingSeverity.HIGH}
        ]

        seen_remediations: set[str] = set()
        recommendations: list[str] = []
        for f in ordered_findings:
            if f.remediation and f.remediation not in seen_remediations:
                seen_remediations.add(f.remediation)
                recommendations.append(f.remediation)

        summary = ReviewSummary(
            total_findings=len(ordered_findings),
            findings_by_severity=sev_counts,
            findings_by_category=cat_counts,
            files_reviewed=sorted(set(files_reviewed)),
            files_with_findings=sorted(files_with_findings),
            critical_findings=critical_count,
            high_risk_findings=high_count,
            critical_count=critical_count,
            high_count=high_count,
            medium_count=medium_count,
            low_count=low_count,
            requires_human_review=requires_human,
            review_status=status,
            approval_recommended=approval_recommended,
            blocking_reasons=blocking_reasons,
            quality_score=quality_score,
        )

        return ReviewReport(
            review_id=eff_review_id,
            repository_path=repository_path,
            repository_info=repository_info or {},
            reviewed_artifacts=reviewed_artifacts or {},
            status=status,
            review_scope=review_scope,
            configuration=config,
            summary=summary,
            findings=ordered_findings,
            blockers=blockers,
            recommendations=recommendations,
            analysis_metadata={
                "analyzers": [
                    "DeterministicStaticAnalyzer",
                    "SecurityAnalyzer",
                    "ArchitectureAnalyzer",
                    "RegressionAnalyzer",
                ],
                "files_count": len(files_reviewed),
            },
            limitations=[],
            generated_at=now_iso,
            completed_at=now_iso,
            duration_ms=round(elapsed_ms, 2),
        )

    @staticmethod
    def deduplicate_findings(
        findings: list[ReviewFinding],
    ) -> list[ReviewFinding]:
        """Deduplicate findings sharing rule_id, file_path, start_line, and end_line."""
        seen: dict[tuple[str, str, int, int], ReviewFinding] = {}

        severity_rank = {
            FindingSeverity.LOW: 1,
            FindingSeverity.MEDIUM: 2,
            FindingSeverity.HIGH: 3,
            FindingSeverity.CRITICAL: 4,
        }

        for f in findings:
            key = (f.rule_id, f.file_path, f.start_line, f.end_line)
            if key not in seen:
                seen[key] = f
            else:
                existing = seen[key]
                # Keep higher severity if different
                if severity_rank.get(f.severity, 1) > severity_rank.get(
                    existing.severity, 1
                ):
                    seen[key] = f

        return list(seen.values())

    @staticmethod
    def sort_findings(
        findings: list[ReviewFinding],
    ) -> list[ReviewFinding]:
        """Order findings by severity descending, then file path and line number."""
        severity_rank = {
            FindingSeverity.CRITICAL: 0,
            FindingSeverity.HIGH: 1,
            FindingSeverity.MEDIUM: 2,
            FindingSeverity.LOW: 3,
        }

        return sorted(
            findings,
            key=lambda f: (
                severity_rank.get(f.severity, 4),
                f.file_path,
                f.start_line,
            ),
        )

    @staticmethod
    def calculate_quality_score(
        critical_count: int,
        high_count: int,
        medium_count: int,
        low_count: int,
    ) -> float:
        """Calculate explainable 0.0 - 100.0 quality score using
        deterministic penalties.
        """
        base = 100.0
        deductions = (
            (critical_count * 25.0)
            + (high_count * 10.0)
            + (medium_count * 3.0)
            + (low_count * 1.0)
        )
        score = base - deductions
        return max(0.0, min(100.0, score))

    @staticmethod
    def evaluate_gating(
        critical_count: int,
        high_count: int,
        medium_count: int,
        low_count: int,
        quality_score: float,
        config: ReviewConfiguration,
    ) -> tuple[ReviewStatus, bool, list[str]]:
        """Determine gating approval status and explanations for blockers."""
        blocking_reasons: list[str] = []

        if critical_count > 0 and config.block_on_critical:
            blocking_reasons.append(
                f"{critical_count} critical finding(s) blocking automated approval."
            )
            return ReviewStatus.BLOCKED, False, blocking_reasons

        if high_count > 0 or quality_score < 70.0:
            if high_count > 0:
                blocking_reasons.append(
                    f"{high_count} high-severity finding(s) require remediation."
                )
            if quality_score < 70.0:
                blocking_reasons.append(
                    f"Overall quality score ({quality_score:.1f}) is below "
                    "acceptance threshold (70.0)."
                )
            return ReviewStatus.REQUIRES_CHANGES, False, blocking_reasons

        if medium_count > 0 or low_count > 0:
            return ReviewStatus.APPROVED_WITH_COMMENTS, True, []

        return ReviewStatus.APPROVED, True, []
