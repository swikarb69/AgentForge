"""Security analysis and vulnerability detection engine for code review."""

import ast
import hashlib
import os
import re
import uuid
from pathlib import Path

from backend.app.review.models import (
    CodeEvidence,
    FindingCategory,
    FindingConfidence,
    FindingSeverity,
    ReviewConfiguration,
    ReviewFinding,
    redact_secrets,
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


# Known high-confidence token / secret regexes
TOKEN_REGEXES: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"""gh[pousr]_[A-Za-z0-9_]{36,255}"""),
        "Hardcoded GitHub Token",
    ),
    (
        re.compile(r"""AKIA[0-9A-Z]{16}"""),
        "Hardcoded AWS Access Key",
    ),
    (
        re.compile(r"""-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"""),
        "Hardcoded Private Key",
    ),
    (
        re.compile(r"""(?i)bearer\s+[a-zA-Z0-9_\-\.]{25,}"""),
        "Hardcoded Bearer Authentication Token",
    ),
    (
        re.compile(r"""xox[baprs]-[0-9a-zA-Z]{10,48}"""),
        "Hardcoded Slack Token",
    ),
    (
        re.compile(
            r"""eyJ[a-zA-Z0-9_-]{10,}\.eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}"""
        ),
        "Hardcoded JWT Token",
    ),
]

SECRET_VAR_NAME_PATTERN = re.compile(
    r"""(?i)^.*(api[_-]?key|secret[_-]?key|auth[_-]?token|access[_-]?token|"""
    r"""private[_-]?key|client[_-]?secret|password|passwd).*$"""
)


class SecurityAnalyzer:
    """Production-grade deterministic security analysis engine."""

    def __init__(self, registry: RuleRegistry | None = None) -> None:
        """Initialize security analyzer with optional rule registry."""
        self.registry = registry if registry is not None else get_default_registry()

    def analyze_source(
        self,
        file_path: str,
        source_code: str,
        config: ReviewConfiguration | None = None,
    ) -> list[ReviewFinding]:
        """Analyze a single Python source file for security vulnerabilities."""
        active_config = config or ReviewConfiguration()
        if FindingCategory.SECURITY not in active_config.enabled_categories:
            return []

        clean_path = file_path.replace("\\", "/").lstrip("/")
        findings: list[ReviewFinding] = []
        source_lines = source_code.splitlines()

        # Step 1: Text-based security checks (Secrets, Tokens)
        self._check_secrets_text(clean_path, source_lines, findings, active_config)

        # Step 2: AST-based security checks
        try:
            tree = ast.parse(source_code, filename=clean_path)
            self._check_sql_injection(
                tree, clean_path, source_lines, findings, active_config
            )
            self._check_command_injection(
                tree, clean_path, source_lines, findings, active_config
            )
            self._check_dynamic_execution(
                tree, clean_path, source_lines, findings, active_config
            )
            self._check_path_traversal(
                tree, clean_path, source_lines, findings, active_config
            )
            self._check_insecure_deserialization(
                tree, clean_path, source_lines, findings, active_config
            )
            self._check_insecure_tempfile(
                tree, clean_path, source_lines, findings, active_config
            )
            self._check_secret_variables_ast(
                tree, clean_path, source_lines, findings, active_config
            )
        except SyntaxError:
            # Syntax errors are handled cleanly by DeterministicStaticAnalyzer
            pass

        # Step 3: Apply configuration filters
        filtered: list[ReviewFinding] = []
        severity_rank = {
            FindingSeverity.LOW: 1,
            FindingSeverity.MEDIUM: 2,
            FindingSeverity.HIGH: 3,
            FindingSeverity.CRITICAL: 4,
        }
        min_rank = severity_rank.get(active_config.min_severity, 1)

        for finding in findings:
            if finding.rule_id in active_config.disabled_rule_ids:
                continue
            if severity_rank.get(finding.severity, 1) < min_rank:
                continue
            filtered.append(finding)

        if active_config.max_findings_per_file:
            return filtered[: active_config.max_findings_per_file]
        return filtered

    def analyze_files(
        self,
        files: dict[str, str],
        config: ReviewConfiguration | None = None,
    ) -> list[ReviewFinding]:
        """Analyze a collection of files mapped by path to source code."""
        all_findings: list[ReviewFinding] = []
        for path, content in files.items():
            if path.endswith(".py"):
                all_findings.extend(self.analyze_source(path, content, config))
        return all_findings

    def analyze_repository(
        self,
        root_path: Path | str,
        config: ReviewConfiguration | None = None,
    ) -> list[ReviewFinding]:
        """Scan all eligible Python files in repository path for vulnerabilities."""
        base_dir = Path(root_path).resolve()
        if not base_dir.is_dir():
            return []

        all_findings: list[ReviewFinding] = []
        excluded_dirs = {
            ".git",
            ".venv",
            "venv",
            "node_modules",
            "__pycache__",
            ".pytest_cache",
            ".ruff_cache",
        }

        for root, dirs, files in os.walk(base_dir):
            dirs[:] = [d for d in dirs if d not in excluded_dirs]
            for file_name in files:
                if file_name.endswith(".py"):
                    full_p = Path(root) / file_name
                    try:
                        rel_p = full_p.relative_to(base_dir).as_posix()
                        content = full_p.read_text(encoding="utf-8", errors="replace")
                        all_findings.extend(self.analyze_source(rel_p, content, config))
                    except (OSError, UnicodeDecodeError):
                        continue

        return all_findings

    def analyze_patch(
        self,
        patch_text: str,
        config: ReviewConfiguration | None = None,
    ) -> list[ReviewFinding]:
        """Analyze added lines in unified diff patch for security risks."""
        active_config = config or ReviewConfiguration()
        if FindingCategory.SECURITY not in active_config.enabled_categories:
            return []

        findings: list[ReviewFinding] = []
        current_file: str | None = None
        added_lines: list[tuple[int, str]] = []

        for line in patch_text.splitlines():
            if line.startswith("+++ b/"):
                if current_file and added_lines:
                    self._scan_patch_lines(
                        current_file, added_lines, findings, active_config
                    )
                current_file = line[6:].strip()
                added_lines = []
            elif line.startswith("@@"):
                # Header line
                pass
            elif line.startswith("+") and not line.startswith("+++"):
                added_lines.append((len(added_lines) + 1, line[1:]))

        if current_file and added_lines:
            self._scan_patch_lines(current_file, added_lines, findings, active_config)

        return findings

    def _scan_patch_lines(
        self,
        file_path: str,
        lines: list[tuple[int, str]],
        findings: list[ReviewFinding],
        config: ReviewConfiguration,
    ) -> None:
        """Scan added patch lines for secret tokens and obvious patterns."""
        rule = self.registry.get_rule("SEC-004")
        if not rule or not rule.enabled:
            return

        for line_no, text in lines:
            for pattern, title in TOKEN_REGEXES:
                if pattern.search(text):
                    redacted = redact_secrets(text)
                    excerpt = (
                        redacted if len(redacted) <= 100 else redacted[:97] + "..."
                    )
                    evidence = CodeEvidence(
                        evidence_id=f"ev-{uuid.uuid4().hex[:8]}",
                        file_path=file_path,
                        start_line=line_no,
                        end_line=line_no,
                        code_excerpt=excerpt,
                        related_rule=rule.rule_id,
                        content_hash=compute_content_hash(excerpt),
                    )
                    findings.append(
                        ReviewFinding(
                            finding_id=f"find-{uuid.uuid4().hex[:8]}",
                            rule_id=rule.rule_id,
                            title=title,
                            description=(
                                f"Detected potential {title.lower()} added in patch."
                            ),
                            category=rule.category,
                            severity=rule.severity,
                            confidence=rule.confidence,
                            file_path=file_path,
                            start_line=line_no,
                            end_line=line_no,
                            evidence=evidence,
                            impact=(
                                "Exposing credentials in diffs leads to "
                                "unauthorized access."
                            ),
                            remediation=rule.remediation_guidance,
                            source="security_analysis",
                            requires_human_review=True,
                        )
                    )

    # -----------------------------------------------------------------------
    # Detector Implementations
    # -----------------------------------------------------------------------

    def _check_secrets_text(
        self,
        file_path: str,
        source_lines: list[str],
        findings: list[ReviewFinding],
        config: ReviewConfiguration,
    ) -> None:
        """SEC-004: Check line-by-line text for high-confidence tokens."""
        rule = self.registry.get_rule("SEC-004")
        if not rule or not rule.enabled:
            return

        for idx, line in enumerate(source_lines):
            line_no = idx + 1
            for pattern, title in TOKEN_REGEXES:
                if pattern.search(line):
                    excerpt = extract_code_excerpt(
                        source_lines, line_no, line_no, config.max_excerpt_lines
                    )
                    evidence = CodeEvidence(
                        evidence_id=f"ev-{uuid.uuid4().hex[:8]}",
                        file_path=file_path,
                        start_line=line_no,
                        end_line=line_no,
                        code_excerpt=excerpt,
                        related_rule=rule.rule_id,
                        content_hash=compute_content_hash(excerpt),
                    )
                    findings.append(
                        ReviewFinding(
                            finding_id=f"find-{uuid.uuid4().hex[:8]}",
                            rule_id=rule.rule_id,
                            title=title,
                            description=(
                                f"Detected potential {title.lower()} in source code."
                            ),
                            category=rule.category,
                            severity=rule.severity,
                            confidence=rule.confidence,
                            file_path=file_path,
                            start_line=line_no,
                            end_line=line_no,
                            evidence=evidence,
                            impact=(
                                "Plaintext credentials exposed in code can "
                                "lead to unauthorized system access."
                            ),
                            remediation=rule.remediation_guidance,
                            source="security_analysis",
                            requires_human_review=True,
                        )
                    )

    def _check_secret_variables_ast(
        self,
        tree: ast.AST,
        file_path: str,
        source_lines: list[str],
        findings: list[ReviewFinding],
        config: ReviewConfiguration,
    ) -> None:
        """SEC-004: Check assignments to sensitive variable names
        with string literals.
        """
        rule = self.registry.get_rule("SEC-004")
        if not rule or not rule.enabled:
            return

        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        name = target.id
                        if SECRET_VAR_NAME_PATTERN.match(name):
                            if isinstance(node.value, ast.Constant) and isinstance(
                                node.value.value, str
                            ):
                                val = node.value.value.strip()
                                # Ignore obvious empty/placeholder values
                                if len(val) >= 8 and not val.startswith(
                                    ("${", "{{", "env.", "os.environ")
                                ):
                                    start_l = node.lineno
                                    end_l = (
                                        getattr(node, "end_lineno", start_l) or start_l
                                    )
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
                                        related_rule=rule.rule_id,
                                        content_hash=compute_content_hash(excerpt),
                                    )
                                    findings.append(
                                        ReviewFinding(
                                            finding_id=f"find-{uuid.uuid4().hex[:8]}",
                                            rule_id=rule.rule_id,
                                            title=rule.title,
                                            description=(
                                                f"Hardcoded secret assigned "
                                                f"to variable '{name}'."
                                            ),
                                            category=rule.category,
                                            severity=rule.severity,
                                            confidence=FindingConfidence.MEDIUM,
                                            file_path=file_path,
                                            start_line=start_l,
                                            end_line=end_l,
                                            affected_symbol=name,
                                            evidence=evidence,
                                            impact=(
                                                "Credentials embedded in "
                                                "source files are vulnerable "
                                                "to leakage."
                                            ),
                                            remediation=rule.remediation_guidance,
                                            source="security_analysis",
                                            requires_human_review=True,
                                        )
                                    )

    def _check_sql_injection(
        self,
        tree: ast.AST,
        file_path: str,
        source_lines: list[str],
        findings: list[ReviewFinding],
        config: ReviewConfiguration,
    ) -> None:
        """SEC-001: String concatenation or formatting into SQL execution calls."""
        rule = self.registry.get_rule("SEC-001")
        if not rule or not rule.enabled:
            return

        sql_execution_methods = {
            "execute",
            "executemany",
            "raw_sql",
            "raw",
            "cursor_execute",
        }
        sql_keywords = (
            "select",
            "insert",
            "update",
            "delete",
            "drop",
            "alter",
            "create",
        )
        tainted_sql_vars: set[str] = set()

        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                is_sql_formatting = False
                if isinstance(node.value, ast.JoinedStr):
                    for part in node.value.values:
                        if (
                            isinstance(part, ast.Constant)
                            and isinstance(part.value, str)
                            and any(kw in part.value.lower() for kw in sql_keywords)
                        ):
                            is_sql_formatting = True
                            break
                elif isinstance(node.value, ast.BinOp) and isinstance(
                    node.value.op, (ast.Mod, ast.Add)
                ):
                    left = getattr(node.value.left, "value", None)
                    if isinstance(left, str) and any(
                        kw in left.lower() for kw in sql_keywords
                    ):
                        is_sql_formatting = True
                elif isinstance(node.value, ast.Call):
                    if (
                        isinstance(node.value.func, ast.Attribute)
                        and node.value.func.attr == "format"
                    ):
                        call_obj = getattr(node.value.func.value, "value", None)
                        if isinstance(call_obj, str) and any(
                            kw in call_obj.lower() for kw in sql_keywords
                        ):
                            is_sql_formatting = True

                if is_sql_formatting:
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            tainted_sql_vars.add(target.id)

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func_name = ""
                if isinstance(node.func, ast.Attribute):
                    func_name = node.func.attr
                elif isinstance(node.func, ast.Name):
                    func_name = node.func.id

                if func_name in sql_execution_methods and node.args:
                    arg0 = node.args[0]
                    suspicious = False
                    reason = ""

                    if isinstance(arg0, ast.JoinedStr):
                        suspicious = True
                        reason = "f-string formatting"
                    elif isinstance(arg0, ast.BinOp) and isinstance(
                        arg0.op, (ast.Mod, ast.Add)
                    ):
                        suspicious = True
                        reason = "string concatenation or % formatting"
                    elif isinstance(arg0, ast.Call):
                        if (
                            isinstance(arg0.func, ast.Attribute)
                            and arg0.func.attr == "format"
                        ):
                            suspicious = True
                            reason = ".format() string formatting"
                    elif isinstance(arg0, ast.Name) and arg0.id in tainted_sql_vars:
                        suspicious = True
                        reason = f"formatted SQL string variable '{arg0.id}'"

                    if suspicious:
                        start_l = node.lineno
                        end_l = getattr(node, "end_lineno", start_l) or start_l
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
                            related_rule=rule.rule_id,
                            content_hash=compute_content_hash(excerpt),
                        )
                        findings.append(
                            ReviewFinding(
                                finding_id=f"find-{uuid.uuid4().hex[:8]}",
                                rule_id=rule.rule_id,
                                title=rule.title,
                                description=(
                                    f"Potential SQL injection via {reason} "
                                    f"in '{func_name}()' query argument."
                                ),
                                category=rule.category,
                                severity=rule.severity,
                                confidence=rule.confidence,
                                file_path=file_path,
                                start_line=start_l,
                                end_line=end_l,
                                affected_symbol=func_name,
                                evidence=evidence,
                                impact=(
                                    "Untrusted data interpolated into SQL "
                                    "allows arbitrary database execution."
                                ),
                                remediation=rule.remediation_guidance,
                                source="security_analysis",
                                requires_human_review=True,
                            )
                        )

    def _check_command_injection(
        self,
        tree: ast.AST,
        file_path: str,
        source_lines: list[str],
        findings: list[ReviewFinding],
        config: ReviewConfiguration,
    ) -> None:
        """SEC-002: Subprocess execution with shell=True or os.system."""
        rule = self.registry.get_rule("SEC-002")
        if not rule or not rule.enabled:
            return

        subprocess_methods = {
            "run",
            "Popen",
            "call",
            "check_call",
            "check_output",
        }
        os_shell_methods = {
            "system",
            "popen",
            "popen2",
            "popen3",
            "popen4",
        }

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                detected = False
                details = ""

                # Case 1: os.system(...) or os.popen(...)
                if isinstance(node.func, ast.Attribute):
                    if (
                        isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "os"
                        and node.func.attr in os_shell_methods
                    ):
                        detected = True
                        details = f"os.{node.func.attr}() invocation"
                    elif (
                        isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "subprocess"
                        and node.func.attr in subprocess_methods
                    ):
                        # Case 2: subprocess.run(..., shell=True)
                        for kw in node.keywords:
                            if kw.arg == "shell":
                                if (
                                    isinstance(kw.value, ast.Constant)
                                    and kw.value.value is True
                                ):
                                    detected = True
                                    details = (
                                        f"subprocess.{node.func.attr}() with shell=True"
                                    )
                                elif not isinstance(kw.value, ast.Constant):
                                    detected = True
                                    details = (
                                        f"subprocess.{node.func.attr}() "
                                        "with dynamic shell flag"
                                    )

                if detected:
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
                                f"Unsafe shell execution detected: {details}."
                            ),
                            category=rule.category,
                            severity=rule.severity,
                            confidence=rule.confidence,
                            file_path=file_path,
                            start_line=start_l,
                            end_line=end_l,
                            evidence=evidence,
                            impact=(
                                "Command injection allows an attacker to "
                                "execute arbitrary system commands."
                            ),
                            remediation=rule.remediation_guidance,
                            source="security_analysis",
                            requires_human_review=True,
                        )
                    )

    def _check_dynamic_execution(
        self,
        tree: ast.AST,
        file_path: str,
        source_lines: list[str],
        findings: list[ReviewFinding],
        config: ReviewConfiguration,
    ) -> None:
        """SEC-003: Dynamic code execution via eval(), exec(), or __import__()."""
        rule = self.registry.get_rule("SEC-003")
        if not rule or not rule.enabled:
            return

        dangerous_calls = {"eval", "exec", "__import__"}

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                callee: str | None = None
                if isinstance(node.func, ast.Name):
                    callee = node.func.id
                elif (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr in dangerous_calls
                ):
                    callee = node.func.attr

                if callee in dangerous_calls:
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
                                f"Dynamic execution with '{callee}()' detected."
                            ),
                            category=rule.category,
                            severity=rule.severity,
                            confidence=rule.confidence,
                            file_path=file_path,
                            start_line=start_l,
                            end_line=end_l,
                            affected_symbol=callee,
                            evidence=evidence,
                            impact=(
                                "Dynamic code execution allows arbitrary code "
                                "injection and complete control."
                            ),
                            remediation=rule.remediation_guidance,
                            source="security_analysis",
                            requires_human_review=True,
                        )
                    )

    def _check_path_traversal(
        self,
        tree: ast.AST,
        file_path: str,
        source_lines: list[str],
        findings: list[ReviewFinding],
        config: ReviewConfiguration,
    ) -> None:
        """SEC-005: Insecure path traversal via '..' in file paths or concatenation."""
        rule = self.registry.get_rule("SEC-005")
        if not rule or not rule.enabled:
            return

        for node in ast.walk(tree):
            suspicious = False
            details = ""

            if isinstance(node, ast.Call):
                func_name = ""
                if isinstance(node.func, ast.Name):
                    func_name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    func_name = node.func.attr

                if func_name in {"open", "read_text", "write_text", "unlink", "remove"}:
                    for arg in node.args:
                        # Check for constant containing '..'
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                            if ".." in arg.value:
                                suspicious = True
                        # Check joined string or addition without sanitization
                        elif isinstance(arg, ast.JoinedStr):
                            for part in arg.values:
                                if (
                                    isinstance(part, ast.Constant)
                                    and isinstance(part.value, str)
                                    and ".." in part.value
                                ):
                                    suspicious = True
                                    details = "parent traversal ('..') in f-string"
                        elif isinstance(arg, ast.BinOp) and isinstance(arg.op, ast.Add):
                            if isinstance(arg.left, ast.Constant) and ".." in str(
                                arg.left.value
                            ):
                                suspicious = True
                                details = "parent traversal ('..') in concatenated path"
                            elif isinstance(arg.right, ast.Constant) and ".." in str(
                                arg.right.value
                            ):
                                suspicious = True
                                details = "parent traversal ('..') in concatenated path"

                    if suspicious:
                        start_l = node.lineno
                        end_l = getattr(node, "end_lineno", start_l) or start_l
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
                            related_rule=rule.rule_id,
                            content_hash=compute_content_hash(excerpt),
                        )
                        findings.append(
                            ReviewFinding(
                                finding_id=f"find-{uuid.uuid4().hex[:8]}",
                                rule_id=rule.rule_id,
                                title=rule.title,
                                description=(
                                    f"Potential path traversal risk ({details})."
                                ),
                                category=rule.category,
                                severity=rule.severity,
                                confidence=rule.confidence,
                                file_path=file_path,
                                start_line=start_l,
                                end_line=end_l,
                                evidence=evidence,
                                impact=(
                                    "Unsanitized path traversal permits "
                                    "reading or writing files outside the "
                                    "workspace."
                                ),
                                remediation=rule.remediation_guidance,
                                source="security_analysis",
                                requires_human_review=True,
                            )
                        )

    def _check_insecure_deserialization(
        self,
        tree: ast.AST,
        file_path: str,
        source_lines: list[str],
        findings: list[ReviewFinding],
        config: ReviewConfiguration,
    ) -> None:
        """SEC-006: Insecure deserialization via pickle, marshal, or unsafe yaml."""
        rule = self.registry.get_rule("SEC-006")
        if not rule or not rule.enabled:
            return

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                suspicious = False
                details = ""

                # pickle.loads / pickle.load / _pickle.loads
                if isinstance(node.func, ast.Attribute):
                    deserializers = {"pickle", "_pickle", "cPickle", "marshal"}
                    if (
                        isinstance(node.func.value, ast.Name)
                        and node.func.value.id in deserializers
                        and node.func.attr in {"loads", "load"}
                    ):
                        suspicious = True
                        details = f"{node.func.value.id}.{node.func.attr}()"
                    # yaml.load without SafeLoader or yaml.unsafe_load
                    elif (
                        isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "yaml"
                    ):
                        if node.func.attr == "unsafe_load":
                            suspicious = True
                            details = "yaml.unsafe_load()"
                        elif node.func.attr == "load":
                            has_safe_loader = False
                            safe_names = {"SafeLoader", "CSafeLoader"}
                            for kw in node.keywords:
                                if kw.arg == "Loader":
                                    if (
                                        isinstance(kw.value, ast.Attribute)
                                        and kw.value.attr in safe_names
                                    ):
                                        has_safe_loader = True
                                    elif (
                                        isinstance(kw.value, ast.Name)
                                        and kw.value.id in safe_names
                                    ):
                                        has_safe_loader = True
                            if not has_safe_loader:
                                suspicious = True
                                details = "yaml.load() without SafeLoader"

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
                            description=(f"Insecure deserialization via {details}."),
                            category=rule.category,
                            severity=rule.severity,
                            confidence=rule.confidence,
                            file_path=file_path,
                            start_line=start_l,
                            end_line=end_l,
                            evidence=evidence,
                            impact=(
                                "Deserialization of untrusted payloads leads "
                                "to remote code execution."
                            ),
                            remediation=rule.remediation_guidance,
                            source="security_analysis",
                            requires_human_review=True,
                        )
                    )

    def _check_insecure_tempfile(
        self,
        tree: ast.AST,
        file_path: str,
        source_lines: list[str],
        findings: list[ReviewFinding],
        config: ReviewConfiguration,
    ) -> None:
        """SEC-007: Insecure temporary file creation via tempfile.mktemp
        or predictable paths.
        """
        rule = self.registry.get_rule("SEC-007")
        if not rule or not rule.enabled:
            return

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                suspicious = False
                details = ""

                func_name = ""
                is_mktemp = False
                if isinstance(node.func, ast.Attribute):
                    if (
                        isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "tempfile"
                        and node.func.attr == "mktemp"
                    ):
                        is_mktemp = True
                    func_name = node.func.attr
                elif isinstance(node.func, ast.Name):
                    func_name = node.func.id

                if is_mktemp:
                    suspicious = True
                    details = "tempfile.mktemp() (vulnerable to TOCTOU)"
                elif func_name in {"open", "NamedTemporaryFile"}:
                    insecure_prefixes = (
                        "".join(["/", "tmp", "/"]),
                        "".join(["/", "var", "/", "tmp", "/"]),
                    )
                    for arg in node.args:
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                            if arg.value.startswith(insecure_prefixes):
                                suspicious = True
                                details = f"predictable temporary path '{arg.value}'"

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
                            description=(f"Insecure temporary file usage: {details}."),
                            category=rule.category,
                            severity=rule.severity,
                            confidence=rule.confidence,
                            file_path=file_path,
                            start_line=start_l,
                            end_line=end_l,
                            evidence=evidence,
                            impact=(
                                "Predictable temporary paths introduce race "
                                "conditions allowing symlink attacks."
                            ),
                            remediation=rule.remediation_guidance,
                            source="security_analysis",
                            requires_human_review=rule.requires_human_review,
                        )
                    )
