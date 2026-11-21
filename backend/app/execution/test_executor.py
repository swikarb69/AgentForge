"""Test execution orchestration and test runner output parsing."""

import re
from pathlib import Path

from backend.app.execution.docker_runner import DockerRunner
from backend.app.execution.models import (
    ExecutionPolicy,
    ExecutionResult,
    TestSummary,
    VerificationProfile,
)
from backend.app.execution.policy import ExecutionPolicyEnforcer


class TestExecutor:
    """Orchestrates test execution inside container sandbox and parses test outcomes."""

    __test__: bool = False

    def __init__(self, docker_runner: DockerRunner | None = None) -> None:
        self.runner = docker_runner or DockerRunner()

    @classmethod
    def parse_pytest_output(
        cls, output_text: str, exit_code: int | None
    ) -> TestSummary:
        """Parse pytest stdout/stderr text into a structured TestSummary."""
        cleaned = output_text or ""

        # Search for pytest summary: e.g. "5 passed, 2 failed in 0.12s"
        passed_m = re.search(r"(\d+)\s+passed", cleaned)
        failed_m = re.search(r"(\d+)\s+failed", cleaned)
        errors_m = re.search(r"(\d+)\s+error", cleaned)
        skipped_m = re.search(r"(\d+)\s+skipped", cleaned)

        passed = int(passed_m.group(1)) if passed_m else 0
        failed = int(failed_m.group(1)) if failed_m else 0
        errors = int(errors_m.group(1)) if errors_m else 0
        skipped = int(skipped_m.group(1)) if skipped_m else 0

        total = passed + failed + errors + skipped

        # If no explicit summary numbers found, infer from exit code and output
        if total == 0:
            if exit_code == 0 and (
                "passed" in cleaned.lower() or "ok" in cleaned.lower()
            ):
                total = 1
                passed = 1
            elif exit_code != 0:
                # Execution failed without test summary (e.g. collection error or crash)
                total = 1
                errors = 1

        return TestSummary(
            total=total,
            passed=passed,
            failed=failed,
            skipped=skipped,
            errors=errors,
        )

    @classmethod
    def parse_ruff_output(cls, output_text: str, exit_code: int | None) -> TestSummary:
        """Parse ruff check output text into a structured TestSummary."""
        cleaned = output_text or ""

        if "All checks passed!" in cleaned or exit_code == 0:
            return TestSummary(total=1, passed=1, failed=0, errors=0)

        err_m = re.search(r"Found\s+(\d+)\s+error", cleaned)
        if err_m:
            count = int(err_m.group(1))
            return TestSummary(total=count, passed=0, failed=count, errors=0)

        # Fallback for non-zero exit
        return TestSummary(total=1, passed=0, failed=1, errors=0)

    @classmethod
    def parse_output(
        cls,
        profile: VerificationProfile,
        output_text: str,
        exit_code: int | None,
    ) -> TestSummary:
        """Parse output according to verification profile."""
        if profile == VerificationProfile.PYTHON_QUALITY_CHECKS:
            return cls.parse_ruff_output(output_text, exit_code)
        # Default to pytest output parser
        return cls.parse_pytest_output(output_text, exit_code)

    def run_tests(
        self,
        workspace_path: str | Path,
        profile: VerificationProfile = VerificationProfile.PYTHON_UNIT_TESTS,
        policy: ExecutionPolicy | None = None,
        environment: dict[str, str] | None = None,
        execution_id: str | None = None,
    ) -> ExecutionResult:
        """Execute verification profile synchronously and attach parsed TestSummary."""
        active_policy = policy or ExecutionPolicy()
        command = ExecutionPolicyEnforcer.get_profile_command(profile, active_policy)

        result = self.runner.execute(
            workspace_path=workspace_path,
            command=command,
            policy=active_policy,
            environment=environment,
            execution_id=execution_id,
        )

        combined_output = f"{result.stdout}\n{result.stderr}"
        result.test_summary = self.parse_output(
            profile=profile,
            output_text=combined_output,
            exit_code=result.exit_code,
        )
        return result

    async def run_tests_async(
        self,
        workspace_path: str | Path,
        profile: VerificationProfile = VerificationProfile.PYTHON_UNIT_TESTS,
        policy: ExecutionPolicy | None = None,
        environment: dict[str, str] | None = None,
        execution_id: str | None = None,
    ) -> ExecutionResult:
        """Execute verification profile asynchronously and attach parsed TestSummary."""
        active_policy = policy or ExecutionPolicy()
        command = ExecutionPolicyEnforcer.get_profile_command(profile, active_policy)

        result = await self.runner.execute_async(
            workspace_path=workspace_path,
            command=command,
            policy=active_policy,
            environment=environment,
            execution_id=execution_id,
        )

        combined_output = f"{result.stdout}\n{result.stderr}"
        result.test_summary = self.parse_output(
            profile=profile,
            output_text=combined_output,
            exit_code=result.exit_code,
        )
        return result
