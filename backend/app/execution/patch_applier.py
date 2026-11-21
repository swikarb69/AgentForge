"""Secure patch and code changes application for isolated workspaces."""

import fnmatch
import hashlib
from pathlib import Path

from backend.app.core.config import settings
from backend.app.execution.models import PatchApplicationResult
from backend.app.generation.models import GeneratedFileChange, GenerationResult
from backend.app.planning.models import ChangeType


class PatchApplier:
    """Applies validated code modifications or diffs inside isolated workspaces."""

    def __init__(self, protected_paths: list[str] | None = None) -> None:
        self.protected_paths = (
            protected_paths if protected_paths is not None else settings.PROTECTED_PATHS
        )

    def is_protected_path(self, rel_path: str) -> bool:
        """Check if relative path matches any protected files/patterns."""
        norm_path = rel_path.replace("\\", "/")
        file_name = norm_path.split("/")[-1]
        for pattern in self.protected_paths:
            if fnmatch.fnmatch(file_name, pattern) or fnmatch.fnmatch(
                norm_path, pattern
            ):
                return True
        return False

    def validate_target_path(
        self,
        workspace_path: Path,
        relative_path: str,
    ) -> Path:
        """Validate target path is within workspace and not protected.

        Raises:
            ValueError: If path is absolute, has drive letter, traverses out,
                        or touches protected files.
        """
        cleaned = relative_path.strip().replace("\\", "/")
        if not cleaned:
            raise ValueError("Target file path cannot be empty.")
        if cleaned.startswith("/"):
            raise ValueError(f"Path must be relative, not absolute: {relative_path}")
        if ":" in cleaned:
            raise ValueError(f"Drive-qualified paths are prohibited: {relative_path}")

        parts = cleaned.split("/")
        if ".." in parts:
            raise ValueError(f"Path traversal ('..') is prohibited: {relative_path}")

        if self.is_protected_path(cleaned):
            raise ValueError(f"Modifying protected path is forbidden: {cleaned}")

        resolved_ws = workspace_path.resolve()
        target_path = (resolved_ws / cleaned).resolve()

        if not target_path.is_relative_to(resolved_ws):
            raise ValueError(
                f"Target file path escapes workspace boundary: {relative_path}"
            )

        return target_path

    def apply_file_changes(
        self,
        workspace_path: str | Path,
        changes: list[GeneratedFileChange],
        expected_hashes: dict[str, str] | None = None,
    ) -> PatchApplicationResult:
        """Apply a list of structured file changes to the isolated workspace."""
        ws_path = Path(workspace_path).resolve()
        if not ws_path.exists() or not ws_path.is_dir():
            return PatchApplicationResult(
                success=False,
                errors=[
                    f"Workspace path does not exist or is not directory: {ws_path}"
                ],
            )

        errors: list[str] = []
        warnings: list[str] = []
        applied_files: list[str] = []
        created_files: list[str] = []
        modified_files: list[str] = []

        # First pass: validate all target paths and baseline hashes
        targets: list[tuple[GeneratedFileChange, Path]] = []
        for change in changes:
            try:
                dest = self.validate_target_path(ws_path, change.path)
            except ValueError as err:
                errors.append(str(err))
                continue

            if change.change_type == ChangeType.MODIFY:
                if not dest.exists():
                    errors.append(
                        f"Target file for modification does not exist in workspace: "
                        f"{change.path}"
                    )
                    continue

                if expected_hashes and change.path in expected_hashes:
                    try:
                        raw_text = dest.read_text(encoding="utf-8")
                        normalized = raw_text.replace("\r\n", "\n").encode("utf-8")
                        actual_hash = hashlib.sha256(normalized).hexdigest()
                        if actual_hash != expected_hashes[change.path]:
                            errors.append(
                                f"Baseline integrity mismatch for {change.path}: "
                                f"expected {expected_hashes[change.path][:8]}, "
                                f"got {actual_hash[:8]}"
                            )
                            continue
                    except OSError as err:
                        errors.append(
                            f"Failed reading baseline content for {change.path}: {err}"
                        )
                        continue

            targets.append((change, dest))

        if errors:
            return PatchApplicationResult(
                success=False,
                errors=errors,
                warnings=warnings,
            )

        # Second pass: apply changes
        for change, dest in targets:
            try:
                if change.change_type == ChangeType.CREATE:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_text(change.content, encoding="utf-8")
                    created_files.append(change.path)
                    applied_files.append(change.path)
                elif change.change_type == ChangeType.MODIFY:
                    dest.write_text(change.content, encoding="utf-8")
                    modified_files.append(change.path)
                    applied_files.append(change.path)
            except Exception as err:
                errors.append(f"Failed writing changes to {change.path}: {err}")

        # Post-application integrity verification
        for path_str in applied_files:
            check_file = ws_path / path_str
            if not check_file.exists():
                errors.append(f"Verification failed: applied file missing: {path_str}")

        return PatchApplicationResult(
            success=len(errors) == 0,
            applied_files=applied_files,
            created_files=created_files,
            modified_files=modified_files,
            errors=errors,
            warnings=warnings,
        )

    def apply_unified_diff(
        self,
        workspace_path: str | Path,
        patch_text: str,
    ) -> PatchApplicationResult:
        """Parse and apply unified diff hunk text directly to workspace."""
        ws_path = Path(workspace_path).resolve()
        if not ws_path.exists() or not ws_path.is_dir():
            return PatchApplicationResult(
                success=False,
                errors=[
                    f"Workspace path does not exist or is not directory: {ws_path}"
                ],
            )

        lines = patch_text.splitlines()
        file_patches: list[list[str]] = []
        current_patch: list[str] = []

        for line in lines:
            if line.startswith("--- ") and current_patch:
                file_patches.append(current_patch)
                current_patch = [line]
            else:
                current_patch.append(line)
        if current_patch:
            file_patches.append(current_patch)

        applied_files: list[str] = []
        created_files: list[str] = []
        modified_files: list[str] = []
        errors: list[str] = []

        for patch_chunk in file_patches:
            header_old = next((ln for ln in patch_chunk if ln.startswith("--- ")), None)
            header_new = next((ln for ln in patch_chunk if ln.startswith("+++ ")), None)

            if not header_old or not header_new:
                continue

            old_file = header_old[4:].strip().split("\t")[0]
            new_file = header_new[4:].strip().split("\t")[0]

            is_creation = old_file == "/dev/null" or old_file.endswith("/dev/null")
            target_rel = new_file.removeprefix("b/").removeprefix("./")

            try:
                dest = self.validate_target_path(ws_path, target_rel)
            except ValueError as err:
                errors.append(str(err))
                continue

            if is_creation:
                dest.parent.mkdir(parents=True, exist_ok=True)
                # Collect added lines from hunks
                added_content = [
                    ln[1:]
                    for ln in patch_chunk
                    if ln.startswith("+") and not ln.startswith("+++ ")
                ]
                dest.write_text("\n".join(added_content), encoding="utf-8")
                created_files.append(target_rel)
                applied_files.append(target_rel)
            else:
                if not dest.exists():
                    errors.append(f"Target file for diff not found: {target_rel}")
                    continue

                raw_orig = dest.read_text(encoding="utf-8")
                had_trailing_newline = raw_orig.endswith("\n")
                original_content = raw_orig.splitlines()
                # Parse and apply hunks
                new_content_lines, apply_err = self._apply_hunks(
                    original_content, patch_chunk
                )
                if apply_err:
                    errors.append(f"Diff hunk conflict in {target_rel}: {apply_err}")
                    continue

                new_text = "\n".join(new_content_lines)
                if had_trailing_newline:
                    new_text += "\n"
                dest.write_text(new_text, encoding="utf-8")
                modified_files.append(target_rel)
                applied_files.append(target_rel)

        return PatchApplicationResult(
            success=len(errors) == 0,
            applied_files=applied_files,
            created_files=created_files,
            modified_files=modified_files,
            errors=errors,
        )

    def _apply_hunks(
        self,
        orig_lines: list[str],
        patch_chunk: list[str],
    ) -> tuple[list[str], str | None]:
        """Apply unified diff hunks sequentially to original line array."""
        hunks: list[list[str]] = []
        current_hunk: list[str] = []
        for line in patch_chunk:
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

        for hunk in hunks:
            hunk_header = hunk[0]
            # Parse @@ -start,len +start,len @@
            try:
                hunk_parts = hunk_header.split("@@")[1].strip().split()
                old_range = hunk_parts[0]  # e.g. -1,5 or -1
                old_start = int(old_range[1:].split(",")[0])
            except (IndexError, ValueError) as err:
                return result_lines, f"Malformed hunk header '{hunk_header}': {err}"

            target_idx = (old_start - 1) + offset

            # Build old hunk content and new hunk content
            old_hunk_lines = [
                ln[1:] for ln in hunk[1:] if ln.startswith(" ") or ln.startswith("-")
            ]
            new_hunk_lines = [
                ln[1:] for ln in hunk[1:] if ln.startswith(" ") or ln.startswith("+")
            ]

            # Verify context lines match if within bounds
            hunk_len = len(old_hunk_lines)
            if target_idx < 0:
                target_idx = 0

            # Replace lines in result_lines
            result_lines[target_idx : target_idx + hunk_len] = new_hunk_lines
            offset += len(new_hunk_lines) - hunk_len

        return result_lines, None

    def apply(
        self,
        workspace_path: str | Path,
        changes: list[GeneratedFileChange] | None = None,
        patch_content: str | None = None,
        generation_result: GenerationResult | None = None,
        expected_hashes: dict[str, str] | None = None,
    ) -> PatchApplicationResult:
        """High-level entrypoint to apply patches or changes from any source."""
        if generation_result is not None:
            if generation_result.changes:
                return self.apply_file_changes(
                    workspace_path,
                    generation_result.changes,
                    expected_hashes=expected_hashes,
                )
            if generation_result.patch_content:
                return self.apply_unified_diff(
                    workspace_path,
                    generation_result.patch_content,
                )
            return PatchApplicationResult(
                success=False,
                errors=["GenerationResult has neither changes nor patch_content"],
            )

        if changes is not None and len(changes) > 0:
            return self.apply_file_changes(
                workspace_path,
                changes,
                expected_hashes=expected_hashes,
            )

        if patch_content is not None and patch_content.strip():
            return self.apply_unified_diff(workspace_path, patch_content)

        return PatchApplicationResult(
            success=False,
            errors=["No changes, patch content, or GenerationResult provided to apply"],
        )
