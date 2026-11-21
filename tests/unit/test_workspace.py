"""Unit tests for isolated workspace manager."""

from pathlib import Path

import pytest

from backend.app.execution.workspace import WorkspaceManager


def test_workspace_manager_init(tmp_path: Path) -> None:
    """Verify WorkspaceManager initialization and custom limits."""
    mgr = WorkspaceManager(
        workspace_root=tmp_path / "workspaces", size_limit_bytes=5000
    )
    assert mgr.workspace_root == tmp_path / "workspaces"
    assert mgr.size_limit_bytes == 5000


def test_workspace_creation_and_file_copying(tmp_path: Path) -> None:
    """Verify that allowed repository files are copied faithfully into workspace."""
    source_repo = tmp_path / "source_repo"
    source_repo.mkdir()
    (source_repo / "main.py").write_text("print('hello')", encoding="utf-8")
    (source_repo / "pkg").mkdir()
    (source_repo / "pkg" / "module.py").write_text("x = 1", encoding="utf-8")

    ws_root = tmp_path / "ws_root"
    mgr = WorkspaceManager(workspace_root=ws_root)

    meta = mgr.create_workspace(source_repo, workspace_id="ws-test-01")

    assert meta.workspace_id == "ws-test-01"
    assert not meta.cleaned_up
    assert meta.file_count == 2
    assert meta.size_bytes > 0

    dest_dir = Path(meta.workspace_path)
    assert (dest_dir / "main.py").exists()
    assert (dest_dir / "pkg" / "module.py").exists()
    assert (dest_dir / "main.py").read_text(encoding="utf-8") == "print('hello')"

    # Clean up
    assert mgr.cleanup(meta)
    assert not dest_dir.exists()
    assert meta.cleaned_up


def test_workspace_exclusion_rules(tmp_path: Path) -> None:
    """Verify excluded directories and sensitive/cache files are omitted."""
    source_repo = tmp_path / "source_repo"
    source_repo.mkdir()

    # Allowed file
    (source_repo / "app.py").write_text("print(1)", encoding="utf-8")

    # Excluded directories
    (source_repo / ".git").mkdir()
    (source_repo / ".git" / "config").write_text("git config", encoding="utf-8")
    (source_repo / ".venv").mkdir()
    (source_repo / ".venv" / "pip.exe").write_text("binary", encoding="utf-8")
    (source_repo / "__pycache__").mkdir()
    (source_repo / "__pycache__" / "app.cpython-313.pyc").write_text(
        "b", encoding="utf-8"
    )

    # Excluded sensitive/cache files in root
    (source_repo / ".env").write_text("SECRET=123", encoding="utf-8")
    (source_repo / ".env.local").write_text("SECRET=456", encoding="utf-8")
    (source_repo / "id_rsa").write_text("ssh-key", encoding="utf-8")
    (source_repo / "cert.pem").write_text("certificate", encoding="utf-8")
    (source_repo / "secrets.json").write_text("{}", encoding="utf-8")
    (source_repo / "test.db").write_text("sqlite", encoding="utf-8")

    ws_root = tmp_path / "ws_root"
    mgr = WorkspaceManager(workspace_root=ws_root)
    meta = mgr.create_workspace(source_repo)

    dest_dir = Path(meta.workspace_path)
    # Only app.py should have been copied
    assert meta.file_count == 1
    assert (dest_dir / "app.py").exists()

    # Verify exclusions
    assert not (dest_dir / ".git").exists()
    assert not (dest_dir / ".venv").exists()
    assert not (dest_dir / "__pycache__").exists()
    assert not (dest_dir / ".env").exists()
    assert not (dest_dir / ".env.local").exists()
    assert not (dest_dir / "id_rsa").exists()
    assert not (dest_dir / "cert.pem").exists()
    assert not (dest_dir / "secrets.json").exists()
    assert not (dest_dir / "test.db").exists()

    mgr.cleanup(meta)


def test_workspace_size_limit_exceeded(tmp_path: Path) -> None:
    """Verify size limit aborts creation and removes partial workspace."""
    source_repo = tmp_path / "source_repo"
    source_repo.mkdir()
    (source_repo / "large.bin").write_text("A" * 10_000, encoding="utf-8")

    ws_root = tmp_path / "ws_root"
    mgr = WorkspaceManager(workspace_root=ws_root, size_limit_bytes=5_000)

    with pytest.raises(ValueError, match="Workspace size exceeds limit"):
        mgr.create_workspace(source_repo, workspace_id="ws-oversized")

    # Verify no leaked partial directory
    assert not (ws_root / "ws-oversized").exists()


def test_workspace_source_validation(tmp_path: Path) -> None:
    """Verify invalid source paths raise proper exceptions."""
    mgr = WorkspaceManager(workspace_root=tmp_path / "ws_root")

    with pytest.raises(FileNotFoundError):
        mgr.create_workspace(tmp_path / "nonexistent_dir")

    regular_file = tmp_path / "file.txt"
    regular_file.write_text("hello", encoding="utf-8")
    with pytest.raises(NotADirectoryError):
        mgr.create_workspace(regular_file)


def test_workspace_context_manager(tmp_path: Path) -> None:
    """Verify synchronous context manager lifecycle and auto cleanup."""
    source_repo = tmp_path / "source_repo"
    source_repo.mkdir()
    (source_repo / "code.py").write_text("val = 42", encoding="utf-8")

    ws_root = tmp_path / "ws_root"
    mgr = WorkspaceManager(workspace_root=ws_root)

    dest_path: Path | None = None
    with mgr.session(source_repo, workspace_id="ws-sync-ctx") as meta:
        assert meta.workspace_id == "ws-sync-ctx"
        dest_path = Path(meta.workspace_path)
        assert dest_path.exists()
        assert (dest_path / "code.py").exists()

    # Automatically cleaned up after exiting context
    assert dest_path is not None
    assert not dest_path.exists()


@pytest.mark.anyio
async def test_workspace_async_context_manager(tmp_path: Path) -> None:
    """Verify asynchronous context manager lifecycle and auto cleanup."""
    source_repo = tmp_path / "source_repo"
    source_repo.mkdir()
    (source_repo / "async_code.py").write_text("async_val = 1", encoding="utf-8")

    ws_root = tmp_path / "ws_root"
    mgr = WorkspaceManager(workspace_root=ws_root)

    dest_path: Path | None = None
    async with mgr.async_session(source_repo, workspace_id="ws-async-ctx") as meta:
        assert meta.workspace_id == "ws-async-ctx"
        dest_path = Path(meta.workspace_path)
        assert dest_path.exists()
        assert (dest_path / "async_code.py").exists()

    assert dest_path is not None
    assert not dest_path.exists()
