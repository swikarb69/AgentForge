"""Adversarial regression test suite for workspace path traversal
and isolation boundaries.
"""

import os
from pathlib import Path

import pytest

from backend.app.execution.exceptions import WorkspaceSecurityError
from backend.app.execution.gatekeeper import SandboxContainerPayloadValidator
from backend.app.execution.workspace import WorkspaceManager


@pytest.fixture
def sample_repo(tmp_path: Path) -> Path:
    """Create a minimal valid source repository."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("print('hello')", encoding="utf-8")
    (repo / "test_main.py").write_text("def test(): pass", encoding="utf-8")
    return repo


@pytest.fixture
def workspace_root(tmp_path: Path) -> Path:
    """Create an isolated workspace root."""
    root = tmp_path / "workspaces"
    root.mkdir()
    return root


# ---------------------------------------------------------------------------
# 1. Path Traversal & Identifier Validation Tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_id",
    [
        "../escaped",
        "..\\escaped",
        "../../etc",
        "sub/../../escaped",
        "a/b",
        "a\\b",
        "/root",
        "\\root",
    ],
)
def test_create_workspace_rejects_path_traversal_ids(
    sample_repo: Path, workspace_root: Path, bad_id: str
) -> None:
    """Verify create_workspace rejects workspace_id with traversal or separators."""
    mgr = WorkspaceManager(workspace_root=workspace_root)
    with pytest.raises(
        WorkspaceSecurityError, match="Path traversal or separator detected"
    ):
        mgr.create_workspace(sample_repo, workspace_id=bad_id)


def test_create_workspace_rejects_null_bytes(
    sample_repo: Path, workspace_root: Path
) -> None:
    """Verify create_workspace rejects workspace_id containing null bytes."""
    mgr = WorkspaceManager(workspace_root=workspace_root)
    with pytest.raises(WorkspaceSecurityError, match="Null byte detected"):
        mgr.create_workspace(sample_repo, workspace_id="ws-null\x00escape")


# ---------------------------------------------------------------------------
# 2. Gatekeeper Workspace Confinement & Validation Tests
# ---------------------------------------------------------------------------


def test_validate_workspace_path_accepts_valid_sub_workspace(
    workspace_root: Path,
) -> None:
    """Verify a valid existing sub-directory inside workspace_root is accepted."""
    sub_ws = workspace_root / "ws-valid-01"
    sub_ws.mkdir()

    validated = SandboxContainerPayloadValidator.validate_workspace_path(
        sub_ws, workspace_root=workspace_root
    )
    assert validated == sub_ws.resolve()


def test_validate_workspace_path_rejects_workspace_root_itself(
    workspace_root: Path,
) -> None:
    """Verify that pointing directly to WORKSPACE_ROOT itself is prohibited."""
    with pytest.raises(
        WorkspaceSecurityError, match="not contained within authorized workspace root"
    ):
        SandboxContainerPayloadValidator.validate_workspace_path(
            workspace_root, workspace_root=workspace_root
        )


def test_validate_workspace_path_rejects_nonexistent_path(workspace_root: Path) -> None:
    """Verify that non-existent workspace path is rejected."""
    non_existent = workspace_root / "does_not_exist"
    with pytest.raises(WorkspaceSecurityError, match="Workspace path does not exist"):
        SandboxContainerPayloadValidator.validate_workspace_path(
            non_existent, workspace_root=workspace_root
        )


def test_validate_workspace_path_rejects_file_target(workspace_root: Path) -> None:
    """Verify that a file path (not directory) is rejected as workspace."""
    file_path = workspace_root / "test_file.txt"
    file_path.write_text("hello", encoding="utf-8")
    with pytest.raises(
        WorkspaceSecurityError, match="Workspace path is not a directory"
    ):
        SandboxContainerPayloadValidator.validate_workspace_path(
            file_path, workspace_root=workspace_root
        )


def test_validate_workspace_path_rejects_traversal_string(workspace_root: Path) -> None:
    """Verify string with '..' is rejected before resolution."""
    raw_traversal = str(workspace_root / ".." / "other")
    with pytest.raises(WorkspaceSecurityError, match="Path traversal"):
        SandboxContainerPayloadValidator.validate_workspace_path(
            raw_traversal, workspace_root=workspace_root
        )


def test_validate_workspace_path_rejects_null_byte_string(workspace_root: Path) -> None:
    """Verify path string containing null byte is rejected."""
    with pytest.raises(WorkspaceSecurityError, match="Null byte detected"):
        SandboxContainerPayloadValidator.validate_workspace_path(
            "ws-\x00test", workspace_root=workspace_root
        )


def test_validate_workspace_path_rejects_prohibited_system_paths() -> None:
    """Verify prohibited system roots and directories are rejected."""
    for sys_path in ["/etc", "/var/run", "C:\\", "C:/Windows"]:
        with pytest.raises(WorkspaceSecurityError):
            SandboxContainerPayloadValidator.validate_workspace_path(sys_path)


# ---------------------------------------------------------------------------
# 3. Symlink Escape Detection Tests
# ---------------------------------------------------------------------------


def test_validate_workspace_path_detects_symlink_escape(
    tmp_path: Path, workspace_root: Path
) -> None:
    """Verify that a symlink pointing outside workspace root is rejected."""
    outside_dir = tmp_path / "outside_sensitive"
    outside_dir.mkdir()

    escaped_symlink = workspace_root / "escaped_link"
    try:
        os.symlink(outside_dir, escaped_symlink)
    except OSError:
        pytest.skip("Symlink creation requires elevated permissions on this platform.")

    with pytest.raises(
        WorkspaceSecurityError, match="Symlink escape detected|outside authorized"
    ):
        SandboxContainerPayloadValidator.validate_workspace_path(
            escaped_symlink, workspace_root=workspace_root
        )


# ---------------------------------------------------------------------------
# 4. Workspace Isolation & Cleanup Boundary Tests
# ---------------------------------------------------------------------------


def test_sibling_workspaces_remain_isolated(
    sample_repo: Path, workspace_root: Path
) -> None:
    """Verify sibling workspaces are mutually isolated."""
    mgr = WorkspaceManager(workspace_root=workspace_root)
    meta_1 = mgr.create_workspace(sample_repo, workspace_id="ws-1")
    meta_2 = mgr.create_workspace(sample_repo, workspace_id="ws-2")

    path_1 = Path(meta_1.workspace_path)
    path_2 = Path(meta_2.workspace_path)

    assert path_1 != path_2
    assert path_1.exists()
    assert path_2.exists()

    # Mutate ws-1
    (path_1 / "new_file.py").write_text("added in ws-1", encoding="utf-8")
    assert not (path_2 / "new_file.py").exists()

    mgr.cleanup(meta_1)
    assert not path_1.exists()
    assert path_2.exists()

    mgr.cleanup(meta_2)
    assert not path_2.exists()


def test_cleanup_refuses_to_delete_workspace_root(workspace_root: Path) -> None:
    """Verify cleanup refuses to delete WORKSPACE_ROOT itself."""
    mgr = WorkspaceManager(workspace_root=workspace_root)
    # Attempting to clean up the workspace root itself must return False
    assert not mgr.cleanup(workspace_root)
    assert workspace_root.exists()


def test_cleanup_refuses_to_delete_outside_workspace_root(
    tmp_path: Path, workspace_root: Path
) -> None:
    """Verify cleanup refuses to delete paths outside WORKSPACE_ROOT."""
    mgr = WorkspaceManager(workspace_root=workspace_root)
    outside_dir = tmp_path / "critical_data"
    outside_dir.mkdir()
    (outside_dir / "data.db").write_text("do not delete", encoding="utf-8")

    assert not mgr.cleanup(outside_dir)
    assert outside_dir.exists()
    assert (outside_dir / "data.db").exists()


def test_workspace_session_guarantees_cleanup(
    sample_repo: Path, workspace_root: Path
) -> None:
    """Verify session context manager cleans up workspace even on exception."""
    mgr = WorkspaceManager(workspace_root=workspace_root)
    ws_path: Path | None = None

    with pytest.raises(RuntimeError, match="Simulated crash"):
        with mgr.session(sample_repo) as meta:
            ws_path = Path(meta.workspace_path)
            assert ws_path.exists()
            raise RuntimeError("Simulated crash")

    assert ws_path is not None
    assert not ws_path.exists()
