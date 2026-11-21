"""Patch and change validator for generated code proposals."""

import ast
from pathlib import Path

from backend.app.core.config import settings
from backend.app.generation.models import (
    GeneratedFileChange,
    GenerationStatus,
    PatchValidation,
    SourceContext,
)
from backend.app.planning.models import ChangeType, ImplementationPlan


class PatchValidator:
    """Validates proposed code changes against authorization, safety, and syntax."""

    def __init__(
        self,
        max_output_bytes: int = settings.MAX_GENERATED_OUTPUT,
        protected_paths: list[str] | None = None,
    ) -> None:
        """Initialize validator with output size and protected path configurations."""
        self.max_output_bytes = max_output_bytes
        self.protected_paths = (
            protected_paths
            if protected_paths is not None
            else list(settings.PROTECTED_PATHS)
        )

    def _normalize_path(self, path: str) -> str:
        """Normalize repository-relative path to forward slashes."""
        return path.strip().replace("\\", "/").lstrip("/")

    def _is_protected(self, norm_path: str) -> bool:
        """Check if relative path matches any protected path pattern."""
        lower_path = norm_path.lower()
        parts = lower_path.split("/")
        for prot in self.protected_paths:
            prot_clean = prot.lower().strip().replace("\\", "/").lstrip("/")
            if not prot_clean:
                continue
            if prot_clean in parts:
                return True
            if lower_path == prot_clean or lower_path.startswith(f"{prot_clean}/"):
                return True
            if lower_path.endswith(f"/{prot_clean}"):
                return True
        return False

    def validate(
        self,
        changes: list[GeneratedFileChange],
        plan: ImplementationPlan,
        context: SourceContext,
    ) -> PatchValidation:
        """Perform comprehensive validation on proposed file changes."""
        errors: list[str] = []
        warnings: list[str] = []
        files_checked: list[str] = []
        plan_scope_violations: list[str] = []
        path_safety_findings: list[str] = []

        authorized_files = set(context.target_files.keys())
        repo_root = Path(context.repository_path).resolve()

        if not changes:
            warnings.append("No file changes were proposed in generation.")

        for change in changes:
            norm_path = self._normalize_path(change.path)
            files_checked.append(norm_path)

            # 1. Path Safety Checks
            if ":" in norm_path:
                msg = f"Drive-qualified path prohibited: '{norm_path}'"
                path_safety_findings.append(msg)
                errors.append(msg)

            if ".." in norm_path.split("/"):
                msg = f"Path traversal ('..') prohibited: '{norm_path}'"
                path_safety_findings.append(msg)
                errors.append(msg)

            if self._is_protected(norm_path):
                msg = f"Change proposed for protected path: '{norm_path}'"
                path_safety_findings.append(msg)
                errors.append(msg)

            target_abs = (repo_root / norm_path).resolve()
            try:
                target_abs.relative_to(repo_root)
            except ValueError:
                msg = f"Path escapes repository boundary: '{norm_path}'"
                path_safety_findings.append(msg)
                errors.append(msg)

            # 2. Plan Authorization Check
            if norm_path not in authorized_files:
                msg = (
                    f"File '{norm_path}' is not authorized by the implementation plan. "
                    f"Authorized files: {sorted(authorized_files)}"
                )
                plan_scope_violations.append(msg)
                errors.append(msg)

            # 3. Change Type Validation
            if change.change_type == ChangeType.DELETE:
                msg = f"File deletion is prohibited: '{norm_path}'"
                errors.append(msg)

            # 4. Content Checks
            if not change.content.strip():
                errors.append(f"Proposed file content is empty for '{norm_path}'")

            content_bytes = len(change.content.encode("utf-8"))
            if content_bytes > self.max_output_bytes:
                errors.append(
                    f"Content for '{norm_path}' ({content_bytes} bytes) exceeds "
                    f"maximum output limit ({self.max_output_bytes} bytes)"
                )

            # 5. Python Syntax Validation
            if norm_path.endswith(".py"):
                try:
                    ast.parse(change.content, filename=norm_path)
                except SyntaxError as syntax_err:
                    errors.append(
                        f"Python syntax error in '{norm_path}' at line "
                        f"{syntax_err.lineno}: {syntax_err.msg}"
                    )

        is_valid = len(errors) == 0
        status = GenerationStatus.VALIDATED if is_valid else GenerationStatus.REJECTED

        return PatchValidation(
            valid=is_valid,
            status=status,
            errors=errors,
            warnings=warnings,
            files_checked=files_checked,
            plan_scope_violations=plan_scope_violations,
            path_safety_findings=path_safety_findings,
        )
