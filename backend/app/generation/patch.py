"""Unified diff patch generator and artifact management."""

import difflib
from datetime import UTC, datetime
from pathlib import Path

from backend.app.core.config import settings
from backend.app.generation.models import GeneratedFileChange, SourceContext


class PatchBuilder:
    """Builds unified diffs from proposed file changes and persists patch artifacts."""

    def __init__(
        self,
        artifact_dir: str = settings.GENERATION_ARTIFACT_DIR,
    ) -> None:
        """Initialize patch builder with configured artifact directory."""
        self.artifact_dir = Path(artifact_dir)

    def build_diff(
        self,
        change: GeneratedFileChange,
        context: SourceContext,
    ) -> str:
        """Create unified diff for a single file change."""
        norm_path = change.path.strip().replace("\\", "/").lstrip("/")
        original_content = ""
        if norm_path in context.target_files:
            original_content = context.target_files[norm_path].original_content

        original_lines = (
            original_content.splitlines(keepends=True) if original_content else []
        )
        new_lines = change.content.splitlines(keepends=True) if change.content else []

        fromfile = f"a/{norm_path}" if original_content else "/dev/null"
        tofile = f"b/{norm_path}"

        diff_lines = list(
            difflib.unified_diff(
                original_lines,
                new_lines,
                fromfile=fromfile,
                tofile=tofile,
                lineterm="\n",
            )
        )
        return "".join(diff_lines)

    def build_patch(
        self,
        generation_id: str,
        plan_id: str,
        changes: list[GeneratedFileChange],
        context: SourceContext,
    ) -> str:
        """Create complete unified diff patch with auditable header."""
        header = [
            "# AgentForge Unified Diff Patch",
            f"# Generation ID: {generation_id}",
            f"# Plan ID: {plan_id}",
            f"# Repository: {context.repository_path}",
            f"# Timestamp: {datetime.now(UTC).isoformat()}",
            f"# Total Files: {len(changes)}",
            "",
        ]
        diff_blocks = []
        for change in changes:
            diff_text = self.build_diff(change, context)
            if diff_text:
                diff_blocks.append(diff_text)

        return "\n".join(header) + "\n" + "\n".join(diff_blocks)

    def save_artifact(
        self,
        generation_id: str,
        patch_content: str,
    ) -> Path:
        """Save patch content to the reviewable artifact directory."""
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = self.artifact_dir / f"{generation_id}.patch"
        artifact_path.write_text(patch_content, encoding="utf-8")
        return artifact_path
