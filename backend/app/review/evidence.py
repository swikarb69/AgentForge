"""Evidence collection, verification, and binding utilities for code review."""

import hashlib
import uuid
from pathlib import Path

from backend.app.review.models import CodeEvidence, redact_secrets


def compute_content_hash(text: str) -> str:
    """Calculate SHA-256 hash of reviewed code content."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class EvidenceCollector:
    """Production utility for extracting and validating bounded code evidence."""

    @staticmethod
    def extract_excerpt(
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

    @classmethod
    def build_evidence(
        cls,
        file_path: str,
        start_line: int,
        end_line: int,
        source_lines: list[str],
        related_rule: str | None = None,
        source_artifact: str | None = None,
        max_lines: int = 15,
    ) -> CodeEvidence:
        """Construct sanitized and bounded CodeEvidence from source lines."""
        raw_excerpt = cls.extract_excerpt(source_lines, start_line, end_line, max_lines)
        redacted = redact_secrets(raw_excerpt)
        content_hash = compute_content_hash(redacted)

        return CodeEvidence(
            evidence_id=f"ev-{uuid.uuid4().hex[:8]}",
            file_path=file_path,
            start_line=start_line,
            end_line=end_line,
            code_excerpt=redacted,
            related_rule=related_rule,
            source_artifact=source_artifact,
            content_hash=content_hash,
        )

    @staticmethod
    def build_synthetic_evidence(
        file_path: str,
        description: str,
        related_rule: str | None = None,
        source_artifact: str | None = None,
    ) -> CodeEvidence:
        """Construct synthetic evidence for non-line-specific or artifact findings."""
        redacted = redact_secrets(description)
        content_hash = compute_content_hash(redacted)

        return CodeEvidence(
            evidence_id=f"ev-{uuid.uuid4().hex[:8]}",
            file_path=file_path,
            start_line=1,
            end_line=1,
            code_excerpt=redacted,
            related_rule=related_rule,
            source_artifact=source_artifact,
            content_hash=content_hash,
        )

    @staticmethod
    def verify_file_exists(file_path: str, repo_root: Path | str | None = None) -> bool:
        """Check whether a referenced file actually exists in the workspace."""
        if repo_root is None:
            return Path(file_path).is_file()
        return (Path(repo_root) / file_path).is_file()
