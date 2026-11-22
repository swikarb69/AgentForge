"""Repair evaluation engine comparing pre-repair and post-repair outcomes."""

import re
import time

from backend.app.debugging.models import (
    RepairEvaluation,
    RepairEvaluationVerdict,
    RepairProposal,
)
from backend.app.execution.models import ExecutionStatus, VerificationReport
from backend.app.planning.models import ImplementationPlan


class RepairEvaluator:
    """Evaluates test outcomes before and after code repair to compute verdicts."""

    FAILED_TEST_PATTERN = re.compile(r"FAILED\s+([^\s]+)")

    def _extract_failures(self, report: VerificationReport) -> set[str]:
        """Extract explicit failed test names and failure reasons from report."""
        failures: set[str] = set()

        if report.failures:
            for f in report.failures:
                failures.add(f.strip())

        if report.execution_result:
            combined = (
                f"{report.execution_result.stdout}\n{report.execution_result.stderr}"
            )
            for match in self.FAILED_TEST_PATTERN.finditer(combined):
                failures.add(match.group(1).strip())

            for err in report.execution_result.errors:
                failures.add(err.strip())

        return {f for f in failures if f}

    def evaluate(
        self,
        pre_report: VerificationReport,
        post_report: VerificationReport,
        plan: ImplementationPlan | None = None,
        proposal: RepairProposal | None = None,
    ) -> RepairEvaluation:
        """Compare pre-repair and post-repair reports to derive evaluation."""
        start_time = time.monotonic()

        pre_failures = self._extract_failures(pre_report)
        post_failures = self._extract_failures(post_report)

        # Failure comparisons
        remaining_failures = sorted(pre_failures & post_failures)
        new_failures = sorted(post_failures - pre_failures)
        resolved_failures = sorted(pre_failures - post_failures)

        original_resolved = len(remaining_failures) == 0 and len(pre_failures) > 0
        regressions = len(new_failures) > 0

        # Scope preservation check
        scope_preserved = True
        if proposal and proposal.validation:
            scope_preserved = proposal.validation.valid

        # Extract criteria
        all_criteria: list[str] = []
        if proposal and proposal.verification_criteria:
            all_criteria = list(proposal.verification_criteria)
        elif plan and plan.test_plan:
            all_criteria = [
                vc.description
                for vc in plan.test_plan.verification_cases
                if vc.description
            ]

        # Verdict calculation
        if post_report.verification_passed and post_report.execution_success:
            if not regressions and scope_preserved:
                verdict = RepairEvaluationVerdict.FIXED
                verified_criteria = all_criteria
                unverified_criteria: list[str] = []
                summary = (
                    f"Repair succeeded: All tests passed cleanly. "
                    f"Resolved {len(resolved_failures)} previous failure(s)."
                )
            else:
                verdict = RepairEvaluationVerdict.NEEDS_HUMAN_REVIEW
                verified_criteria = []
                unverified_criteria = all_criteria
                summary = (
                    "Tests passed but scope violations or regressions were flagged."
                )
        elif regressions:
            verdict = RepairEvaluationVerdict.REGRESSION_DETECTED
            verified_criteria = []
            unverified_criteria = all_criteria
            summary = (
                f"Regression detected: {len(new_failures)} new failure(s) introduced: "
                f"{', '.join(new_failures[:3])}"
            )
        elif len(resolved_failures) > 0 and len(remaining_failures) > 0:
            verdict = RepairEvaluationVerdict.PARTIALLY_FIXED
            # Split criteria proportionally
            split_idx = max(1, len(all_criteria) // 2) if all_criteria else 0
            verified_criteria = all_criteria[:split_idx]
            unverified_criteria = all_criteria[split_idx:]
            summary = (
                f"Partially resolved: {len(resolved_failures)} failure(s) fixed, "
                f"{len(remaining_failures)} failure(s) remain."
            )
        elif (
            not post_report.patch_applied
            or post_report.execution_result is None
            or post_report.execution_result.status
            in (
                ExecutionStatus.TIMED_OUT,
                ExecutionStatus.REJECTED,
                ExecutionStatus.CANCELLED,
            )
        ):
            verdict = RepairEvaluationVerdict.INCONCLUSIVE
            verified_criteria = []
            unverified_criteria = all_criteria
            summary = (
                "Evaluation inconclusive: Patch application failed, or container "
                "execution timed out or was rejected."
            )
        else:
            verdict = RepairEvaluationVerdict.NOT_FIXED
            verified_criteria = []
            unverified_criteria = all_criteria
            summary = (
                f"Repair did not resolve defect. {len(remaining_failures)} failure(s) "
                "persist unchanged."
            )

        duration = round(time.monotonic() - start_time, 4)

        return RepairEvaluation(
            verdict=verdict,
            original_failures_resolved=original_resolved,
            remaining_failures=remaining_failures,
            new_failures_introduced=new_failures,
            regressions_detected=regressions,
            acceptance_criteria_verified=verified_criteria,
            unverified_criteria=unverified_criteria,
            scope_preserved=scope_preserved,
            summary=summary,
            duration_seconds=duration,
        )
