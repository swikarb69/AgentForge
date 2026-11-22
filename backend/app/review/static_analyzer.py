"""Deterministic static analysis engine evaluating AST patterns for code review."""

import ast
import hashlib
import uuid
from pathlib import Path

from backend.app.review.models import (
    CodeEvidence,
    FindingCategory,
    FindingConfidence,
    FindingSeverity,
    ReviewConfiguration,
    ReviewFinding,
)
from backend.app.review.registry import RuleRegistry, get_default_registry
from backend.app.review.rules import ReviewRule


def extract_code_excerpt(
    source_lines: list[str],
    start_line: int,
    end_line: int,
    max_lines: int = 10,
) -> str:
    """Extract bounded code snippet from source lines (1-indexed)."""
    if not source_lines:
        return ""
    total = len(source_lines)
    s = max(1, min(start_line, total))
    e = max(s, min(end_line, total))

    if e - s + 1 > max_lines:
        e = s + max_lines - 1

    selected = source_lines[s - 1 : e]
    return "\n".join(selected)


def compute_content_hash(text: str) -> str:
    """Compute SHA-256 digest of code snippet."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ComplexityVisitor(ast.NodeVisitor):
    """Calculates McCabe cyclomatic complexity of an AST node."""

    def __init__(self) -> None:
        self.complexity: int = 1

    def visit_If(self, node: ast.If) -> None:
        self.complexity += 1
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        self.complexity += 1
        self.generic_visit(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self.complexity += 1
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:
        self.complexity += 1
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        self.complexity += 1
        self.generic_visit(node)

    def visit_With(self, node: ast.With) -> None:
        self.complexity += 1
        self.generic_visit(node)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self.complexity += 1
        self.generic_visit(node)

    def visit_Assert(self, node: ast.Assert) -> None:
        self.complexity += 1
        self.generic_visit(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        self.complexity += len(node.values) - 1
        self.generic_visit(node)


class DeterministicStaticAnalyzer:
    """Inspects Python source code and AST structures for defects and quality issues."""

    def __init__(self, registry: RuleRegistry | None = None) -> None:
        self.registry = registry or get_default_registry()

    def analyze_source(
        self,
        file_path: str,
        source_code: str,
        config: ReviewConfiguration | None = None,
    ) -> list[ReviewFinding]:
        """Analyze a single Python source code string."""
        cfg = config or ReviewConfiguration()
        normalized_path = file_path.replace("\\", "/").lstrip("/")

        # Check file size limit
        if len(source_code.encode("utf-8")) > cfg.max_file_size_bytes:
            return []

        source_lines = source_code.splitlines()
        findings: list[ReviewFinding] = []

        try:
            tree = ast.parse(source_code, filename=normalized_path)
        except SyntaxError as err:
            err_line = err.lineno or 1
            corr_rule = self.registry.get_rule("CORR-001")
            excerpt = (
                source_lines[err_line - 1]
                if 0 < err_line <= len(source_lines)
                else str(err.text or "")
            )
            evidence = CodeEvidence(
                evidence_id=f"ev-{uuid.uuid4().hex[:8]}",
                file_path=normalized_path,
                start_line=err_line,
                end_line=err_line,
                code_excerpt=excerpt or f"SyntaxError: {err.msg}",
                related_rule="CORR-001",
            )
            findings.append(
                ReviewFinding(
                    finding_id=f"find-{uuid.uuid4().hex[:8]}",
                    rule_id="CORR-001",
                    title="Syntax Error In Source",
                    description=f"File contains a syntax error: {err.msg}",
                    category=FindingCategory.CORRECTNESS,
                    severity=FindingSeverity.HIGH,
                    confidence=FindingConfidence.HIGH,
                    file_path=normalized_path,
                    start_line=err_line,
                    end_line=err_line,
                    evidence=evidence,
                    impact="File will fail to import or execute at runtime.",
                    remediation="Correct the syntax error.",
                    source="static_analysis",
                    requires_human_review=(
                        corr_rule.requires_human_review if corr_rule else False
                    ),
                )
            )
            return self._filter_findings(findings, cfg)

        # Run AST checks
        self._check_unreachable_code(tree, normalized_path, source_lines, findings, cfg)
        self._check_functions_and_classes(
            tree, normalized_path, source_lines, findings, cfg
        )
        self._check_comparisons(tree, normalized_path, source_lines, findings, cfg)
        self._check_exception_handling(
            tree, normalized_path, source_lines, findings, cfg
        )
        self._check_resource_cleanup(tree, normalized_path, source_lines, findings, cfg)
        self._check_performance_patterns(
            tree, normalized_path, source_lines, findings, cfg
        )

        return self._filter_findings(findings, cfg)

    def analyze_files(
        self,
        files: dict[str, str],
        config: ReviewConfiguration | None = None,
    ) -> list[ReviewFinding]:
        """Analyze a collection of files keyed by relative file path."""
        cfg = config or ReviewConfiguration()
        all_findings: list[ReviewFinding] = []
        for path, content in files.items():
            if not path.endswith(".py"):
                continue
            findings = self.analyze_source(path, content, cfg)
            all_findings.extend(findings)
        return all_findings

    def analyze_repository_path(
        self,
        repo_path: str,
        target_files: list[str] | None = None,
        config: ReviewConfiguration | None = None,
    ) -> list[ReviewFinding]:
        """Read and analyze target files from disk within repository."""
        cfg = config or ReviewConfiguration()
        root = Path(repo_path)
        all_findings: list[ReviewFinding] = []

        files_to_check = target_files
        if files_to_check is None:
            files_to_check = [
                str(p.relative_to(root)).replace("\\", "/")
                for p in root.rglob("*.py")
                if not any(part.startswith(".") for part in p.parts)
            ]

        for rel_path in files_to_check:
            full_path = root / rel_path
            if full_path.is_file() and rel_path.endswith(".py"):
                try:
                    content = full_path.read_text(encoding="utf-8", errors="replace")
                    findings = self.analyze_source(rel_path, content, cfg)
                    all_findings.extend(findings)
                except (OSError, UnicodeDecodeError):
                    continue
        return all_findings

    # --- Rule Detectors ---

    def _check_unreachable_code(
        self,
        tree: ast.AST,
        file_path: str,
        source_lines: list[str],
        findings: list[ReviewFinding],
        config: ReviewConfiguration,
    ) -> None:
        """CORR-002: Detect unreachable statements following terminal statements."""
        rule = self.registry.get_rule("CORR-002")
        if not rule or not rule.enabled:
            return

        for node in ast.walk(tree):
            for _field, val in ast.iter_fields(node):
                if isinstance(val, list) and val and isinstance(val[0], ast.stmt):
                    self._scan_block_for_unreachable(
                        val, file_path, source_lines, findings, rule, config
                    )

    def _scan_block_for_unreachable(
        self,
        block: list[ast.stmt],
        file_path: str,
        source_lines: list[str],
        findings: list[ReviewFinding],
        rule: ReviewRule,
        config: ReviewConfiguration,
    ) -> None:
        terminal_seen = False
        unreachable_start: ast.stmt | None = None

        for stmt in block:
            if terminal_seen:
                if unreachable_start is None:
                    unreachable_start = stmt
            elif isinstance(stmt, (ast.Return, ast.Raise, ast.Break, ast.Continue)):
                terminal_seen = True

        if unreachable_start is not None:
            start_l = unreachable_start.lineno
            end_l = getattr(block[-1], "end_lineno", block[-1].lineno) or start_l
            excerpt = extract_code_excerpt(
                source_lines, start_l, end_l, config.max_excerpt_lines
            )
            evidence = CodeEvidence(
                evidence_id=f"ev-{uuid.uuid4().hex[:8]}",
                file_path=file_path,
                start_line=start_l,
                end_line=end_l,
                code_excerpt=excerpt,
                related_rule=rule.rule_id,
                content_hash=compute_content_hash(excerpt),
            )
            findings.append(
                ReviewFinding(
                    finding_id=f"find-{uuid.uuid4().hex[:8]}",
                    rule_id=rule.rule_id,
                    title=rule.title,
                    description=(
                        "Unreachable code detected: statements placed after "
                        "return, raise, break, or continue will never execute."
                    ),
                    category=rule.category,
                    severity=rule.severity,
                    confidence=rule.confidence,
                    file_path=file_path,
                    start_line=start_l,
                    end_line=end_l,
                    evidence=evidence,
                    impact="Code will never execute, leading to dead code.",
                    remediation=rule.remediation_guidance,
                    source="static_analysis",
                    requires_human_review=rule.requires_human_review,
                )
            )

    def _check_functions_and_classes(
        self,
        tree: ast.AST,
        file_path: str,
        source_lines: list[str],
        findings: list[ReviewFinding],
        config: ReviewConfiguration,
    ) -> None:
        """Inspect function and class definitions."""
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._check_mutable_defaults(
                    node, file_path, source_lines, findings, config
                )
                self._check_function_length(
                    node, file_path, source_lines, findings, config
                )
                self._check_function_parameters(
                    node, file_path, source_lines, findings, config
                )
                self._check_cyclomatic_complexity(
                    node, file_path, source_lines, findings, config
                )
                self._check_nesting_depth(
                    node, file_path, source_lines, findings, config
                )
                self._check_global_state(
                    node, file_path, source_lines, findings, config
                )
                self._check_docstring(
                    node, file_path, source_lines, findings, config, is_class=False
                )
            elif isinstance(node, ast.ClassDef):
                self._check_docstring(
                    node, file_path, source_lines, findings, config, is_class=True
                )

    def _check_mutable_defaults(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        file_path: str,
        source_lines: list[str],
        findings: list[ReviewFinding],
        config: ReviewConfiguration,
    ) -> None:
        """CORR-003: Check for mutable default arguments."""
        rule = self.registry.get_rule("CORR-003")
        if not rule or not rule.enabled:
            return

        all_defaults = list(node.args.defaults) + [
            d for d in node.args.kw_defaults if d is not None
        ]
        for d in all_defaults:
            if isinstance(d, (ast.List, ast.Dict, ast.Set)):
                start_l = d.lineno
                end_l = getattr(d, "end_lineno", start_l) or start_l
                excerpt = extract_code_excerpt(
                    source_lines, start_l, end_l, config.max_excerpt_lines
                )
                evidence = CodeEvidence(
                    evidence_id=f"ev-{uuid.uuid4().hex[:8]}",
                    file_path=file_path,
                    start_line=start_l,
                    end_line=end_l,
                    code_excerpt=excerpt,
                    related_rule=rule.rule_id,
                    content_hash=compute_content_hash(excerpt),
                )
                findings.append(
                    ReviewFinding(
                        finding_id=f"find-{uuid.uuid4().hex[:8]}",
                        rule_id=rule.rule_id,
                        title=rule.title,
                        description=(
                            f"Function '{node.name}' defines a "
                            "mutable default argument."
                        ),
                        category=rule.category,
                        severity=rule.severity,
                        confidence=rule.confidence,
                        file_path=file_path,
                        start_line=start_l,
                        end_line=end_l,
                        affected_symbol=node.name,
                        evidence=evidence,
                        impact="Mutable default retains mutations across invocations.",
                        remediation=rule.remediation_guidance,
                        source="static_analysis",
                        requires_human_review=rule.requires_human_review,
                    )
                )

    def _check_function_length(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        file_path: str,
        source_lines: list[str],
        findings: list[ReviewFinding],
        config: ReviewConfiguration,
    ) -> None:
        """MAINT-001: Excessively long function (> 50 lines)."""
        rule = self.registry.get_rule("MAINT-001")
        if not rule or not rule.enabled:
            return

        end_l = getattr(node, "end_lineno", node.lineno) or node.lineno
        length = end_l - node.lineno + 1
        if length > 50:
            excerpt = extract_code_excerpt(
                source_lines, node.lineno, node.lineno + 3, config.max_excerpt_lines
            )
            evidence = CodeEvidence(
                evidence_id=f"ev-{uuid.uuid4().hex[:8]}",
                file_path=file_path,
                start_line=node.lineno,
                end_line=end_l,
                code_excerpt=excerpt,
                related_rule=rule.rule_id,
                content_hash=compute_content_hash(excerpt),
            )
            findings.append(
                ReviewFinding(
                    finding_id=f"find-{uuid.uuid4().hex[:8]}",
                    rule_id=rule.rule_id,
                    title=rule.title,
                    description=f"Function '{node.name}' has {length} lines (> 50).",
                    category=rule.category,
                    severity=rule.severity,
                    confidence=rule.confidence,
                    file_path=file_path,
                    start_line=node.lineno,
                    end_line=end_l,
                    affected_symbol=node.name,
                    evidence=evidence,
                    impact="Excessive function length impairs readability.",
                    remediation=rule.remediation_guidance,
                    source="static_analysis",
                    requires_human_review=rule.requires_human_review,
                )
            )

    def _check_function_parameters(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        file_path: str,
        source_lines: list[str],
        findings: list[ReviewFinding],
        config: ReviewConfiguration,
    ) -> None:
        """MAINT-002: Excessive function parameters (> 6 parameters)."""
        rule = self.registry.get_rule("MAINT-002")
        if not rule or not rule.enabled:
            return

        total_args = (
            len(node.args.posonlyargs)
            + len(node.args.args)
            + len(node.args.kwonlyargs)
            + (1 if node.args.vararg else 0)
            + (1 if node.args.kwarg else 0)
        )
        if node.args.args and node.args.args[0].arg in ("self", "cls"):
            total_args -= 1

        if total_args > 6:
            excerpt = extract_code_excerpt(
                source_lines, node.lineno, node.lineno, config.max_excerpt_lines
            )
            evidence = CodeEvidence(
                evidence_id=f"ev-{uuid.uuid4().hex[:8]}",
                file_path=file_path,
                start_line=node.lineno,
                end_line=node.lineno,
                code_excerpt=excerpt,
                related_rule=rule.rule_id,
                content_hash=compute_content_hash(excerpt),
            )
            findings.append(
                ReviewFinding(
                    finding_id=f"find-{uuid.uuid4().hex[:8]}",
                    rule_id=rule.rule_id,
                    title=rule.title,
                    description=(
                        f"Function '{node.name}' accepts {total_args} params (> 6)."
                    ),
                    category=rule.category,
                    severity=rule.severity,
                    confidence=rule.confidence,
                    file_path=file_path,
                    start_line=node.lineno,
                    end_line=node.lineno,
                    affected_symbol=node.name,
                    evidence=evidence,
                    impact="High parameter count suggests tight coupling.",
                    remediation=rule.remediation_guidance,
                    source="static_analysis",
                    requires_human_review=rule.requires_human_review,
                )
            )

    def _check_cyclomatic_complexity(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        file_path: str,
        source_lines: list[str],
        findings: list[ReviewFinding],
        config: ReviewConfiguration,
    ) -> None:
        """MAINT-004: High cyclomatic complexity (> 10)."""
        rule = self.registry.get_rule("MAINT-004")
        if not rule or not rule.enabled:
            return

        visitor = ComplexityVisitor()
        for stmt in node.body:
            visitor.visit(stmt)

        if visitor.complexity > 10:
            excerpt = extract_code_excerpt(
                source_lines, node.lineno, node.lineno, config.max_excerpt_lines
            )
            evidence = CodeEvidence(
                evidence_id=f"ev-{uuid.uuid4().hex[:8]}",
                file_path=file_path,
                start_line=node.lineno,
                end_line=getattr(node, "end_lineno", node.lineno) or node.lineno,
                code_excerpt=excerpt,
                related_rule=rule.rule_id,
                content_hash=compute_content_hash(excerpt),
            )
            findings.append(
                ReviewFinding(
                    finding_id=f"find-{uuid.uuid4().hex[:8]}",
                    rule_id=rule.rule_id,
                    title=rule.title,
                    description=(
                        f"Function '{node.name}' has cyclomatic complexity of "
                        f"{visitor.complexity} (> 10)."
                    ),
                    category=rule.category,
                    severity=rule.severity,
                    confidence=rule.confidence,
                    file_path=file_path,
                    start_line=node.lineno,
                    end_line=getattr(node, "end_lineno", node.lineno) or node.lineno,
                    affected_symbol=node.name,
                    evidence=evidence,
                    impact="High branching complexity makes verification error-prone.",
                    remediation=rule.remediation_guidance,
                    source="static_analysis",
                    requires_human_review=rule.requires_human_review,
                )
            )

    def _check_nesting_depth(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        file_path: str,
        source_lines: list[str],
        findings: list[ReviewFinding],
        config: ReviewConfiguration,
    ) -> None:
        """MAINT-003: Deeply nested control flow (> 4 levels)."""
        rule = self.registry.get_rule("MAINT-003")
        if not rule or not rule.enabled:
            return

        def walk_depth(current: ast.AST, depth: int) -> None:
            is_control = isinstance(
                current,
                (
                    ast.If,
                    ast.For,
                    ast.AsyncFor,
                    ast.While,
                    ast.Try,
                    ast.With,
                    ast.AsyncWith,
                ),
            )
            next_depth = depth + 1 if is_control else depth
            if is_control and depth > 4:
                line_no = getattr(current, "lineno", node.lineno)
                end_line_no = getattr(current, "end_lineno", line_no) or line_no
                excerpt = extract_code_excerpt(
                    source_lines, line_no, end_line_no, config.max_excerpt_lines
                )
                evidence = CodeEvidence(
                    evidence_id=f"ev-{uuid.uuid4().hex[:8]}",
                    file_path=file_path,
                    start_line=line_no,
                    end_line=end_line_no,
                    code_excerpt=excerpt,
                    related_rule=rule.rule_id,
                    content_hash=compute_content_hash(excerpt),
                )
                findings.append(
                    ReviewFinding(
                        finding_id=f"find-{uuid.uuid4().hex[:8]}",
                        rule_id=rule.rule_id,
                        title=rule.title,
                        description=(
                            f"Control flow inside '{node.name}' is nested {depth} "
                            "levels deep (> 4)."
                        ),
                        category=rule.category,
                        severity=rule.severity,
                        confidence=rule.confidence,
                        file_path=file_path,
                        start_line=line_no,
                        end_line=end_line_no,
                        affected_symbol=node.name,
                        evidence=evidence,
                        impact="Deep nesting impairs readability.",
                        remediation=rule.remediation_guidance,
                        source="static_analysis",
                        requires_human_review=rule.requires_human_review,
                    )
                )
                return

            for child in ast.iter_child_nodes(current):
                walk_depth(child, next_depth)

        for stmt in node.body:
            walk_depth(stmt, 1)

    def _check_global_state(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        file_path: str,
        source_lines: list[str],
        findings: list[ReviewFinding],
        config: ReviewConfiguration,
    ) -> None:
        """REL-004: Global mutable state modification inside function."""
        rule = self.registry.get_rule("REL-004")
        if not rule or not rule.enabled:
            return

        for child in ast.walk(node):
            if isinstance(child, ast.Global):
                excerpt = extract_code_excerpt(
                    source_lines, child.lineno, child.lineno, config.max_excerpt_lines
                )
                evidence = CodeEvidence(
                    evidence_id=f"ev-{uuid.uuid4().hex[:8]}",
                    file_path=file_path,
                    start_line=child.lineno,
                    end_line=child.lineno,
                    code_excerpt=excerpt,
                    related_rule=rule.rule_id,
                    content_hash=compute_content_hash(excerpt),
                )
                findings.append(
                    ReviewFinding(
                        finding_id=f"find-{uuid.uuid4().hex[:8]}",
                        rule_id=rule.rule_id,
                        title=rule.title,
                        description=(
                            f"Function '{node.name}' accesses global state with "
                            f"'global {', '.join(child.names)}'."
                        ),
                        category=rule.category,
                        severity=rule.severity,
                        confidence=rule.confidence,
                        file_path=file_path,
                        start_line=child.lineno,
                        end_line=child.lineno,
                        affected_symbol=node.name,
                        evidence=evidence,
                        impact="Global state causes side-effects and race hazards.",
                        remediation=rule.remediation_guidance,
                        source="static_analysis",
                        requires_human_review=rule.requires_human_review,
                    )
                )

    def _check_docstring(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
        file_path: str,
        source_lines: list[str],
        findings: list[ReviewFinding],
        config: ReviewConfiguration,
        is_class: bool,
    ) -> None:
        """MAINT-005: Missing docstring on public function or class."""
        rule = self.registry.get_rule("MAINT-005")
        if not rule or not rule.enabled:
            return

        if node.name.startswith("_") and not (
            node.name.startswith("__") and node.name.endswith("__")
        ):
            return

        has_doc = (
            node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        )
        if not has_doc:
            entity_type = "Class" if is_class else "Function"
            excerpt = extract_code_excerpt(
                source_lines, node.lineno, node.lineno, config.max_excerpt_lines
            )
            evidence = CodeEvidence(
                evidence_id=f"ev-{uuid.uuid4().hex[:8]}",
                file_path=file_path,
                start_line=node.lineno,
                end_line=node.lineno,
                code_excerpt=excerpt,
                related_rule=rule.rule_id,
                content_hash=compute_content_hash(excerpt),
            )
            findings.append(
                ReviewFinding(
                    finding_id=f"find-{uuid.uuid4().hex[:8]}",
                    rule_id=rule.rule_id,
                    title=rule.title,
                    description=(
                        f"Public {entity_type} '{node.name}' lacks a docstring."
                    ),
                    category=rule.category,
                    severity=rule.severity,
                    confidence=rule.confidence,
                    file_path=file_path,
                    start_line=node.lineno,
                    end_line=node.lineno,
                    affected_symbol=node.name,
                    evidence=evidence,
                    impact="Absence of API documentation hinders maintainability.",
                    remediation=rule.remediation_guidance,
                    source="static_analysis",
                    requires_human_review=rule.requires_human_review,
                )
            )

    def _check_comparisons(
        self,
        tree: ast.AST,
        file_path: str,
        source_lines: list[str],
        findings: list[ReviewFinding],
        config: ReviewConfiguration,
    ) -> None:
        """CORR-004: Suspicious identity comparison (is/is not with literals)."""
        rule = self.registry.get_rule("CORR-004")
        if not rule or not rule.enabled:
            return

        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                for op, comp in zip(node.ops, node.comparators, strict=False):
                    if isinstance(op, (ast.Is, ast.IsNot)):
                        suspicious = False
                        if isinstance(node.left, ast.Constant) and isinstance(
                            node.left.value, (str, int, float, bytes)
                        ):
                            suspicious = True
                        elif isinstance(comp, ast.Constant) and isinstance(
                            comp.value, (str, int, float, bytes)
                        ):
                            suspicious = True

                        if suspicious:
                            start_l = node.lineno
                            end_l = getattr(node, "end_lineno", start_l) or start_l
                            excerpt = extract_code_excerpt(
                                source_lines, start_l, end_l, config.max_excerpt_lines
                            )
                            evidence = CodeEvidence(
                                evidence_id=f"ev-{uuid.uuid4().hex[:8]}",
                                file_path=file_path,
                                start_line=start_l,
                                end_line=end_l,
                                code_excerpt=excerpt,
                                related_rule=rule.rule_id,
                                content_hash=compute_content_hash(excerpt),
                            )
                            findings.append(
                                ReviewFinding(
                                    finding_id=f"find-{uuid.uuid4().hex[:8]}",
                                    rule_id=rule.rule_id,
                                    title=rule.title,
                                    description=(
                                        "Suspicious identity comparison "
                                        "('is' or 'is not') used with "
                                        "literal value."
                                    ),
                                    category=rule.category,
                                    severity=rule.severity,
                                    confidence=rule.confidence,
                                    file_path=file_path,
                                    start_line=start_l,
                                    end_line=end_l,
                                    evidence=evidence,
                                    impact=(
                                        "Identity comparison with literals may fail."
                                    ),
                                    remediation=rule.remediation_guidance,
                                    source="static_analysis",
                                    requires_human_review=rule.requires_human_review,
                                )
                            )

    def _check_exception_handling(
        self,
        tree: ast.AST,
        file_path: str,
        source_lines: list[str],
        findings: list[ReviewFinding],
        config: ReviewConfiguration,
    ) -> None:
        """REL-001 & REL-002: Bare and empty exception handlers."""
        rel001 = self.registry.get_rule("REL-001")
        rel002 = self.registry.get_rule("REL-002")

        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                start_l = node.lineno
                end_l = getattr(node, "end_lineno", start_l) or start_l

                # REL-001: Bare except:
                if node.type is None and rel001 and rel001.enabled:
                    excerpt = extract_code_excerpt(
                        source_lines, start_l, end_l, config.max_excerpt_lines
                    )
                    evidence = CodeEvidence(
                        evidence_id=f"ev-{uuid.uuid4().hex[:8]}",
                        file_path=file_path,
                        start_line=start_l,
                        end_line=end_l,
                        code_excerpt=excerpt,
                        related_rule=rel001.rule_id,
                        content_hash=compute_content_hash(excerpt),
                    )
                    findings.append(
                        ReviewFinding(
                            finding_id=f"find-{uuid.uuid4().hex[:8]}",
                            rule_id=rel001.rule_id,
                            title=rel001.title,
                            description=rel001.description,
                            category=rel001.category,
                            severity=rel001.severity,
                            confidence=rel001.confidence,
                            file_path=file_path,
                            start_line=start_l,
                            end_line=end_l,
                            evidence=evidence,
                            impact="Catches SystemExit and KeyboardInterrupt.",
                            remediation=rel001.remediation_guidance,
                            source="static_analysis",
                            requires_human_review=rel001.requires_human_review,
                        )
                    )

                # REL-002: Silent / empty exception handler
                is_empty = len(node.body) == 1 and (
                    isinstance(node.body[0], ast.Pass)
                    or (
                        isinstance(node.body[0], ast.Expr)
                        and isinstance(node.body[0].value, ast.Constant)
                    )
                )
                if is_empty and rel002 and rel002.enabled:
                    excerpt = extract_code_excerpt(
                        source_lines, start_l, end_l, config.max_excerpt_lines
                    )
                    evidence = CodeEvidence(
                        evidence_id=f"ev-{uuid.uuid4().hex[:8]}",
                        file_path=file_path,
                        start_line=start_l,
                        end_line=end_l,
                        code_excerpt=excerpt,
                        related_rule=rel002.rule_id,
                        content_hash=compute_content_hash(excerpt),
                    )
                    findings.append(
                        ReviewFinding(
                            finding_id=f"find-{uuid.uuid4().hex[:8]}",
                            rule_id=rel002.rule_id,
                            title=rel002.title,
                            description=rel002.description,
                            category=rel002.category,
                            severity=rel002.severity,
                            confidence=rel002.confidence,
                            file_path=file_path,
                            start_line=start_l,
                            end_line=end_l,
                            evidence=evidence,
                            impact="Silently swallowed errors mask underlying defects.",
                            remediation=rel002.remediation_guidance,
                            source="static_analysis",
                            requires_human_review=rel002.requires_human_review,
                        )
                    )

    def _check_resource_cleanup(
        self,
        tree: ast.AST,
        file_path: str,
        source_lines: list[str],
        findings: list[ReviewFinding],
        config: ReviewConfiguration,
    ) -> None:
        """REL-003: Unhandled resource cleanup with open()."""
        rule = self.registry.get_rule("REL-003")
        if not rule or not rule.enabled:
            return

        with_items = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    with_items.add(item.context_expr)

        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "open"
            ):
                if node not in with_items:
                    start_l = node.lineno
                    end_l = getattr(node, "end_lineno", start_l) or start_l
                    excerpt = extract_code_excerpt(
                        source_lines, start_l, end_l, config.max_excerpt_lines
                    )
                    evidence = CodeEvidence(
                        evidence_id=f"ev-{uuid.uuid4().hex[:8]}",
                        file_path=file_path,
                        start_line=start_l,
                        end_line=end_l,
                        code_excerpt=excerpt,
                        related_rule=rule.rule_id,
                        content_hash=compute_content_hash(excerpt),
                    )
                    findings.append(
                        ReviewFinding(
                            finding_id=f"find-{uuid.uuid4().hex[:8]}",
                            rule_id=rule.rule_id,
                            title=rule.title,
                            description="open() called without context manager.",
                            category=rule.category,
                            severity=rule.severity,
                            confidence=rule.confidence,
                            file_path=file_path,
                            start_line=start_l,
                            end_line=end_l,
                            evidence=evidence,
                            impact="May leak file descriptors on unhandled exceptions.",
                            remediation=rule.remediation_guidance,
                            source="static_analysis",
                            requires_human_review=rule.requires_human_review,
                        )
                    )

    def _check_performance_patterns(
        self,
        tree: ast.AST,
        file_path: str,
        source_lines: list[str],
        findings: list[ReviewFinding],
        config: ReviewConfiguration,
    ) -> None:
        """PERF-001 & PERF-002: Inefficient loop patterns and repeated compilation."""
        perf001 = self.registry.get_rule("PERF-001")
        perf002 = self.registry.get_rule("PERF-002")

        for node in ast.walk(tree):
            if isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
                if perf002 and perf002.enabled:
                    for inner in ast.walk(node):
                        if (
                            isinstance(inner, ast.Call)
                            and isinstance(inner.func, ast.Attribute)
                            and isinstance(inner.func.value, ast.Name)
                            and inner.func.value.id == "re"
                            and inner.func.attr == "compile"
                        ):
                            start_l = inner.lineno
                            end_l = getattr(inner, "end_lineno", start_l) or start_l
                            excerpt = extract_code_excerpt(
                                source_lines, start_l, end_l, config.max_excerpt_lines
                            )
                            evidence = CodeEvidence(
                                evidence_id=f"ev-{uuid.uuid4().hex[:8]}",
                                file_path=file_path,
                                start_line=start_l,
                                end_line=end_l,
                                code_excerpt=excerpt,
                                related_rule=perf002.rule_id,
                                content_hash=compute_content_hash(excerpt),
                            )
                            findings.append(
                                ReviewFinding(
                                    finding_id=f"find-{uuid.uuid4().hex[:8]}",
                                    rule_id=perf002.rule_id,
                                    title=perf002.title,
                                    description="re.compile() invoked inside loop.",
                                    category=perf002.category,
                                    severity=perf002.severity,
                                    confidence=perf002.confidence,
                                    file_path=file_path,
                                    start_line=start_l,
                                    end_line=end_l,
                                    evidence=evidence,
                                    impact="Repeated compilation degrades performance.",
                                    remediation=perf002.remediation_guidance,
                                    source="static_analysis",
                                    requires_human_review=perf002.requires_human_review,
                                )
                            )

                if perf001 and perf001.enabled:
                    outer_target = getattr(node, "target", None)
                    outer_name = (
                        outer_target.id if isinstance(outer_target, ast.Name) else None
                    )
                    for inner in node.body:
                        if isinstance(inner, (ast.For, ast.AsyncFor)):
                            inner_iter = getattr(inner, "iter", None)
                            outer_iter = getattr(node, "iter", None)
                            if (
                                isinstance(inner_iter, ast.Name)
                                and isinstance(outer_iter, ast.Name)
                                and inner_iter.id == outer_iter.id
                                and outer_name
                            ):
                                start_l = inner.lineno
                                end_l = getattr(inner, "end_lineno", start_l) or start_l
                                excerpt = extract_code_excerpt(
                                    source_lines,
                                    start_l,
                                    end_l,
                                    config.max_excerpt_lines,
                                )
                                evidence = CodeEvidence(
                                    evidence_id=f"ev-{uuid.uuid4().hex[:8]}",
                                    file_path=file_path,
                                    start_line=start_l,
                                    end_line=end_l,
                                    code_excerpt=excerpt,
                                    related_rule=perf001.rule_id,
                                    content_hash=compute_content_hash(excerpt),
                                )
                                findings.append(
                                    ReviewFinding(
                                        finding_id=f"find-{uuid.uuid4().hex[:8]}",
                                        rule_id=perf001.rule_id,
                                        title=perf001.title,
                                        description=(
                                            f"Nested loop iterates over the same "
                                            f"collection '{inner_iter.id}' (O(n^2))."
                                        ),
                                        category=perf001.category,
                                        severity=perf001.severity,
                                        confidence=perf001.confidence,
                                        file_path=file_path,
                                        start_line=start_l,
                                        end_line=end_l,
                                        evidence=evidence,
                                        impact="Quadratic complexity on collection.",
                                        remediation=perf001.remediation_guidance,
                                        source="static_analysis",
                                        requires_human_review=(
                                            perf001.requires_human_review
                                        ),
                                    )
                                )

    def _filter_findings(
        self,
        findings: list[ReviewFinding],
        config: ReviewConfiguration,
    ) -> list[ReviewFinding]:
        """Apply category filters, rule disablement, severity gates, and file caps."""
        severity_order = {
            FindingSeverity.INFORMATIONAL: 0,
            FindingSeverity.LOW: 1,
            FindingSeverity.MEDIUM: 2,
            FindingSeverity.HIGH: 3,
            FindingSeverity.CRITICAL: 4,
        }
        min_sev_val = severity_order.get(config.min_severity, 0)

        filtered: list[ReviewFinding] = []
        file_counts: dict[str, int] = {}

        for f in findings:
            if f.category not in config.enabled_categories:
                continue
            if f.rule_id in config.disabled_rule_ids:
                continue
            if severity_order.get(f.severity, 0) < min_sev_val:
                continue

            current_count = file_counts.get(f.file_path, 0)
            if current_count >= config.max_findings_per_file:
                continue

            file_counts[f.file_path] = current_count + 1
            filtered.append(f)

        return filtered
