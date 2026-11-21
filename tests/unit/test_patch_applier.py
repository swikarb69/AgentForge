"""Unit tests for secure patch application in isolated workspaces."""

import hashlib
from pathlib import Path

import pytest

from backend.app.execution.patch_applier import PatchApplier
from backend.app.generation.models import (
    GeneratedFileChange,
    GenerationResult,
    GenerationStatus,
    PatchValidation,
)
from backend.app.planning.models import ChangeType


def test_apply_file_changes_create_and_modify(tmp_path: Path) -> None:
    """Verify applying create and modify changes to files in workspace."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    applier = PatchApplier()

    changes = [
        GeneratedFileChange(
            path="calc.py",
            change_type=ChangeType.MODIFY,
            content="def add(a, b):\n    return a + b + 0\n",
        ),
        GeneratedFileChange(
            path="subpkg/helper.py",
            change_type=ChangeType.CREATE,
            content="def help():\n    return 'ok'\n",
        ),
    ]

    result = applier.apply_file_changes(ws, changes)

    assert result.success
    assert result.applied_files == ["calc.py", "subpkg/helper.py"]
    assert result.modified_files == ["calc.py"]
    assert result.created_files == ["subpkg/helper.py"]
    assert result.errors == []

    # Check files on disk
    assert (ws / "calc.py").read_text(
        encoding="utf-8"
    ) == "def add(a, b):\n    return a + b + 0\n"
    assert (ws / "subpkg" / "helper.py").read_text(
        encoding="utf-8"
    ) == "def help():\n    return 'ok'\n"


def test_apply_file_changes_baseline_hash_verification(tmp_path: Path) -> None:
    """Verify baseline integrity checking fails on content mismatch."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    original_code = "ORIGINAL_CONTENT\n"
    (ws / "config.py").write_text(original_code, encoding="utf-8")
    correct_hash = hashlib.sha256(original_code.encode("utf-8")).hexdigest()

    applier = PatchApplier()
    change = GeneratedFileChange(
        path="config.py",
        change_type=ChangeType.MODIFY,
        content="UPDATED_CONTENT\n",
    )

    # Success with matching hash
    ok_result = applier.apply_file_changes(
        ws, [change], expected_hashes={"config.py": correct_hash}
    )
    assert ok_result.success

    # Failure with mismatching hash
    bad_result = applier.apply_file_changes(
        ws, [change], expected_hashes={"config.py": "0" * 64}
    )
    assert not bad_result.success
    assert any("Baseline integrity mismatch" in err for err in bad_result.errors)


def test_apply_file_changes_modify_nonexistent(tmp_path: Path) -> None:
    """Verify modifying a nonexistent file records error."""
    ws = tmp_path / "workspace"
    ws.mkdir()

    applier = PatchApplier()
    change = GeneratedFileChange(
        path="missing.py",
        change_type=ChangeType.MODIFY,
        content="new content",
    )

    result = applier.apply_file_changes(ws, [change])
    assert not result.success
    assert any("does not exist in workspace" in err for err in result.errors)


def test_validate_target_path_traversal(tmp_path: Path) -> None:
    """Verify directory traversal is prevented."""
    ws = tmp_path / "workspace"
    ws.mkdir()

    applier = PatchApplier()
    with pytest.raises(ValueError, match="Path traversal"):
        applier.validate_target_path(ws, "sub/../../escape.py")

    with pytest.raises(ValueError, match="Path must be relative"):
        applier.validate_target_path(ws, "/etc/passwd")


def test_validate_target_path_protected(tmp_path: Path) -> None:
    """Verify modifying protected security files is forbidden."""
    ws = tmp_path / "workspace"
    ws.mkdir()

    applier = PatchApplier()
    change = GeneratedFileChange(
        path=".env",
        change_type=ChangeType.CREATE,
        content="TOKEN=abc",
    )

    result = applier.apply_file_changes(ws, [change])
    assert not result.success
    assert any("protected path is forbidden" in err for err in result.errors)


def test_apply_unified_diff(tmp_path: Path) -> None:
    """Verify parsing and applying unified diff text."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "service.py").write_text("line1\nline2\nline3\n", encoding="utf-8")

    diff_text = (
        "--- a/service.py\n"
        "+++ b/service.py\n"
        "@@ -1,3 +1,3 @@\n"
        " line1\n"
        "-line2\n"
        "+line2_modified\n"
        " line3\n"
        "--- /dev/null\n"
        "+++ b/created.py\n"
        "@@ -0,0 +1,2 @@\n"
        "+new_line_1\n"
        "+new_line_2\n"
    )

    applier = PatchApplier()
    result = applier.apply_unified_diff(ws, diff_text)

    assert result.success
    assert "service.py" in result.modified_files
    assert "created.py" in result.created_files
    assert (ws / "service.py").read_text(
        encoding="utf-8"
    ) == "line1\nline2_modified\nline3\n"
    assert (ws / "created.py").read_text(encoding="utf-8") == "new_line_1\nnew_line_2"


def test_apply_from_generation_result(tmp_path: Path) -> None:
    """Verify high-level apply method using GenerationResult object."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "foo.py").write_text("x = 10\n", encoding="utf-8")

    gen_result = GenerationResult(
        generation_id="gen-test-01",
        status=GenerationStatus.VALIDATED,
        provider_type="mock",
        changes=[
            GeneratedFileChange(
                path="foo.py",
                change_type=ChangeType.MODIFY,
                content="x = 20\n",
            )
        ],
        validation=PatchValidation(
            valid=True,
            status=GenerationStatus.VALIDATED,
        ),
        patch_content="",
    )

    applier = PatchApplier()
    result = applier.apply(ws, generation_result=gen_result)

    assert result.success
    assert "foo.py" in result.modified_files
    assert (ws / "foo.py").read_text(encoding="utf-8") == "x = 20\n"


def test_apply_empty_inputs(tmp_path: Path) -> None:
    """Verify apply reports error when no changes or patch are provided."""
    ws = tmp_path / "workspace"
    ws.mkdir()

    applier = PatchApplier()
    result = applier.apply(ws)
    assert not result.success
    assert any(
        "No changes, patch content, or GenerationResult" in err for err in result.errors
    )
