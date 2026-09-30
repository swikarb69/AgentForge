"""Unit tests for RepositoryScanner."""

from pathlib import Path

import pytest

from backend.app.repository.scanner import RepositoryScanner


def test_scanner_discovers_files(tmp_path: Path) -> None:
    """Verify scanner recursively discovers valid source files."""
    # Create sample structure
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text("print('hello')", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_main.py").write_text(
        "def test_foo(): pass", encoding="utf-8"
    )
    (tmp_path / "README.md").write_text("# Project", encoding="utf-8")

    # Create ignored folder
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "cached.pyc").write_bytes(b"\x00\x01\x02")

    scanner = RepositoryScanner()
    files = scanner.scan(tmp_path)

    relative_paths = [f.relative_path for f in files]
    assert "app/main.py" in relative_paths
    assert "tests/test_main.py" in relative_paths
    assert "README.md" in relative_paths

    # Verify pycache files are excluded
    assert not any("__pycache__" in p for p in relative_paths)


def test_scanner_invalid_paths() -> None:
    """Verify scanner raises appropriate errors for non-existent or file paths."""
    scanner = RepositoryScanner()

    with pytest.raises(FileNotFoundError):
        scanner.scan("/non/existent/path/for/agentforge")

    with pytest.raises(NotADirectoryError):
        scanner.scan(Path(__file__))
