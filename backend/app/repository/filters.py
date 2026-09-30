"""Path validation and exclusion rules for repository scanning."""

import fnmatch
from pathlib import Path

DEFAULT_IGNORED_DIRECTORIES: set[str] = {
    ".git",
    ".github",
    ".venv",
    "venv",
    "env",
    "ENV",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".coverage",
    "htmlcov",
    "node_modules",
    "dist",
    "build",
    "target",
    ".vscode",
    ".idea",
    ".scratch",
}

DEFAULT_IGNORED_EXTENSIONS: set[str] = {
    ".pyc",
    ".pyo",
    ".pyd",
    ".so",
    ".dll",
    ".dylib",
    ".exe",
    ".bin",
    ".obj",
    ".o",
    ".a",
    ".lib",
    ".ds_store",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".svg",
    ".pdf",
    ".zip",
    ".tar",
    ".gz",
    ".7z",
    ".rar",
    ".mp3",
    ".mp4",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
}


def validate_safe_path(base_path: Path, target_path: Path) -> Path:
    """Validate target_path is inside base_path to prevent path traversal.

    Raises:
        ValueError: If target_path escapes base_path.
    """
    resolved_base = base_path.resolve()
    resolved_target = target_path.resolve()

    try:
        resolved_target.relative_to(resolved_base)
    except ValueError as err:
        raise ValueError(
            f"Path traversal detected: '{target_path}' is outside root '{base_path}'"
        ) from err

    return resolved_target


def is_ignored_directory(dir_name: str) -> bool:
    """Return True if directory name matches default ignore lists."""
    return dir_name.lower() in {d.lower() for d in DEFAULT_IGNORED_DIRECTORIES}


def is_ignored_file(file_name: str) -> bool:
    """Return True if file extension or file name matches default ignore lists."""
    name_lower = file_name.lower()
    if name_lower == ".ds_store" or name_lower == "thumbs.db":
        return True

    ext = Path(file_name).suffix.lower()
    return ext in DEFAULT_IGNORED_EXTENSIONS


def load_gitignore_patterns(repo_root: Path) -> list[str]:
    """Load ignore patterns from repository .gitignore file if present."""
    gitignore_file = repo_root / ".gitignore"
    patterns: list[str] = []

    if not gitignore_file.is_file():
        return patterns

    try:
        with open(gitignore_file, encoding="utf-8", errors="ignore") as f:
            for line in f:
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    patterns.append(stripped)
    except OSError:
        pass

    return patterns


def matches_gitignore(relative_path: str, patterns: list[str]) -> bool:
    """Check if relative_path matches any loaded .gitignore glob pattern."""
    path_obj = Path(relative_path)
    parts = path_obj.parts

    for pattern in patterns:
        clean_pat = pattern.rstrip("/")
        # Check against relative path or filename
        if fnmatch.fnmatch(relative_path, clean_pat) or fnmatch.fnmatch(
            path_obj.name, clean_pat
        ):
            return True
        for part in parts:
            if fnmatch.fnmatch(part, clean_pat):
                return True
    return False
