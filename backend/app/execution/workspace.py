"""Isolated ephemeral workspace manager for sandboxed execution."""

import fnmatch
import os
import shutil
import stat
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager, suppress
from pathlib import Path

from backend.app.core.config import settings
from backend.app.execution.models import WorkspaceMetadata

EXCLUDED_DIR_NAMES: set[str] = {
    ".git",
    ".github",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    "build",
    "dist",
    ".idea",
    ".vscode",
    "node_modules",
}

EXCLUDED_FILE_PATTERNS: tuple[str, ...] = (
    "*.pyc",
    "*.pyo",
    "*.pyd",
    ".DS_Store",
    "Thumbs.db",
    ".env*",
    "*.pem",
    "*.key",
    "*.secret",
    "*.db",
    "*.sqlite",
    "id_rsa",
    "id_ed25519",
    "credentials.json",
    "secrets.json",
)


def _remove_readonly(func, path, exc_info) -> None:  # type: ignore[no-untyped-def]
    """Clear readonly bit and re-attempt file deletion (Windows compatibility)."""
    with suppress(Exception):
        os.chmod(path, stat.S_IWRITE)
        func(path)


class WorkspaceManager:
    """Creates, isolates, tracks, and cleans up execution workspaces."""

    def __init__(
        self,
        workspace_root: str | Path | None = None,
        size_limit_bytes: int | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root or settings.WORKSPACE_ROOT)
        self.size_limit_bytes = (
            size_limit_bytes
            if size_limit_bytes is not None
            else settings.WORKSPACE_SIZE_LIMIT
        )

    def is_excluded_dir(self, dir_name: str) -> bool:
        """Check if a directory name should be excluded from workspace."""
        return dir_name in EXCLUDED_DIR_NAMES

    def is_excluded_file(self, file_name: str) -> bool:
        """Check if a file matches any excluded security or cache patterns."""
        for pattern in EXCLUDED_FILE_PATTERNS:
            if fnmatch.fnmatch(file_name, pattern):
                return True
        return False

    def create_workspace(
        self,
        repo_path: str | Path,
        workspace_id: str | None = None,
    ) -> WorkspaceMetadata:
        """Create an ephemeral workspace populated with a sanitized copy of repo.

        Args:
            repo_path: Path to the source repository.
            workspace_id: Optional custom identifier.

        Returns:
            WorkspaceMetadata describing the created workspace.

        Raises:
            FileNotFoundError: If repo_path does not exist.
            NotADirectoryError: If repo_path is not a directory.
            ValueError: If workspace size exceeds permitted limit.
        """
        source = Path(repo_path).resolve()
        if not source.exists():
            raise FileNotFoundError(f"Source repository path not found: {source}")
        if not source.is_dir():
            raise NotADirectoryError(f"Source repository is not a directory: {source}")

        ws_id = workspace_id or f"ws-{uuid.uuid4().hex[:12]}"
        dest_dir = (self.workspace_root / ws_id).resolve()
        dest_dir.mkdir(parents=True, exist_ok=True)

        total_files = 0
        total_bytes = 0

        try:
            for root, dirs, files in os.walk(source):
                # Prune excluded directories in-place so os.walk does not descend
                dirs[:] = [d for d in dirs if not self.is_excluded_dir(d)]

                rel_root = Path(root).relative_to(source)
                target_sub_dir = dest_dir / rel_root
                target_sub_dir.mkdir(parents=True, exist_ok=True)

                for file in files:
                    if self.is_excluded_file(file):
                        continue

                    source_file = Path(root) / file
                    # Skip symlinks pointing outside for security
                    if source_file.is_symlink():
                        try:
                            resolved_link = source_file.resolve()
                            if not resolved_link.is_relative_to(source):
                                continue
                        except (ValueError, RuntimeError):
                            continue

                    try:
                        file_size = source_file.stat().st_size
                    except OSError:
                        file_size = 0

                    if total_bytes + file_size > self.size_limit_bytes:
                        raise ValueError(
                            f"Workspace size exceeds limit of {self.size_limit_bytes} "
                            f"bytes (reached {total_bytes + file_size} bytes)."
                        )

                    dest_file = target_sub_dir / file
                    shutil.copy2(source_file, dest_file)
                    total_files += 1
                    total_bytes += file_size

        except Exception:
            # Clean up partial directory on failure
            self.cleanup(dest_dir)
            raise

        return WorkspaceMetadata(
            workspace_id=ws_id,
            workspace_path=str(dest_dir),
            file_count=total_files,
            size_bytes=total_bytes,
        )

    def cleanup(self, workspace: WorkspaceMetadata | str | Path) -> bool:
        """Recursively delete the workspace directory.

        Returns True if deleted or did not exist, False on failure.
        """
        if isinstance(workspace, WorkspaceMetadata):
            target_path = Path(workspace.workspace_path)
        else:
            target_path = Path(workspace)

        if not target_path.exists():
            if isinstance(workspace, WorkspaceMetadata):
                workspace.cleaned_up = True
            return True

        try:
            shutil.rmtree(target_path, onerror=_remove_readonly)
            if isinstance(workspace, WorkspaceMetadata):
                workspace.cleaned_up = True
            return True
        except Exception:
            return False

    @contextmanager
    def session(
        self,
        repo_path: str | Path,
        workspace_id: str | None = None,
        auto_cleanup: bool = True,
    ) -> Iterator[WorkspaceMetadata]:
        """Synchronous context manager creating and optionally cleaning up workspace."""
        meta = self.create_workspace(repo_path, workspace_id)
        try:
            yield meta
        finally:
            if auto_cleanup:
                self.cleanup(meta)

    @asynccontextmanager
    async def async_session(
        self,
        repo_path: str | Path,
        workspace_id: str | None = None,
        auto_cleanup: bool = True,
    ) -> AsyncIterator[WorkspaceMetadata]:
        """Async context manager creating and optionally cleaning up workspace."""
        meta = self.create_workspace(repo_path, workspace_id)
        try:
            yield meta
        finally:
            if auto_cleanup:
                self.cleanup(meta)
