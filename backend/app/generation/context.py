"""Bounded source context builder for code generation."""

import hashlib
from pathlib import Path
from typing import Any

from backend.app.core.config import settings
from backend.app.generation.models import FileContext, SourceContext
from backend.app.planning.models import ChangeType, ImplementationPlan


class SourceContextBuilder:
    """Extracts bounded, safe source context for an implementation plan."""

    def __init__(
        self,
        max_context_size: int = settings.MAX_CONTEXT_SIZE,
        max_files: int = settings.MAX_FILES_PER_REQUEST,
        protected_paths: list[str] | None = None,
    ) -> None:
        """Initialize builder with security and size thresholds."""
        self.max_context_size = max_context_size
        self.max_files = max_files
        self.protected_paths = (
            protected_paths
            if protected_paths is not None
            else list(settings.PROTECTED_PATHS)
        )

    def _normalize_path(self, path: str) -> str:
        """Normalize repository-relative path to forward slashes."""
        return path.strip().replace("\\", "/").lstrip("/")

    def _is_protected(self, normalized_path: str) -> bool:
        """Check if relative path matches any protected path pattern."""
        lower_path = normalized_path.lower()
        parts = lower_path.split("/")

        for protected in self.protected_paths:
            prot_lower = protected.lower().strip().replace("\\", "/").lstrip("/")
            if not prot_lower:
                continue
            if prot_lower in parts:
                return True
            if lower_path == prot_lower or lower_path.startswith(f"{prot_lower}/"):
                return True
            if lower_path.endswith(f"/{prot_lower}"):
                return True

        return False

    def build_context(
        self,
        repository_path: str,
        plan: ImplementationPlan,
    ) -> SourceContext:
        """Build bounded source context for the authorized files in plan."""
        errors: list[str] = []
        warnings: list[str] = []
        target_files: dict[str, FileContext] = {}
        reference_files: dict[str, str] = {}
        total_bytes = 0

        repo_root = Path(repository_path)
        if not repo_root.exists() or not repo_root.is_dir():
            return SourceContext(
                repository_path=repository_path,
                target_files={},
                reference_files={},
                total_bytes=0,
                warnings=warnings,
                errors=[
                    "Repository path does not exist or is not a directory: "
                    f"{repository_path}"
                ],
            )

        resolved_repo_root = repo_root.resolve()

        # 1. Reject prohibited deletions
        if plan.files_to_delete:
            deleted_paths = [f.path for f in plan.files_to_delete]
            errors.append(f"File deletion is prohibited: {deleted_paths}")

        # 2. Collect authorized target files with change types and symbols
        authorized_entries: dict[str, dict[str, Any]] = {}

        def record_entry(
            raw_path: str, change_type: ChangeType, symbols: list[str] | None = None
        ) -> None:
            norm = self._normalize_path(raw_path)
            if not norm:
                return
            if norm not in authorized_entries:
                authorized_entries[norm] = {
                    "change_type": change_type,
                    "symbols": set(symbols or []),
                }
            else:
                if symbols:
                    authorized_entries[norm]["symbols"].update(symbols)

        for fc in plan.files_to_modify:
            record_entry(fc.path, ChangeType.MODIFY, fc.affected_symbols)

        for fc in plan.files_to_create:
            record_entry(fc.path, ChangeType.CREATE, fc.affected_symbols)

        for test_path in plan.test_plan.tests_to_modify:
            record_entry(test_path, ChangeType.MODIFY)

        for test_path in plan.test_plan.tests_to_add:
            record_entry(test_path, ChangeType.CREATE)

        for task in plan.tasks:
            t_type = (
                ChangeType.CREATE
                if task.change_type == ChangeType.CREATE
                else ChangeType.MODIFY
            )
            for fpath in task.target_files:
                record_entry(fpath, t_type, task.target_symbols)

        # Merge explicit symbol changes
        for sc in plan.symbol_changes:
            norm = self._normalize_path(sc.file_path)
            if norm in authorized_entries:
                authorized_entries[norm]["symbols"].add(sc.symbol_name)

        # 3. Check target file count limit
        if len(authorized_entries) > self.max_files:
            errors.append(
                f"Target file count ({len(authorized_entries)}) exceeds "
                f"maximum allowed limit ({self.max_files})"
            )

        # 4. Process each authorized target file
        for norm_path, entry in authorized_entries.items():
            change_type = entry["change_type"]
            symbols = sorted(entry["symbols"])

            # Validate path safety
            if ":" in norm_path:
                errors.append(f"Drive-qualified paths are prohibited: '{norm_path}'")
                continue
            if ".." in norm_path.split("/"):
                errors.append(f"Path traversal ('..') is prohibited: '{norm_path}'")
                continue

            if self._is_protected(norm_path):
                errors.append(f"Target file '{norm_path}' is a protected path")
                continue

            target_abs = (resolved_repo_root / norm_path).resolve()
            try:
                target_abs.relative_to(resolved_repo_root)
            except ValueError:
                errors.append(f"Target file '{norm_path}' escapes repository boundary")
                continue

            # Read or initialize file context
            if change_type == ChangeType.CREATE:
                if target_abs.exists():
                    warnings.append(
                        f"File '{norm_path}' marked for creation already exists"
                    )
                    try:
                        content = target_abs.read_text(encoding="utf-8")
                        content_bytes = content.encode("utf-8")
                        file_bytes = len(content_bytes)
                        file_hash = hashlib.sha256(content_bytes).hexdigest()
                        exists = True
                    except Exception as err:
                        errors.append(
                            f"Unable to read existing file '{norm_path}': {err}"
                        )
                        continue
                else:
                    content = ""
                    file_bytes = 0
                    file_hash = None
                    exists = False
            else:
                if not target_abs.exists():
                    errors.append(
                        f"Target file for modification not found: '{norm_path}'"
                    )
                    continue
                if target_abs.is_dir():
                    errors.append(
                        f"Target path is a directory, not a file: '{norm_path}'"
                    )
                    continue

                try:
                    content = target_abs.read_text(encoding="utf-8")
                    content_bytes = content.encode("utf-8")
                    file_bytes = len(content_bytes)
                    file_hash = hashlib.sha256(content_bytes).hexdigest()
                    exists = True
                except UnicodeDecodeError:
                    errors.append(
                        f"Unable to decode text from binary file: '{norm_path}'"
                    )
                    continue
                except Exception as err:
                    errors.append(f"Failed reading target file '{norm_path}': {err}")
                    continue

            # Context size check
            if total_bytes + file_bytes > self.max_context_size:
                errors.append(
                    f"Total context size ({total_bytes + file_bytes} bytes) exceeds "
                    f"maximum limit ({self.max_context_size} bytes)"
                )
                break

            total_bytes += file_bytes
            target_files[norm_path] = FileContext(
                path=norm_path,
                change_type=change_type,
                original_content=content,
                original_hash=file_hash,
                size_bytes=file_bytes,
                target_symbols=symbols,
                exists=exists,
            )

        # 5. Collect bounded reference files (e.g. existing relevant tests)
        for raw_ref in plan.test_plan.existing_tests:
            norm_ref = self._normalize_path(raw_ref)
            if not norm_ref or norm_ref in target_files:
                continue
            if ":" in norm_ref or ".." in norm_ref.split("/"):
                continue
            if self._is_protected(norm_ref):
                continue

            ref_abs = (resolved_repo_root / norm_ref).resolve()
            try:
                ref_abs.relative_to(resolved_repo_root)
            except ValueError:
                continue

            if ref_abs.exists() and ref_abs.is_file():
                try:
                    ref_content = ref_abs.read_text(encoding="utf-8")
                    ref_bytes = len(ref_content.encode("utf-8"))
                    if total_bytes + ref_bytes <= self.max_context_size:
                        reference_files[norm_ref] = ref_content
                        total_bytes += ref_bytes
                    else:
                        warnings.append(
                            f"Reference file '{norm_ref}' omitted to stay "
                            "within context size limit"
                        )
                except Exception as err:
                    warnings.append(
                        f"Could not read reference file '{norm_ref}': {err}"
                    )

        return SourceContext(
            repository_path=str(resolved_repo_root),
            target_files=target_files,
            reference_files=reference_files,
            total_bytes=total_bytes,
            warnings=warnings,
            errors=errors,
        )
