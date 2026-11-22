"""Regression and plan compliance analyzer for code review."""

import ast
import hashlib
import uuid
from typing import Any

from backend.app.debugging.models import RepairResult, RepairState
from backend.app.execution.models import VerificationReport
from backend.app.generation.models import GeneratedFileChange
from backend.app.issue.models import IssueContext
from backend.app.planning.models import ImplementationPlan
from backend.app.review.models import (
    CodeEvidence,
    FindingSeverity,
    ReviewConfiguration,
    ReviewFinding,
)
from backend.app.review.registry import RuleRegistry, get_default_registry


def extract_code_excerpt(
    source_lines: list[str],
    start_line: int,
    end_line: int,
    max_lines: int = 15,
) -> str:
    """Extract and bound source code lines for review evidence."""
    total = len(source_lines)
    s_idx = max(0, start_line - 1)
    e_idx = min(total, end_line)

    if s_idx >= total or s_idx > e_idx:
        return ""

    lines = source_lines[s_idx:e_idx]
    if len(lines) > max_lines:
        lines = lines[:max_lines] + ["# ... [truncated excerpt]"]

    return "\n".join(lines)


def compute_content_hash(text: str) -> str:
    """Calculate SHA-256 hash of reviewed code content."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class AssertionChecker(ast.NodeVisitor):
    """Visitor checking whether a test function contains valid assertions."""

    def __init__(self) -> None:
        self.has_assertions = False
        self.is_skipped = False
        self.skip_reason = ""

    def visit_Assert(self, node: ast.Assert) -> None:
        self.has_assertions = True
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        # Check for pytest.raises or pytest.warns
        if isinstance(node.func, ast.Attribute):
            if (
                isinstance(node.func.value, ast.Name)
                and node.func.value.id == "pytest"
                and node.func.attr in {"raises", "warns"}
            ):
                self.has_assertions = True
            elif node.func.attr.startswith("assert"):
                # unittest-style assertions (self.assertEqual etc.)
                self.has_assertions = True
            elif (
                isinstance(node.func.value, ast.Name)
                and node.func.value.id == "pytest"
                and node.func.attr == "skip"
            ):
                self.is_skipped = True
        elif isinstance(node.func, ast.Name) and node.func.id.startswith("assert"):
            self.has_assertions = True

        self.generic_visit(node)


class RegressionAnalyzer:
    """Analyzer detecting regressions, unauthorized changes, and test defects."""

    def __init__(self, registry: RuleRegistry | None = None) -> None:
        """Initialize analyzer with optional rule registry."""
        self.registry = registry if registry is not None else get_default_registry()

    def analyze_plan_compliance(
        self,
        plan: ImplementationPlan,
        generated_changes: list[GeneratedFileChange] | list[str],
        config: ReviewConfiguration | None = None,
    ) -> list[ReviewFinding]:
        """Verify generated file changes adhere to authorized plan scope."""
        active_config = config or ReviewConfiguration()
        findings: list[ReviewFinding] = []

        # Collect all authorized files from implementation plan
        authorized_files: set[str] = set()
        planned_mandatory_files: set[str] = set()

        for fc in plan.files_to_modify + plan.files_to_create:
            clean = fc.path.replace("\\", "/").lstrip("/")
            authorized_files.add(clean)
            planned_mandatory_files.add(clean)

        for fc in plan.files_to_delete:
            clean = fc.path.replace("\\", "/").lstrip("/")
            authorized_files.add(clean)

        for task in plan.tasks:
            for tf in task.target_files:
                authorized_files.add(tf.replace("\\", "/").lstrip("/"))

        for tf in (
            plan.test_plan.tests_to_modify
            + plan.test_plan.tests_to_add
            + plan.test_plan.existing_tests
        ):
            authorized_files.add(tf.replace("\\", "/").lstrip("/"))

        # Normalize changed file paths
        changed_paths: set[str] = set()
        for item in generated_changes:
            if isinstance(item, GeneratedFileChange):
                changed_paths.add(item.path.replace("\\", "/").lstrip("/"))
            elif isinstance(item, str):
                changed_paths.add(item.replace("\\", "/").lstrip("/"))

        # Rule REG-001: Unauthorized File Modification
        reg_001 = self.registry.get_rule("REG-001")
        if reg_001 and reg_001.enabled:
            for path in changed_paths:
                if path not in authorized_files:
                    evidence = CodeEvidence(
                        evidence_id=f"ev-{uuid.uuid4().hex[:8]}",
                        file_path=path,
                        start_line=1,
                        end_line=1,
                        code_excerpt=f"# File modified outside plan: {path}",
                        related_rule=reg_001.rule_id,
                        source_artifact=plan.id,
                        content_hash=compute_content_hash(path),
                    )
                    findings.append(
                        ReviewFinding(
                            finding_id=f"find-{uuid.uuid4().hex[:8]}",
                            rule_id=reg_001.rule_id,
                            title=reg_001.title,
                            description=(
                                f"Unauthorized file modification: '{path}' was "
                                f"changed but not authorized in plan '{plan.id}'."
                            ),
                            category=reg_001.category,
                            severity=reg_001.severity,
                            confidence=reg_001.confidence,
                            file_path=path,
                            start_line=1,
                            end_line=1,
                            evidence=evidence,
                            impact=(
                                "Scope creep can introduce regressions and "
                                "unvetted changes to production code."
                            ),
                            remediation=reg_001.remediation_guidance,
                            source="regression_analysis",
                            requires_human_review=reg_001.requires_human_review,
                        )
                    )

        # Rule REG-002: Missing Planned Target Implementation
        reg_002 = self.registry.get_rule("REG-002")
        if reg_002 and reg_002.enabled:
            for planned_file in planned_mandatory_files:
                if planned_file not in changed_paths:
                    evidence = CodeEvidence(
                        evidence_id=f"ev-{uuid.uuid4().hex[:8]}",
                        file_path=planned_file,
                        start_line=1,
                        end_line=1,
                        code_excerpt=(f"# Omitted planned target file: {planned_file}"),
                        related_rule=reg_002.rule_id,
                        source_artifact=plan.id,
                        content_hash=compute_content_hash(planned_file),
                    )
                    findings.append(
                        ReviewFinding(
                            finding_id=f"find-{uuid.uuid4().hex[:8]}",
                            rule_id=reg_002.rule_id,
                            title=reg_002.title,
                            description=(
                                f"Missing planned target implementation: "
                                f"'{planned_file}' was planned in '{plan.id}' "
                                "but omitted from generation."
                            ),
                            category=reg_002.category,
                            severity=reg_002.severity,
                            confidence=reg_002.confidence,
                            file_path=planned_file,
                            start_line=1,
                            end_line=1,
                            evidence=evidence,
                            impact=(
                                "Omission of planned target changes leaves the "
                                "feature or bugfix incomplete."
                            ),
                            remediation=reg_002.remediation_guidance,
                            source="regression_analysis",
                            requires_human_review=reg_002.requires_human_review,
                        )
                    )

        return self._filter_findings(findings, active_config)

    def analyze_verification_compliance(
        self,
        report: VerificationReport,
        config: ReviewConfiguration | None = None,
    ) -> list[ReviewFinding]:
        """Check verification report for test failures and execution defects."""
        active_config = config or ReviewConfiguration()
        findings: list[ReviewFinding] = []

        reg_003 = self.registry.get_rule("REG-003")
        if not reg_003 or not reg_003.enabled:
            return []

        has_failure = not report.verification_passed or bool(report.failures)
        if (
            report.execution_result
            and report.execution_result.test_summary
            and not report.execution_result.test_summary.is_all_passed
        ):
            has_failure = True

        if has_failure:
            reasons = (
                "; ".join(report.failures)
                if report.failures
                else ("One or more tests failed during sandbox verification.")
            )
            evidence = CodeEvidence(
                evidence_id=f"ev-{uuid.uuid4().hex[:8]}",
                file_path="tests",
                start_line=1,
                end_line=1,
                code_excerpt=reasons[:200],
                related_rule=reg_003.rule_id,
                source_artifact=report.execution_id,
                content_hash=compute_content_hash(reasons),
            )
            findings.append(
                ReviewFinding(
                    finding_id=f"find-{uuid.uuid4().hex[:8]}",
                    rule_id=reg_003.rule_id,
                    title=reg_003.title,
                    description=(f"Unresolved test execution failure: {reasons}"),
                    category=reg_003.category,
                    severity=reg_003.severity,
                    confidence=reg_003.confidence,
                    file_path="tests",
                    start_line=1,
                    end_line=1,
                    evidence=evidence,
                    impact=(
                        "Proceeding with failing verification tests causes "
                        "regressions and breaks build stability."
                    ),
                    remediation=reg_003.remediation_guidance,
                    source="regression_analysis",
                    requires_human_review=reg_003.requires_human_review,
                )
            )

        return self._filter_findings(findings, active_config)

    def analyze_repair_compliance(
        self,
        repair_result: RepairResult,
        config: ReviewConfiguration | None = None,
    ) -> list[ReviewFinding]:
        """Analyze automated repair outcome for persistent regressions."""
        active_config = config or ReviewConfiguration()
        findings: list[ReviewFinding] = []

        reg_004 = self.registry.get_rule("REG-004")
        if not reg_004 or not reg_004.enabled:
            return []

        if repair_result.current_state in {
            RepairState.FAILED,
            RepairState.CANCELLED,
        }:
            reason = repair_result.error_message or (
                f"Repair session '{repair_result.repair_id}' terminated in "
                f"state {repair_result.current_state.value}."
            )
            evidence = CodeEvidence(
                evidence_id=f"ev-{uuid.uuid4().hex[:8]}",
                file_path="tests",
                start_line=1,
                end_line=1,
                code_excerpt=reason[:200],
                related_rule=reg_004.rule_id,
                source_artifact=repair_result.repair_id,
                content_hash=compute_content_hash(reason),
            )
            findings.append(
                ReviewFinding(
                    finding_id=f"find-{uuid.uuid4().hex[:8]}",
                    rule_id=reg_004.rule_id,
                    title=reg_004.title,
                    description=f"Repair failed: {reason}",
                    category=reg_004.category,
                    severity=reg_004.severity,
                    confidence=reg_004.confidence,
                    file_path="tests",
                    start_line=1,
                    end_line=1,
                    evidence=evidence,
                    impact=(
                        "Unsuccessful repair attempts indicate persistent "
                        "regressions requiring human diagnosis."
                    ),
                    remediation=reg_004.remediation_guidance,
                    source="regression_analysis",
                    requires_human_review=reg_004.requires_human_review,
                )
            )

        return self._filter_findings(findings, active_config)

    def analyze_issue_compliance(
        self,
        issue_context: IssueContext,
        plan: ImplementationPlan,
        config: ReviewConfiguration | None = None,
    ) -> list[ReviewFinding]:
        """Verify that issue acceptance criteria are addressed by plan."""
        active_config = config or ReviewConfiguration()
        findings: list[ReviewFinding] = []

        reg_002 = self.registry.get_rule("REG-002")
        if not reg_002 or not reg_002.enabled:
            return []

        addressed_criteria: set[str] = set()
        for task in plan.tasks:
            addressed_criteria.update(task.addressed_acceptance_criteria)
        for vc in plan.test_plan.verification_cases:
            if vc.acceptance_criterion_id:
                addressed_criteria.add(vc.acceptance_criterion_id)

        # Check explicit acceptance criteria from issue
        for ac in issue_context.analysis.acceptance_criteria:
            if ac.id not in addressed_criteria:
                evidence = CodeEvidence(
                    evidence_id=f"ev-{uuid.uuid4().hex[:8]}",
                    file_path="plan.md",
                    start_line=1,
                    end_line=1,
                    code_excerpt=f"Unaddressed criterion: {ac.description}",
                    related_rule=reg_002.rule_id,
                    source_artifact=issue_context.analysis.issue.id,
                    content_hash=compute_content_hash(ac.description),
                )
                findings.append(
                    ReviewFinding(
                        finding_id=f"find-{uuid.uuid4().hex[:8]}",
                        rule_id=reg_002.rule_id,
                        title="Unmet Acceptance Criterion",
                        description=(
                            f"Acceptance criterion '{ac.id}' ('{ac.description}') "
                            f"is not addressed in implementation plan '{plan.id}'."
                        ),
                        category=reg_002.category,
                        severity=reg_002.severity,
                        confidence=reg_002.confidence,
                        file_path="plan.md",
                        start_line=1,
                        end_line=1,
                        evidence=evidence,
                        impact=(
                            "Failing to implement required acceptance criteria "
                            "will cause acceptance test failures."
                        ),
                        remediation=reg_002.remediation_guidance,
                        source="regression_analysis",
                        requires_human_review=reg_002.requires_human_review,
                    )
                )

        return self._filter_findings(findings, active_config)

    def analyze_test_quality(
        self,
        test_files: dict[str, str],
        config: ReviewConfiguration | None = None,
    ) -> list[ReviewFinding]:
        """Examine test code quality for empty tests and disabled assertions."""
        active_config = config or ReviewConfiguration()
        findings: list[ReviewFinding] = []

        rule_empty = self.registry.get_rule("TEST-001")
        rule_skipped = self.registry.get_rule("TEST-002")

        for file_path, code in test_files.items():
            clean_path = file_path.replace("\\", "/").lstrip("/")
            try:
                tree = ast.parse(code, filename=clean_path)
            except SyntaxError:
                continue

            source_lines = code.splitlines()

            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    name = node.name
                    if not (name.startswith("test_") or name.endswith("_test")):
                        continue

                    # Check for skipped decorator
                    is_skipped_decorator = False
                    for dec in node.decorator_list:
                        dec_str = ""
                        if isinstance(dec, ast.Attribute):
                            dec_str = dec.attr
                        elif isinstance(dec, ast.Call):
                            if isinstance(dec.func, ast.Attribute):
                                dec_str = dec.func.attr
                            elif isinstance(dec.func, ast.Name):
                                dec_str = dec.func.id

                        if dec_str in {"skip", "skipif"}:
                            is_skipped_decorator = True

                    checker = AssertionChecker()
                    checker.visit(node)

                    # TEST-002: Skipped test
                    if (
                        (is_skipped_decorator or checker.is_skipped)
                        and rule_skipped
                        and rule_skipped.enabled
                    ):
                        start_l = node.lineno
                        end_l = getattr(node, "end_lineno", start_l) or start_l
                        excerpt = extract_code_excerpt(
                            source_lines,
                            start_l,
                            end_l,
                            active_config.max_excerpt_lines,
                        )
                        evidence = CodeEvidence(
                            evidence_id=f"ev-{uuid.uuid4().hex[:8]}",
                            file_path=clean_path,
                            start_line=start_l,
                            end_line=end_l,
                            code_excerpt=excerpt,
                            related_rule=rule_skipped.rule_id,
                            content_hash=compute_content_hash(excerpt),
                        )
                        findings.append(
                            ReviewFinding(
                                finding_id=f"find-{uuid.uuid4().hex[:8]}",
                                rule_id=rule_skipped.rule_id,
                                title=rule_skipped.title,
                                description=(
                                    f"Test '{name}' in '{clean_path}' is "
                                    "disabled or skipped."
                                ),
                                category=rule_skipped.category,
                                severity=rule_skipped.severity,
                                confidence=rule_skipped.confidence,
                                file_path=clean_path,
                                start_line=start_l,
                                end_line=end_l,
                                affected_symbol=name,
                                evidence=evidence,
                                impact=(
                                    "Skipping tests lowers coverage and can "
                                    "hide regressions."
                                ),
                                remediation=rule_skipped.remediation_guidance,
                                source="testing_analysis",
                                requires_human_review=False,
                            )
                        )

                    # TEST-001: Assert-less or empty test function
                    if (
                        not checker.has_assertions
                        and not is_skipped_decorator
                        and not checker.is_skipped
                        and rule_empty
                        and rule_empty.enabled
                    ):
                        start_l = node.lineno
                        end_l = getattr(node, "end_lineno", start_l) or start_l
                        excerpt = extract_code_excerpt(
                            source_lines,
                            start_l,
                            end_l,
                            active_config.max_excerpt_lines,
                        )
                        evidence = CodeEvidence(
                            evidence_id=f"ev-{uuid.uuid4().hex[:8]}",
                            file_path=clean_path,
                            start_line=start_l,
                            end_line=end_l,
                            code_excerpt=excerpt,
                            related_rule=rule_empty.rule_id,
                            content_hash=compute_content_hash(excerpt),
                        )
                        findings.append(
                            ReviewFinding(
                                finding_id=f"find-{uuid.uuid4().hex[:8]}",
                                rule_id=rule_empty.rule_id,
                                title=rule_empty.title,
                                description=(
                                    f"Test function '{name}' in '{clean_path}' "
                                    "contains no assertions or expectations."
                                ),
                                category=rule_empty.category,
                                severity=rule_empty.severity,
                                confidence=rule_empty.confidence,
                                file_path=clean_path,
                                start_line=start_l,
                                end_line=end_l,
                                affected_symbol=name,
                                evidence=evidence,
                                impact=(
                                    "Tests without assertions provide false "
                                    "confidence without verifying behavior."
                                ),
                                remediation=rule_empty.remediation_guidance,
                                source="testing_analysis",
                                requires_human_review=False,
                            )
                        )

        return self._filter_findings(findings, active_config)

    def analyze_files(
        self,
        files: dict[str, str],
        plan: ImplementationPlan | None = None,
        verification_report: VerificationReport | None = None,
        repair_result: Any | None = None,
        config: ReviewConfiguration | None = None,
    ) -> list[ReviewFinding]:
        """Synthesize regression and quality checks across changed files."""
        findings: list[ReviewFinding] = []
        test_files = {p: c for p, c in files.items() if "test" in p.lower()}
        if test_files:
            findings.extend(self.analyze_test_quality(test_files, config=config))
        if plan:
            findings.extend(
                self.analyze_plan_compliance(plan, list(files.keys()), config=config)
            )
        if verification_report:
            findings.extend(
                self.analyze_verification_compliance(verification_report, config=config)
            )
        if repair_result:
            findings.extend(
                self.analyze_repair_compliance(repair_result, config=config)
            )
        return findings

    def _filter_findings(
        self,
        findings: list[ReviewFinding],
        config: ReviewConfiguration,
    ) -> list[ReviewFinding]:
        """Apply active configuration filters for disabled rules and severity."""
        severity_rank = {
            FindingSeverity.LOW: 1,
            FindingSeverity.MEDIUM: 2,
            FindingSeverity.HIGH: 3,
            FindingSeverity.CRITICAL: 4,
        }
        min_rank = severity_rank.get(config.min_severity, 1)

        filtered: list[ReviewFinding] = []
        for finding in findings:
            if finding.category not in config.enabled_categories:
                continue
            if finding.rule_id in config.disabled_rule_ids:
                continue
            if severity_rank.get(finding.severity, 1) < min_rank:
                continue
            filtered.append(finding)

        return filtered
