"""High-level service coordinating code review analysis, aggregation, and reporting."""

import fnmatch
import logging
import re
import time
from pathlib import Path
from typing import Any

from backend.app.execution.models import VerificationReport
from backend.app.generation.models import GenerationResult
from backend.app.planning.models import ImplementationPlan
from backend.app.review.aggregator import ReviewAggregator
from backend.app.review.architecture_analyzer import ArchitectureAnalyzer
from backend.app.review.evidence import EvidenceCollector
from backend.app.review.models import (
    ReviewFinding,
    ReviewReport,
    ReviewRequest,
    ReviewScope,
    ReviewStatus,
)
from backend.app.review.regression_analyzer import RegressionAnalyzer
from backend.app.review.security_analyzer import SecurityAnalyzer
from backend.app.review.static_analyzer import DeterministicStaticAnalyzer

logger = logging.getLogger(__name__)

DEFAULT_IGNORED_DIRS = {
    ".git",
    "venv",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    "dist",
    "build",
    ".eggs",
}


def _apply_hunks_in_memory(
    orig_lines: list[str],
    lines: list[str],
) -> tuple[list[str], str | None]:
    """Apply unified diff hunks sequentially in memory without touching disk."""
    hunks: list[list[str]] = []
    current_hunk: list[str] = []
    for line in lines:
        if line.startswith("@@"):
            if current_hunk:
                hunks.append(current_hunk)
            current_hunk = [line]
        elif current_hunk:
            current_hunk.append(line)
    if current_hunk:
        hunks.append(current_hunk)

    result_lines = list(orig_lines)
    offset = 0

    hunk_re = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
    for hunk in hunks:
        hunk_header = hunk[0]
        match = hunk_re.match(hunk_header)
        if not match:
            return result_lines, f"Invalid hunk header: {hunk_header}"

        old_start = int(match.group(1))
        target_idx = max(0, old_start - 1 + offset)

        body = hunk[1:]
        del_count = 0
        insertions: list[str] = []
        pos = target_idx

        for body_line in body:
            if not body_line:
                continue
            marker = body_line[0]
            content = body_line[1:]
            if marker == " ":
                pos += 1
            elif marker == "-":
                if pos < len(result_lines):
                    result_lines.pop(pos)
                    del_count += 1
            elif marker == "+":
                insertions.append(content)
                result_lines.insert(pos, content)
                pos += 1

        offset += len(insertions) - del_count

    return result_lines, None


def extract_files_from_unified_diff(
    diff_text: str,
    repo_path: Path | None = None,
) -> dict[str, str]:
    """Extract affected files and contents in-memory from a unified diff."""
    files: dict[str, str] = {}
    chunks = diff_text.split("diff --git ")
    for chunk in chunks:
        if not chunk.strip():
            continue
        lines = chunk.splitlines()
        header_old = None
        header_new = None
        for line in lines:
            if line.startswith("--- "):
                header_old = line
            elif line.startswith("+++ "):
                header_new = line
                break

        if not header_old or not header_new:
            continue

        old_file = header_old[4:].strip().split("\t")[0]
        new_file = header_new[4:].strip().split("\t")[0]
        is_creation = old_file == "/dev/null" or old_file.endswith("/dev/null")
        target_rel = new_file.removeprefix("b/").removeprefix("./").strip()

        if is_creation or not repo_path or not (repo_path / target_rel).exists():
            added_lines = [
                ln[1:]
                for ln in lines
                if ln.startswith("+") and not ln.startswith("+++ ")
            ]
            files[target_rel] = "\n".join(added_lines)
        else:
            try:
                orig_text = (repo_path / target_rel).read_text(encoding="utf-8")
                orig_lines = orig_text.splitlines()
                new_lines, err = _apply_hunks_in_memory(orig_lines, lines)
                if not err:
                    files[target_rel] = "\n".join(new_lines)
                else:
                    added_lines = [
                        ln[1:]
                        for ln in lines
                        if ln.startswith("+") and not ln.startswith("+++ ")
                    ]
                    files[target_rel] = "\n".join(added_lines)
            except Exception:
                added_lines = [
                    ln[1:]
                    for ln in lines
                    if ln.startswith("+") and not ln.startswith("+++ ")
                ]
                files[target_rel] = "\n".join(added_lines)

    return files


class ReviewService:
    """Coordinates code review analysis, evidence collection, and report aggregation."""

    def __init__(
        self,
        static_analyzer: DeterministicStaticAnalyzer | None = None,
        security_analyzer: SecurityAnalyzer | None = None,
        architecture_analyzer: ArchitectureAnalyzer | None = None,
        regression_analyzer: RegressionAnalyzer | None = None,
        evidence_collector: EvidenceCollector | None = None,
        aggregator: ReviewAggregator | None = None,
        generation_service: Any | None = None,
        execution_service: Any | None = None,
        debugging_service: Any | None = None,
    ) -> None:
        self.static_analyzer = static_analyzer or DeterministicStaticAnalyzer()
        self.security_analyzer = security_analyzer or SecurityAnalyzer()
        self.architecture_analyzer = architecture_analyzer or ArchitectureAnalyzer()
        self.regression_analyzer = regression_analyzer or RegressionAnalyzer()
        self.evidence_collector = evidence_collector or EvidenceCollector()
        self.aggregator = aggregator or ReviewAggregator(
            evidence_collector=self.evidence_collector
        )
        self.generation_service = generation_service
        self.execution_service = execution_service
        self.debugging_service = debugging_service
        self._reviews: dict[str, ReviewReport] = {}

    def get_review(self, review_id: str) -> ReviewReport | None:
        """Retrieve stored review report by ID."""
        return self._reviews.get(review_id)

    def list_reviews(self) -> list[ReviewReport]:
        """List all stored review reports."""
        return list(self._reviews.values())

    def scan_repository_files(
        self,
        repo_path: Path,
        ignored_patterns: list[str] | None = None,
    ) -> dict[str, str]:
        """Read-only scan of Python files in the given repository path.

        Never alters files or writes to the repository.
        """
        resolved = repo_path.resolve()
        if not resolved.exists() or not resolved.is_dir():
            raise ValueError(f"Repository directory does not exist: {repo_path}")

        files: dict[str, str] = {}
        for py_file in resolved.rglob("*.py"):
            parts = py_file.parts
            if any(ign in parts for ign in DEFAULT_IGNORED_DIRS):
                continue

            rel_str = py_file.relative_to(resolved).as_posix()
            if ignored_patterns:
                if any(fnmatch.fnmatch(rel_str, pat) for pat in ignored_patterns):
                    continue

            try:
                files[rel_str] = py_file.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError) as exc:
                logger.warning("Could not read file %s: %s", rel_str, exc)
                continue

        return files

    async def create_review(
        self,
        request: ReviewRequest,
        files: dict[str, str] | None = None,
        plan: ImplementationPlan | None = None,
        verification_report: VerificationReport | None = None,
        repair_result: Any | None = None,
    ) -> ReviewReport:
        """Execute full review pipeline and return aggregated ReviewReport."""
        start_time = time.perf_counter()
        repo_path = Path(request.repository_path).resolve()
        analyzers_run: list[str] = []

        # 1. Resolve upstream artifacts if IDs are provided
        reviewed_artifacts: dict[str, str | None] = {
            "generation_id": request.generation_id,
            "execution_id": request.execution_id,
            "repair_id": request.repair_id,
            "plan_id": request.plan_id,
            "issue_id": request.issue_id,
        }

        # Resolve files to review
        review_files: dict[str, str] = {}
        if files:
            review_files.update(files)

        # Check metadata for passed files
        if not review_files and "files" in request.metadata:
            meta_files = request.metadata["files"]
            if isinstance(meta_files, dict):
                review_files.update(meta_files)

        # Check GenerationService
        if not review_files and request.generation_id and self.generation_service:
            gen_result = getattr(
                self.generation_service, "get_generation", lambda _: None
            )(request.generation_id)
            if isinstance(gen_result, GenerationResult):
                for ch in gen_result.changes:
                    review_files[ch.path] = ch.content

        # Check DebuggingService
        if not review_files and request.repair_id and self.debugging_service:
            rep = getattr(self.debugging_service, "get_repair", lambda _: None)(
                request.repair_id
            )
            if rep:
                repair_result = repair_result or rep
                if hasattr(rep, "winning_patch") and rep.winning_patch:
                    diff_files = extract_files_from_unified_diff(
                        rep.winning_patch, repo_path=repo_path
                    )
                    review_files.update(diff_files)

        # Check patch_content
        if not review_files and request.patch_content:
            diff_files = extract_files_from_unified_diff(
                request.patch_content, repo_path=repo_path
            )
            review_files.update(diff_files)

        # If full repository scope or no patch files found, read repository
        if not review_files or request.review_scope == ReviewScope.FULL_REPOSITORY:
            if repo_path.exists() and repo_path.is_dir():
                scanned = self.scan_repository_files(
                    repo_path,
                    ignored_patterns=request.config.ignored_paths,
                )
                if request.review_scope == ReviewScope.FULL_REPOSITORY:
                    review_files = scanned
                elif not review_files:
                    review_files.update(scanned)

        if not review_files:
            # If still empty, return clean report noting no files reviewed
            report = ReviewReport(
                review_id=request.review_id,
                repository_path=str(repo_path),
                reviewed_artifacts=reviewed_artifacts,
                status=ReviewStatus.APPROVED,
                review_scope=request.review_scope,
                configuration=request.config,
                findings=[],
                blockers=[],
                recommendations=["No files identified or provided for review."],
                analysis_metadata={"analyzers_executed": []},
                duration_ms=round((time.perf_counter() - start_time) * 1000, 2),
            )
            self._reviews[request.review_id] = report
            return report

        # Resolve verification report if execution_id is given
        if not verification_report and request.execution_id and self.execution_service:
            verification_report = getattr(
                self.execution_service, "get_report", lambda _: None
            )(request.execution_id)

        all_findings: list[ReviewFinding] = []

        # 2. Static Analysis
        if request.config.enable_static_analysis:
            analyzers_run.append("DeterministicStaticAnalyzer")
            static_findings = self.static_analyzer.analyze_files(
                review_files,
                config=request.config,
            )
            all_findings.extend(static_findings)

        # 3. Security Analysis
        if request.config.enable_security_analysis:
            analyzers_run.append("SecurityAnalyzer")
            security_findings = self.security_analyzer.analyze_files(
                review_files,
                config=request.config,
            )
            all_findings.extend(security_findings)

        # 4. Architecture Analysis
        if request.config.enable_architecture_analysis:
            analyzers_run.append("ArchitectureAnalyzer")
            arch_findings = self.architecture_analyzer.analyze_files(
                review_files,
                config=request.config,
                repo_root=repo_path if repo_path.exists() else None,
            )
            all_findings.extend(arch_findings)

        # 5. Regression Analysis
        if request.config.enable_regression_analysis:
            analyzers_run.append("RegressionAnalyzer")
            regression_findings = self.regression_analyzer.analyze_files(
                review_files,
                plan=plan,
                verification_report=verification_report,
                repair_result=repair_result,
                config=request.config,
            )
            all_findings.extend(regression_findings)

        # 6. Aggregate Findings and Gating
        report = self.aggregator.aggregate(
            findings=all_findings,
            files_reviewed=list(review_files.keys()),
            config=request.config,
            review_id=request.review_id,
            repository_path=str(repo_path),
            review_scope=request.review_scope,
            reviewed_artifacts=reviewed_artifacts,
            start_time=start_time,
            analyzers_run=analyzers_run,
        )

        self._reviews[request.review_id] = report
        return report

    def format_markdown_report(self, report: ReviewReport) -> str:
        """Produce a formatted Markdown summary of the review report."""
        lines: list[str] = [
            "# AgentForge Code Review Report",
            f"**Review ID:** `{report.review_id}`  ",
            f"**Status:** `{report.status.value.upper()}`  ",
            f"**Quality Score:** `{report.summary.quality_score:.1f}/100`  ",
            f"**Scope:** `{report.review_scope.value}`  ",
            f"**Generated:** {report.generated_at}  ",
            f"**Duration:** {report.duration_ms:.1f}ms  ",
            "",
            "## Summary",
            f"- **Total Findings:** {report.summary.total_findings}",
            f"- **Critical:** {report.summary.critical_count} | "
            f"**High:** {report.summary.high_count} | "
            f"**Medium:** {report.summary.medium_count} | "
            f"**Low:** {report.summary.low_count}",
            "- **Requires Human Review:** "
            f"{'Yes' if report.summary.requires_human_review else 'No'}",
            "",
        ]

        if report.blockers:
            lines.extend(["## Blockers", ""])
            for blocker in report.blockers:
                lines.append(f"- ⛔ {blocker}")
            lines.append("")

        if report.findings:
            lines.extend(["## Findings", ""])
            for finding in report.findings:
                sev = finding.severity.value.upper()
                lines.extend(
                    [
                        f"### [{sev}] {finding.rule_id}: {finding.title}",
                        f"- **File:** `{finding.file_path}:{finding.start_line}`",
                        f"- **Category:** `{finding.category.value}`",
                        f"- **Confidence:** `{finding.confidence.value}`",
                        f"- **Description:** {finding.description}",
                    ]
                )
                if finding.evidence and finding.evidence.code_excerpt:
                    lines.extend(
                        [
                            "- **Evidence:**",
                            "```python",
                            finding.evidence.code_excerpt,
                            "```",
                        ]
                    )
                if finding.remediation:
                    lines.append(f"- **Remediation:** {finding.remediation}")
                lines.append("")

        if report.recommendations:
            lines.extend(["## Recommendations", ""])
            for rec in report.recommendations:
                lines.append(f"- 💡 {rec}")
            lines.append("")

        return "\n".join(lines)
