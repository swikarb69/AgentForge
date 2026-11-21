"""Verification engine synthesizing sandbox outcomes into structured reports."""

from backend.app.execution.models import (
    ExecutionResult,
    ExecutionStatus,
    PatchApplicationResult,
    VerificationReport,
)


class VerificationEngine:
    """Evaluates patch and test outcomes to produce verification reports."""

    @classmethod
    def evaluate(
        cls,
        patch_result: PatchApplicationResult,
        execution_result: ExecutionResult,
    ) -> VerificationReport:
        """Synthesize patch application and test runner outcomes into a report."""
        patch_applied = patch_result.success
        tests_executed = (
            execution_result.status
            not in (ExecutionStatus.REJECTED, ExecutionStatus.PENDING)
            and execution_result.started_at is not None
        )
        execution_success = execution_result.exit_code == 0

        failures: list[str] = []
        warnings: list[str] = []

        # Collect patch errors
        if not patch_applied:
            failures.extend(patch_result.errors or ["Patch application failed."])
        warnings.extend(patch_result.warnings)

        # Collect execution errors
        if execution_result.errors:
            failures.extend(execution_result.errors)
        warnings.extend(execution_result.warnings)

        # Evaluate test summary
        test_summary = execution_result.test_summary
        if test_summary:
            if test_summary.failed > 0:
                failures.append(
                    f"{test_summary.failed} test(s) failed during sandbox verification."
                )
            if test_summary.errors > 0:
                failures.append(
                    f"{test_summary.errors} test error(s) occurred during execution."
                )
            if test_summary.total == 0 and execution_success:
                warnings.append("Zero tests were collected or executed by test runner.")

        if not execution_success and not failures:
            failures.append(
                f"Container execution exited with code {execution_result.exit_code}."
            )

        verification_passed = (
            patch_applied
            and execution_success
            and len(failures) == 0
            and (test_summary is None or test_summary.is_all_passed)
        )

        return VerificationReport(
            execution_id=execution_result.execution_id,
            execution_success=execution_success,
            patch_applied=patch_applied,
            tests_executed=tests_executed,
            verification_passed=verification_passed,
            failures=failures,
            warnings=warnings,
            execution_result=execution_result,
        )
