"""Unit tests for path validation and ignore filtering."""

from pathlib import Path

import pytest

from backend.app.repository.filters import (
    is_ignored_directory,
    is_ignored_file,
    validate_safe_path,
)


def test_is_ignored_directory() -> None:
    """Verify default ignored directories are matched."""
    assert is_ignored_directory(".git") is True
    assert is_ignored_directory(".venv") is True
    assert is_ignored_directory("node_modules") is True
    assert is_ignored_directory("__pycache__") is True
    assert is_ignored_directory(".pytest_cache") is True

    # Valid directories
    assert is_ignored_directory("backend") is False
    assert is_ignored_directory("src") is False
    assert is_ignored_directory("app") is False


def test_is_ignored_file() -> None:
    """Verify default ignored file extensions and filenames."""
    assert is_ignored_file("compiled.pyc") is True
    assert is_ignored_file("binary.so") is True
    assert is_ignored_file("image.png") is True
    assert is_ignored_file(".DS_Store") is True
    assert is_ignored_file("Thumbs.db") is True

    # Valid files
    assert is_ignored_file("main.py") is False
    assert is_ignored_file("README.md") is False
    assert is_ignored_file("pyproject.toml") is False


def test_validate_safe_path_success(tmp_path: Path) -> None:
    """Verify safe path inside repository root passes validation."""
    sub_dir = tmp_path / "app" / "core"
    sub_dir.mkdir(parents=True)
    target_file = sub_dir / "config.py"
    target_file.touch()

    validated = validate_safe_path(tmp_path, target_file)
    assert validated == target_file.resolve()


def test_validate_safe_path_traversal_prevention(tmp_path: Path) -> None:
    """Verify path traversal outside repository root raises ValueError."""
    outside_file = tmp_path.parent / "sensitive.txt"
    with pytest.raises(ValueError, match="Path traversal detected"):
        validate_safe_path(tmp_path, outside_file)
