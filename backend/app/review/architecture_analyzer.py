"""Architecture and dependency analyzer for code review."""

import ast
import hashlib
import os
import sys
import tomllib
import uuid
from pathlib import Path

from backend.app.review.models import (
    CodeEvidence,
    FindingCategory,
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


class ImportCollector(ast.NodeVisitor):
    """AST visitor collecting all import statements and target modules."""

    def __init__(self) -> None:
        self.imports: list[tuple[str, int]] = []
        self.from_imports: list[tuple[str, str, int]] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.append((alias.name, node.lineno))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        for alias in node.names:
            self.from_imports.append((module, alias.name, node.lineno))
        self.generic_visit(node)


class ArchitectureAnalyzer:
    """Production-grade architectural rule and dependency structure analyzer."""

    def __init__(self, registry: RuleRegistry | None = None) -> None:
        """Initialize analyzer with optional rule registry."""
        self.registry = registry if registry is not None else get_default_registry()

    def analyze_files(
        self,
        files: dict[str, str],
        config: ReviewConfiguration | None = None,
        repo_root: Path | str | None = None,
    ) -> list[ReviewFinding]:
        """Analyze a collection of files for architectural defects."""
        active_config = config or ReviewConfiguration()
        if FindingCategory.ARCHITECTURE not in active_config.enabled_categories:
            return []

        findings: list[ReviewFinding] = []

        # Parse ASTs and collect imports
        file_imports: dict[str, ImportCollector] = {}
        for path, code in files.items():
            clean_path = path.replace("\\", "/").lstrip("/")
            try:
                tree = ast.parse(code, filename=clean_path)
                collector = ImportCollector()
                collector.visit(tree)
                file_imports[clean_path] = collector
            except SyntaxError:
                continue

        # Check ARCH-001: Circular import dependencies
        self._check_circular_imports(file_imports, files, findings, active_config)

        # Check ARCH-002: Layer boundary violations
        self._check_layer_violations(file_imports, files, findings, active_config)

        # Check ARCH-003: Unapproved external dependencies
        self._check_unapproved_dependencies(
            file_imports, files, findings, active_config, repo_root
        )

        # Check ARCH-004: Excessive file coupling
        self._check_excessive_coupling(file_imports, files, findings, active_config)

        # Filter by severity and disabled rules
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

        return filtered

    def analyze_repository(
        self,
        root_path: Path | str,
        config: ReviewConfiguration | None = None,
    ) -> list[ReviewFinding]:
        """Analyze all eligible Python files in repository for architecture issues."""
        base_dir = Path(root_path).resolve()
        if not base_dir.is_dir():
            return []

        files: dict[str, str] = {}
        excluded_dirs = {
            ".git",
            ".venv",
            "venv",
            "node_modules",
            "__pycache__",
            ".pytest_cache",
            ".ruff_cache",
        }

        for root, dirs, filenames in os.walk(base_dir):
            dirs[:] = [d for d in dirs if d not in excluded_dirs]
            for file_name in filenames:
                if file_name.endswith(".py"):
                    full_p = Path(root) / file_name
                    try:
                        rel_p = full_p.relative_to(base_dir).as_posix()
                        content = full_p.read_text(encoding="utf-8", errors="replace")
                        files[rel_p] = content
                    except (OSError, UnicodeDecodeError):
                        continue

        return self.analyze_files(files, config, repo_root=base_dir)

    # -----------------------------------------------------------------------
    # Rule Implementations
    # -----------------------------------------------------------------------

    def _check_circular_imports(
        self,
        file_imports: dict[str, ImportCollector],
        files: dict[str, str],
        findings: list[ReviewFinding],
        config: ReviewConfiguration,
    ) -> None:
        """ARCH-001: Detect circular import cycles between modules."""
        rule = self.registry.get_rule("ARCH-001")
        if not rule or not rule.enabled:
            return

        # Map file paths to module identifiers
        path_to_mod: dict[str, str] = {}
        mod_to_path: dict[str, str] = {}
        for path in file_imports:
            mod_name = path.replace("/", ".").rstrip(".py")
            if mod_name.endswith(".__init__"):
                mod_name = mod_name[:-9]
            path_to_mod[path] = mod_name
            mod_to_path[mod_name] = path

        # Build adjacency list
        graph: dict[str, set[str]] = {path: set() for path in file_imports}
        import_lines: dict[tuple[str, str], int] = {}

        for path, collector in file_imports.items():
            all_line_pairs = [(m, line) for m, line in collector.imports] + [
                (m, line) for m, _, line in collector.from_imports
            ]
            for target_mod, line in all_line_pairs:
                for cand_mod, cand_path in mod_to_path.items():
                    if (
                        target_mod == cand_mod or target_mod.startswith(cand_mod + ".")
                    ) and cand_path != path:
                        graph[path].add(cand_path)
                        import_lines[(path, cand_path)] = line

        # Find cycles using DFS
        visited: set[str] = set()
        rec_stack: list[str] = []
        reported_cycles: set[frozenset[str]] = set()

        def dfs(node: str) -> None:
            visited.add(node)
            rec_stack.append(node)

            for neighbor in graph.get(node, set()):
                if neighbor not in visited:
                    dfs(neighbor)
                elif neighbor in rec_stack:
                    # Cycle detected
                    idx = rec_stack.index(neighbor)
                    cycle = rec_stack[idx:]
                    cycle_set = frozenset(cycle)
                    if cycle_set not in reported_cycles and len(cycle) >= 2:
                        reported_cycles.add(cycle_set)
                        self._report_cycle(
                            cycle,
                            import_lines,
                            files,
                            findings,
                            rule,
                            config,
                        )

            rec_stack.pop()

        for p in list(file_imports.keys()):
            if p not in visited:
                dfs(p)

    def _report_cycle(
        self,
        cycle: list[str],
        import_lines: dict[tuple[str, str], int],
        files: dict[str, str],
        findings: list[ReviewFinding],
        rule: ReviewRule,
        config: ReviewConfiguration,
    ) -> None:
        """Create finding for detected circular import cycle."""
        first_file = cycle[0]
        second_file = cycle[1]
        line = import_lines.get((first_file, second_file), 1)

        source_lines = files.get(first_file, "").splitlines()
        excerpt = extract_code_excerpt(
            source_lines, line, line, config.max_excerpt_lines
        )
        cycle_str = " -> ".join(cycle + [first_file])

        evidence = CodeEvidence(
            evidence_id=f"ev-{uuid.uuid4().hex[:8]}",
            file_path=first_file,
            start_line=line,
            end_line=line,
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
                    f"Circular import dependency detected across: {cycle_str}"
                ),
                category=rule.category,
                severity=rule.severity,
                confidence=rule.confidence,
                file_path=first_file,
                start_line=line,
                end_line=line,
                evidence=evidence,
                impact=(
                    "Circular imports cause module initialization failures "
                    "and subtle import-time AttributeError bugs."
                ),
                remediation=rule.remediation_guidance,
                source="architecture_analysis",
                requires_human_review=rule.requires_human_review,
            )
        )

    def _check_layer_violations(
        self,
        file_imports: dict[str, ImportCollector],
        files: dict[str, str],
        findings: list[ReviewFinding],
        config: ReviewConfiguration,
    ) -> None:
        """ARCH-002: Detect lower-tier layers importing higher-tier layers."""
        rule = self.registry.get_rule("ARCH-002")
        if not rule or not rule.enabled:
            return

        # Lower tier (e.g., models, domain) should not import upper tier (api, routes)
        # Definition: file in models/ or repository/ importing from api/ or routes/
        lower_patterns = ("models.py", "repository/", "domain/")
        upper_patterns = ("backend.app.api", ".api.", "routes", "main")

        for path, collector in file_imports.items():
            is_lower = any(pattern in path for pattern in lower_patterns)
            if not is_lower:
                continue

            all_imports = [(m, line) for m, line in collector.imports] + [
                (m, line) for m, _, line in collector.from_imports
            ]

            for mod, line in all_imports:
                is_violation = any(upper in mod for upper in upper_patterns)
                if is_violation:
                    source_lines = files.get(path, "").splitlines()
                    excerpt = extract_code_excerpt(
                        source_lines, line, line, config.max_excerpt_lines
                    )
                    evidence = CodeEvidence(
                        evidence_id=f"ev-{uuid.uuid4().hex[:8]}",
                        file_path=path,
                        start_line=line,
                        end_line=line,
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
                                f"Layer boundary violation: lower-tier module "
                                f"'{path}' imports higher-tier module '{mod}'."
                            ),
                            category=rule.category,
                            severity=rule.severity,
                            confidence=rule.confidence,
                            file_path=path,
                            start_line=line,
                            end_line=line,
                            evidence=evidence,
                            impact=(
                                "Coupling core models or domain logic to "
                                "presentation/API tiers violates clean "
                                "architecture."
                            ),
                            remediation=rule.remediation_guidance,
                            source="architecture_analysis",
                            requires_human_review=rule.requires_human_review,
                        )
                    )

    def _check_unapproved_dependencies(
        self,
        file_imports: dict[str, ImportCollector],
        files: dict[str, str],
        findings: list[ReviewFinding],
        config: ReviewConfiguration,
        repo_root: Path | str | None,
    ) -> None:
        """ARCH-003: Check for third-party dependencies not in pyproject.toml."""
        rule = self.registry.get_rule("ARCH-003")
        if not rule or not rule.enabled:
            return

        approved = self._load_approved_dependencies(repo_root)
        stdlib: set[str] = set(getattr(sys, "stdlib_module_names", set()))

        for path, collector in file_imports.items():
            all_imports = [(m, line) for m, line in collector.imports] + [
                (m, line) for m, _, line in collector.from_imports
            ]

            for mod, line in all_imports:
                if not mod or mod.startswith("."):
                    continue
                root_pkg = mod.split(".")[0].lower()

                # Ignore standard library or internal modules
                if root_pkg in stdlib or root_pkg in {
                    "backend",
                    "tests",
                    "app",
                }:
                    continue

                # Check if package is approved
                if root_pkg not in approved:
                    source_lines = files.get(path, "").splitlines()
                    excerpt = extract_code_excerpt(
                        source_lines, line, line, config.max_excerpt_lines
                    )
                    evidence = CodeEvidence(
                        evidence_id=f"ev-{uuid.uuid4().hex[:8]}",
                        file_path=path,
                        start_line=line,
                        end_line=line,
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
                                f"Unapproved external dependency '{root_pkg}' "
                                f"imported in '{path}'."
                            ),
                            category=rule.category,
                            severity=rule.severity,
                            confidence=rule.confidence,
                            file_path=path,
                            start_line=line,
                            end_line=line,
                            evidence=evidence,
                            impact=(
                                "Undeclared dependencies break container builds "
                                "and introduce unvetted supply chain risks."
                            ),
                            remediation=rule.remediation_guidance,
                            source="architecture_analysis",
                            requires_human_review=rule.requires_human_review,
                        )
                    )

    def _check_excessive_coupling(
        self,
        file_imports: dict[str, ImportCollector],
        files: dict[str, str],
        findings: list[ReviewFinding],
        config: ReviewConfiguration,
    ) -> None:
        """ARCH-004: Flag files importing from more than 15 distinct
        internal modules.
        """
        rule = self.registry.get_rule("ARCH-004")
        if not rule or not rule.enabled:
            return

        for path, collector in file_imports.items():
            internal_modules: set[str] = set()
            first_line = 1

            for mod, line in collector.imports:
                if mod.startswith("backend."):
                    internal_modules.add(mod)
                    first_line = line
            for mod, _, line in collector.from_imports:
                if mod.startswith("backend.") or mod.startswith("."):
                    internal_modules.add(mod)
                    first_line = line

            if len(internal_modules) > 15:
                source_lines = files.get(path, "").splitlines()
                excerpt = extract_code_excerpt(
                    source_lines,
                    first_line,
                    first_line,
                    config.max_excerpt_lines,
                )
                evidence = CodeEvidence(
                    evidence_id=f"ev-{uuid.uuid4().hex[:8]}",
                    file_path=path,
                    start_line=first_line,
                    end_line=first_line,
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
                            f"Excessive module coupling: '{path}' imports from "
                            f"{len(internal_modules)} internal modules (> 15)."
                        ),
                        category=rule.category,
                        severity=rule.severity,
                        confidence=rule.confidence,
                        file_path=path,
                        start_line=first_line,
                        end_line=first_line,
                        evidence=evidence,
                        impact=(
                            "High coupling reduces cohesion and makes files "
                            "harder to test and refactor safely."
                        ),
                        remediation=rule.remediation_guidance,
                        source="architecture_analysis",
                        requires_human_review=rule.requires_human_review,
                    )
                )

    def _load_approved_dependencies(self, repo_root: Path | str | None) -> set[str]:
        """Extract approved third-party dependencies from pyproject.toml."""
        # Built-in known common dependencies for AgentForge
        approved: set[str] = {
            "fastapi",
            "pydantic",
            "pytest",
            "docker",
            "httpx",
            "ruff",
            "mypy",
            "bandit",
            "pytest-cov",
            "anyio",
            "uvicorn",
            "starlette",
            "tomli",
            "tomllib",
            "yaml",
            "pyyaml",
        }

        if repo_root is None:
            # Check current directory
            pyproject_path = Path("pyproject.toml")
        else:
            pyproject_path = Path(repo_root) / "pyproject.toml"

        if pyproject_path.is_file():
            try:
                data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
                project = data.get("project", {})
                dependencies = project.get("dependencies", [])
                for dep in dependencies:
                    pkg = (
                        dep.split(">=")[0]
                        .split("==")[0]
                        .split("<")[0]
                        .split("~=")[0]
                        .split(";")[0]
                        .strip()
                        .lower()
                    )
                    if pkg:
                        approved.add(pkg)
            except (OSError, tomllib.TOMLDecodeError):
                return approved

        return approved
