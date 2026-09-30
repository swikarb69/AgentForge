"""Language detection and file metadata classification logic."""

from pathlib import Path

EXTENSION_LANGUAGE_MAP: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".go": "go",
    ".rs": "rust",
    ".md": "markdown",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".css": "css",
    ".html": "html",
    ".htm": "html",
    ".sh": "bash",
    ".bash": "bash",
    ".sql": "sql",
}


def detect_language(file_extension: str) -> str:
    """Return language string based on file extension, or 'unknown' if unmatched."""
    ext = file_extension.lower()
    if not ext.startswith(".") and ext:
        ext = f".{ext}"
    return EXTENSION_LANGUAGE_MAP.get(ext, "unknown")


def is_test_file(relative_path: str) -> bool:
    """Determine if a file relative path represents a test file."""
    normalized_path = relative_path.replace("\\", "/").lower()
    file_name = Path(normalized_path).name

    # Check directory components
    path_parts = [p.strip() for p in normalized_path.split("/") if p.strip()]
    if any(
        part in {"test", "tests", "__tests__", "spec", "specs"}
        for part in path_parts[:-1]
    ):
        return True

    # Check file naming conventions
    if file_name.startswith("test_") or file_name.endswith("_test.py"):
        return True

    test_suffixes = (
        ".test.js",
        ".spec.js",
        ".test.jsx",
        ".spec.jsx",
        ".test.ts",
        ".spec.ts",
        ".test.tsx",
        ".spec.tsx",
    )
    return file_name.endswith(test_suffixes)
