"""Deterministic failure classification for sandboxed execution outcomes."""

import re

from backend.app.debugging.models import FailureCategory, FailureClassification
from backend.app.execution.models import ExecutionStatus, VerificationReport


class FailureClassifier:
    """Classifies execution and test failures into deterministic categories."""

    # Patterns for regex identification in stdout/stderr/error messages
    SYNTAX_PATTERNS = [
        re.compile(r"SyntaxError:\s*(.+)", re.IGNORECASE),
        re.compile(r"IndentationError:\s*(.+)", re.IGNORECASE),
        re.compile(r"TabError:\s*(.+)", re.IGNORECASE),
    ]

    IMPORT_PATTERNS = [
        re.compile(
            r"ModuleNotFoundError:\s*No module named ['\"]([^'\"]+)['\"]", re.IGNORECASE
        ),
        re.compile(
            r"ImportError:\s*cannot import name ['\"]([^'\"]+)['\"]", re.IGNORECASE
        ),
        re.compile(r"ImportError:\s*(.+)", re.IGNORECASE),
    ]

    TYPE_PATTERNS = [
        re.compile(r"TypeError:\s*(.+)", re.IGNORECASE),
        re.compile(
            r"error:\s*Argument \d+ to \S+ has incompatible type", re.IGNORECASE
        ),
        re.compile(r"error:\s*Incompatible return value type", re.IGNORECASE),
        re.compile(r"error:\s*Item \S+ of \S+ has no attribute", re.IGNORECASE),
    ]

    LINT_PATTERNS = [
        re.compile(r"\b[EFWBCN]\d{3,4}\b"),  # Flake8 / Ruff error codes
        re.compile(r"ruff failed|linting error", re.IGNORECASE),
    ]

    TIMEOUT_PATTERNS = [
        re.compile(r"timed out after \d+ seconds", re.IGNORECASE),
        re.compile(r"Command '.*' timed out", re.IGNORECASE),
        re.compile(r"Execution timed out", re.IGNORECASE),
    ]

    RESOURCE_PATTERNS = [
        re.compile(r"MemoryError", re.IGNORECASE),
        re.compile(r"OOMKilled|out of memory", re.IGNORECASE),
        re.compile(r"Killed\s*$", re.MULTILINE),
    ]

    ENVIRONMENT_PATTERNS = [
        re.compile(r"Docker CLI is not available", re.IGNORECASE),
        re.compile(r"Cannot connect to the Docker daemon", re.IGNORECASE),
        re.compile(r"docker: command not found", re.IGNORECASE),
        re.compile(r"docker daemon is not running", re.IGNORECASE),
    ]

    DEPENDENCY_PATTERNS = [
        re.compile(r"DistributionNotFound:\s*(.+)", re.IGNORECASE),
        re.compile(r"VersionConflict:\s*(.+)", re.IGNORECASE),
        re.compile(r"No matching distribution found for\s*(.+)", re.IGNORECASE),
    ]

    def classify(self, report: VerificationReport) -> FailureClassification:
        """Classify a verification report into a deterministic FailureClassification."""
        # 1. Clean verification pass
        if report.verification_passed and report.execution_success:
            return FailureClassification(
                category=FailureCategory.UNKNOWN,
                confidence=1.0,
                explanation=(
                    "Execution passed verification cleanly. No failure identified."
                ),
                auto_repair_permitted=False,
                human_intervention_required=False,
            )

        # 2. Patch application failure
        if not report.patch_applied:
            error_msgs = list(report.failures)
            return FailureClassification(
                category=FailureCategory.PATCH_APPLICATION_ERROR,
                confidence=1.0,
                evidence_references=["patch_applier"],
                error_messages=error_msgs,
                explanation=(
                    "The proposed patch could not be applied cleanly to the "
                    "isolated workspace."
                ),
                auto_repair_permitted=False,
                human_intervention_required=True,
            )

        # Gather combined text from execution result
        combined_text = ""
        error_messages: list[str] = list(report.failures)
        evidence_refs: list[str] = []
        exit_code: int | None = None
        status: ExecutionStatus | None = None

        if report.execution_result:
            exit_code = report.execution_result.exit_code
            status = report.execution_result.status
            if report.execution_result.stdout:
                combined_text += f"\n{report.execution_result.stdout}"
            if report.execution_result.stderr:
                combined_text += f"\n{report.execution_result.stderr}"
                error_messages.append(report.execution_result.stderr.strip())
            for err in report.execution_result.errors:
                error_messages.append(err)

        # 3. Environment or Docker infrastructure failure (Exit code 127)
        for pat in self.ENVIRONMENT_PATTERNS:
            if pat.search(combined_text) or any(pat.search(m) for m in error_messages):
                return FailureClassification(
                    category=FailureCategory.ENVIRONMENT_ERROR,
                    confidence=1.0,
                    evidence_references=["docker_infrastructure"],
                    error_messages=[m for m in error_messages if m],
                    explanation=(
                        "Docker execution environment is offline, missing, or "
                        "inaccessible. No host fallback is permitted."
                    ),
                    auto_repair_permitted=False,
                    human_intervention_required=True,
                )

        if exit_code == 127 and (
            "docker" in combined_text.lower()
            or any("docker" in m.lower() for m in error_messages)
        ):
            return FailureClassification(
                category=FailureCategory.ENVIRONMENT_ERROR,
                confidence=0.95,
                evidence_references=["exit_code_127"],
                error_messages=error_messages,
                explanation=(
                    "Container runner exited with 127: Docker binary or daemon "
                    "unavailable."
                ),
                auto_repair_permitted=False,
                human_intervention_required=True,
            )

        # 4. Timeout
        if (
            status == ExecutionStatus.TIMED_OUT
            or exit_code == 124
            or any(p.search(combined_text) for p in self.TIMEOUT_PATTERNS)
        ):
            return FailureClassification(
                category=FailureCategory.TIMEOUT,
                confidence=0.95,
                evidence_references=["execution_timeout"],
                error_messages=error_messages,
                explanation="Container execution exceeded permitted timeout limit.",
                auto_repair_permitted=False,
                human_intervention_required=True,
            )

        # 5. Resource exhaustion (OOM)
        if exit_code == 137 or any(
            p.search(combined_text) for p in self.RESOURCE_PATTERNS
        ):
            return FailureClassification(
                category=FailureCategory.RESOURCE_EXHAUSTION,
                confidence=0.95,
                evidence_references=["out_of_memory"],
                error_messages=error_messages,
                explanation=(
                    "Process was killed due to memory or container resource exhaustion."
                ),
                auto_repair_permitted=False,
                human_intervention_required=True,
            )

        # 6. Syntax error in code
        for pat in self.SYNTAX_PATTERNS:
            match = pat.search(combined_text)
            if match:
                evidence_refs.append(match.group(0))
                return FailureClassification(
                    category=FailureCategory.SYNTAX_ERROR,
                    confidence=1.0,
                    evidence_references=evidence_refs,
                    error_messages=[match.group(0)],
                    explanation=f"Python syntax error encountered: {match.group(0)}",
                    auto_repair_permitted=True,
                    human_intervention_required=False,
                )

        # 7. Import error
        for pat in self.IMPORT_PATTERNS:
            match = pat.search(combined_text)
            if match:
                evidence_refs.append(match.group(0))
                return FailureClassification(
                    category=FailureCategory.IMPORT_ERROR,
                    confidence=0.95,
                    evidence_references=evidence_refs,
                    error_messages=[match.group(0)],
                    explanation=f"Module or symbol import error: {match.group(0)}",
                    auto_repair_permitted=True,
                    human_intervention_required=False,
                )

        # 8. Dependency error (third-party package resolution)
        for pat in self.DEPENDENCY_PATTERNS:
            match = pat.search(combined_text)
            if match:
                evidence_refs.append(match.group(0))
                return FailureClassification(
                    category=FailureCategory.DEPENDENCY_ERROR,
                    confidence=0.90,
                    evidence_references=evidence_refs,
                    error_messages=[match.group(0)],
                    explanation=(
                        f"Missing or conflicting package dependency: {match.group(0)}"
                    ),
                    auto_repair_permitted=False,
                    human_intervention_required=True,
                )

        # 9. Type error
        for pat in self.TYPE_PATTERNS:
            match = pat.search(combined_text)
            if match:
                evidence_refs.append(match.group(0))
                return FailureClassification(
                    category=FailureCategory.TYPE_ERROR,
                    confidence=0.90,
                    evidence_references=evidence_refs,
                    error_messages=[match.group(0)],
                    explanation=f"Type handling defect encountered: {match.group(0)}",
                    auto_repair_permitted=True,
                    human_intervention_required=False,
                )

        # 10. Lint error
        if "ruff" in combined_text.lower() and exit_code not in (None, 0):
            matches = [
                m.group(0) for m in self.LINT_PATTERNS[0].finditer(combined_text)
            ]
            if matches:
                return FailureClassification(
                    category=FailureCategory.LINT_ERROR,
                    confidence=0.90,
                    evidence_references=matches[:5],
                    error_messages=[
                        f"Linter rule violations detected: {', '.join(matches[:5])}"
                    ],
                    explanation="Code style or quality violations detected by linter.",
                    auto_repair_permitted=True,
                    human_intervention_required=False,
                )

        # 11. Test assertion failure
        if report.execution_result and report.execution_result.test_summary:
            summary = report.execution_result.test_summary
            if summary.failed > 0:
                failed_names = re.findall(r"FAILED\s+([^\s]+)", combined_text)
                return FailureClassification(
                    category=FailureCategory.TEST_FAILURE,
                    confidence=0.95,
                    evidence_references=failed_names[:10],
                    error_messages=[f"{summary.failed} test(s) failed in test suite."],
                    explanation=(
                        f"Test suite reported {summary.failed} test "
                        "assertion failure(s)."
                    ),
                    auto_repair_permitted=True,
                    human_intervention_required=False,
                )

        if (
            "assert" in combined_text.lower()
            or "assertionerror" in combined_text.lower()
        ):
            return FailureClassification(
                category=FailureCategory.TEST_FAILURE,
                confidence=0.85,
                evidence_references=["AssertionError in output"],
                error_messages=["AssertionError encountered during test run."],
                explanation="Assertion failed during test execution.",
                auto_repair_permitted=True,
                human_intervention_required=False,
            )

        # 12. Fallback: Unknown failure
        return FailureClassification(
            category=FailureCategory.UNKNOWN,
            confidence=0.40,
            evidence_references=["unrecognized_error_signature"],
            error_messages=error_messages
            if error_messages
            else ["Non-zero exit code without recognized error signature."],
            explanation="Execution failure could not be deterministically categorized.",
            auto_repair_permitted=False,
            human_intervention_required=True,
        )
