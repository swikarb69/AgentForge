"""Recursive repository scanner implementation."""

import os
from pathlib import Path

from backend.app.core.config import settings
from backend.app.repository.filters import (
    is_ignored_directory,
    is_ignored_file,
    load_gitignore_patterns,
    matches_gitignore,
    validate_safe_path,
)
from backend.app.repository.metadata import detect_language, is_test_file
from backend.app.repository.models import RepositoryFile


class RepositoryScanner:
    """Recursively inspects repository directories to gather file metadata."""

    def __init__(self, max_file_size: int = settings.MAX_FILE_SIZE) -> None:
        self.max_file_size = max_file_size

    def scan(self, repository_path: str | Path) -> list[RepositoryFile]:
        """Scan target repository path recursively and return list of file metadata."""
        root_path = Path(repository_path).resolve()

        if not root_path.exists():
            raise FileNotFoundError(
                f"Repository path does not exist: '{repository_path}'"
            )
        if not root_path.is_dir():
            raise NotADirectoryError(
                f"Repository path is not a directory: '{repository_path}'"
            )

        gitignore_patterns = load_gitignore_patterns(root_path)
        discovered_files: list[RepositoryFile] = []

        for current_root, dir_names, file_names in os.walk(
            root_path, topdown=True, followlinks=False
        ):
            # Prune ignored directories in-place
            dir_names[:] = [
                d
                for d in dir_names
                if not is_ignored_directory(d)
                and not matches_gitignore(
                    str(Path(current_root, d).relative_to(root_path)),
                    gitignore_patterns,
                )
            ]

            for file_name in file_names:
                file_path = Path(current_root) / file_name

                try:
                    validate_safe_path(root_path, file_path)
                except ValueError:
                    continue

                rel_path = str(file_path.relative_to(root_path)).replace("\\", "/")

                if is_ignored_file(file_name) or matches_gitignore(
                    rel_path, gitignore_patterns
                ):
                    continue

                file_ext = file_path.suffix
                language = detect_language(file_ext)
                is_test = is_test_file(rel_path)

                size_bytes = 0
                line_count = 0
                parse_status = "success"
                error_msg: str | None = None

                try:
                    stat_info = file_path.stat()
                    size_bytes = stat_info.st_size

                    if size_bytes > self.max_file_size:
                        parse_status = "skipped"
                        msg_limit = self.max_file_size
                        error_msg = (
                            f"File size ({size_bytes}B) exceeds limit ({msg_limit}B)"
                        )
                    else:
                        line_count, is_binary = self._read_file_stats(file_path)
                        if is_binary:
                            parse_status = "skipped"
                            error_msg = "Binary file detected"
                except Exception as err:
                    parse_status = "failed"
                    error_msg = f"File read error: {err}"

                file_meta = RepositoryFile(
                    relative_path=rel_path,
                    file_extension=file_ext,
                    language=language,
                    size_bytes=size_bytes,
                    line_count=line_count,
                    is_test=is_test,
                    is_ignored=False,
                    parse_status=parse_status,
                    error_message=error_msg,
                )

                discovered_files.append(file_meta)

        return discovered_files

    @staticmethod
    def _read_file_stats(file_path: Path) -> tuple[int, bool]:
        """Read line count and detect binary encoding for a file."""
        try:
            with open(file_path, "rb") as f:
                chunk = f.read(1024)
                if b"\x00" in chunk:
                    return 0, True

            with open(file_path, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
                return len(lines), False
        except Exception:
            return 0, True
