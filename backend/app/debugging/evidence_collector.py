"""Diagnostic evidence collector with secret sanitization and bounded extraction."""

import re
from typing import Any

from backend.app.debugging.models import DiagnosticEvidence, RepairConfiguration
from backend.app.execution.models import ExecutionStatus, VerificationReport
from backend.app.planning.models import ImplementationPlan


class DiagnosticEvidenceCollector:
    """Collects bounded, sanitized diagnostic evidence from execution reports."""

    # Regex patterns for secret detection
    SECRET_PATTERNS = [
        re.compile(r"sk-[a-zA-Z0-9]{20,}", re.IGNORECASE),  # OpenAI
        re.compile(r"AIzaSy[a-zA-Z0-9_-]{33}", re.IGNORECASE),  # Google / Gemini
        re.compile(r"gh[pousr]_[a-zA-Z0-9]{36,}", re.IGNORECASE),  # GitHub Token
        re.compile(
            r"github_pat_[a-zA-Z0-9_]{50,}", re.IGNORECASE
        ),  # GitHub Fine-grained PAT
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----"
            r"[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"
        ),
        re.compile(r"bearer\s+[a-zA-Z0-9_\-\.]{25,}", re.IGNORECASE),  # Bearer token
        re.compile(
            r"eyJ[a-zA-Z0-9_\-]{10,}\.[a-zA-Z0-9_\-]{10,}\.[a-zA-Z0-9_\-]+",
            re.IGNORECASE,
        ),  # JWT
        re.compile(
            r"(password|secret|api_key|auth_token)\s*[:=]\s*['\"]?([a-zA-Z0-9_\-\.\$]{8,})['\"]?",
            re.IGNORECASE,
        ),
    ]

    ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
    TRACEBACK_FRAME = re.compile(
        r'File\s+["\']([^"\']+)["\'],\s+line\s+(\d+)(?:,\s+in\s+([a-zA-Z0-9_<>]+))?'
    )
    FAILED_TEST_PATTERN = re.compile(r"FAILED\s+([^\s]+)")

    def __init__(self, config: RepairConfiguration | None = None) -> None:
        self.config = config or RepairConfiguration()

    def redact_secrets(self, text: str) -> tuple[str, int]:
        """Redact sensitive credentials.

        Returns tuple of (sanitized_text, redaction_count).
        """
        sanitized = text

        redacted_count = 0

        for pattern in self.SECRET_PATTERNS:
            matches = list(pattern.finditer(sanitized))
            if matches:
                redacted_count += len(matches)
                sanitized = pattern.sub("[REDACTED_SECRET]", sanitized)

        return sanitized, redacted_count

    def strip_ansi(self, text: str) -> str:
        """Strip ANSI terminal escape sequences."""
        return self.ANSI_ESCAPE.sub("", text)

    def collect(
        self,
        report: VerificationReport,
        plan: ImplementationPlan | None = None,
        previous_attempt_count: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> DiagnosticEvidence:
        """Extract bounded, sanitized diagnostic evidence from a verification report."""
        metadata = metadata or {}
        truncated = False
        truncation_reasons: list[str] = []
        total_redactions = 0

        # Combine command outputs
        raw_output = ""
        if report.execution_result:
            if report.execution_result.stdout:
                raw_output += f"\n{report.execution_result.stdout}"
            if report.execution_result.stderr:
                raw_output += f"\n{report.execution_result.stderr}"
            if report.execution_result.errors:
                raw_output += "\n" + "\n".join(report.execution_result.errors)
        if report.failures:
            raw_output += "\n" + "\n".join(report.failures)

        # 1. Clean ANSI control sequences
        clean_text = self.strip_ansi(raw_output)

        # 2. Redact sensitive information
        clean_text, redactions = self.redact_secrets(clean_text)
        total_redactions += redactions

        # 3. Check raw log size limits
        if len(clean_text.encode("utf-8")) > self.config.max_evidence_bytes:
            clean_text = clean_text[: self.config.max_evidence_bytes]
            truncated = True
            truncation_reasons.append(
                f"Raw log output exceeded {self.config.max_evidence_bytes} bytes"
            )

        # 4. Extract failed test names
        failed_tests: list[str] = []
        implicated_files: list[str] = []
        for match in self.FAILED_TEST_PATTERN.finditer(clean_text):
            test_name = match.group(1).strip()
            if test_name not in failed_tests:
                failed_tests.append(test_name)
            if "::" in test_name:
                tfile = test_name.split("::")[0].strip().replace("\\", "/")
                if tfile not in implicated_files:
                    implicated_files.append(tfile)

        # 5. Extract traceback lines
        traceback_snippets: list[str] = []
        implicated_symbols: list[str] = []

        lines = clean_text.splitlines()
        tb_recording = False
        current_tb: list[str] = []

        for line in lines:
            if "Traceback (most recent call last):" in line:
                tb_recording = True
                current_tb = [line]
                continue
            if tb_recording:
                current_tb.append(line)
                if (
                    not line.startswith(" ")
                    and not line.startswith("\t")
                    and len(current_tb) > 2
                ):
                    # End of traceback reached (e.g. exception type line)
                    traceback_snippets.extend(current_tb)
                    tb_recording = False
                    current_tb = []

        if current_tb:
            traceback_snippets.extend(current_tb)

        # Truncate traceback frames if they exceed max lines
        if len(traceback_snippets) > self.config.max_traceback_lines:
            traceback_snippets = traceback_snippets[-self.config.max_traceback_lines :]
            truncated = True
            truncation_reasons.append(
                f"Traceback exceeded {self.config.max_traceback_lines} lines"
            )

        # Parse stack frames for implicated files and symbols
        for tb_line in traceback_snippets:
            frame_match = self.TRACEBACK_FRAME.search(tb_line)
            if frame_match:
                file_path = frame_match.group(1).replace("\\", "/")
                # Ignore standard library and third party python lib paths
                if not (
                    "/lib/" in file_path.lower()
                    or "/site-packages/" in file_path.lower()
                ):
                    if file_path not in implicated_files:
                        implicated_files.append(file_path)
                func_name = frame_match.group(3)
                if (
                    func_name
                    and func_name not in implicated_symbols
                    and func_name != "<module>"
                ):
                    implicated_symbols.append(func_name)

        # 6. Extract error messages
        error_messages: list[str] = []
        for line in lines:
            if any(
                keyword in line
                for keyword in (
                    "Error:",
                    "Exception:",
                    "FAILED",
                    "AssertionError",
                    "TypeError:",
                    "SyntaxError:",
                    "ImportError:",
                    "ModuleNotFoundError:",
                )
            ):
                stripped = line.strip()
                if stripped and stripped not in error_messages:
                    error_messages.append(stripped)

        # 7. Plan criteria
        plan_id = plan.id if plan else None
        acceptance_criteria: list[str] = []
        if plan and plan.test_plan:
            for case in plan.test_plan.verification_cases:
                if case.description and case.description not in acceptance_criteria:
                    acceptance_criteria.append(case.description)

        # 8. Patch summary
        patch_summary = None
        if metadata.get("patch_content"):
            patch_text = str(metadata["patch_content"])
            diff_headers = [
                line for line in patch_text.splitlines() if line.startswith("+++ b/")
            ]
            first_three = ", ".join(h[6:] for h in diff_headers[:3])
            patch_summary = f"Patch modified {len(diff_headers)} files: {first_three}"

        return DiagnosticEvidence(
            execution_id=report.execution_id,
            execution_status=(
                report.execution_result.status
                if report.execution_result
                else ExecutionStatus.FAILED
            ),
            test_summary=(
                report.execution_result.test_summary
                if report.execution_result
                else None
            ),
            failed_test_names=failed_tests[:20],
            error_messages=error_messages[:15],
            traceback_snippets=traceback_snippets,
            relevant_file_paths=implicated_files[:10],
            relevant_symbols=implicated_symbols[:10],
            patch_summary=patch_summary,
            previous_attempt_count=previous_attempt_count,
            plan_id=plan_id,
            acceptance_criteria=acceptance_criteria[:20],
            truncated=truncated,
            truncation_reasons=truncation_reasons,
            sanitized_secret_count=total_redactions,
        )
