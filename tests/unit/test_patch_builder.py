"""Unit tests for PatchBuilder."""

from pathlib import Path

import pytest

from backend.app.generation.models import (
    FileContext,
    GeneratedFileChange,
    SourceContext,
)
from backend.app.generation.patch import PatchBuilder
from backend.app.planning.models import ChangeType


@pytest.fixture
def sample_context() -> SourceContext:
    """Create sample source context for diff computation."""
    return SourceContext(
        repository_path="d:/fake/repo",
        target_files={
            "src/auth.py": FileContext(
                path="src/auth.py",
                change_type=ChangeType.MODIFY,
                original_content=("def verify_token(token: str):\n    return True\n"),
                original_hash="hash1",
                size_bytes=42,
                exists=True,
            ),
            "src/tokens.py": FileContext(
                path="src/tokens.py",
                change_type=ChangeType.CREATE,
                original_content="",
                original_hash=None,
                size_bytes=0,
                exists=False,
            ),
        },
        reference_files={},
        total_bytes=42,
        warnings=[],
        errors=[],
    )


def test_build_diff_modify(sample_context: SourceContext) -> None:
    """Test unified diff generation for modified file."""
    builder = PatchBuilder()
    change = GeneratedFileChange(
        path="src/auth.py",
        change_type=ChangeType.MODIFY,
        content=(
            "def verify_token(token: str):\n"
            "    if token == 'expired':\n"
            "        return False\n"
            "    return True\n"
        ),
    )

    diff = builder.build_diff(change, sample_context)

    assert "--- a/src/auth.py" in diff
    assert "+++ b/src/auth.py" in diff
    assert "+    if token == 'expired':" in diff
    assert "+        return False" in diff


def test_build_diff_create(sample_context: SourceContext) -> None:
    """Test unified diff generation for new file."""
    builder = PatchBuilder()
    change = GeneratedFileChange(
        path="src/tokens.py",
        change_type=ChangeType.CREATE,
        content="def get_token():\n    return 'xyz'\n",
    )

    diff = builder.build_diff(change, sample_context)

    assert "--- /dev/null" in diff
    assert "+++ b/src/tokens.py" in diff
    assert "+def get_token():" in diff
    assert "+    return 'xyz'" in diff


def test_build_patch_and_save_artifact(
    tmp_path: Path, sample_context: SourceContext
) -> None:
    """Test full patch generation and artifact file persistence."""
    artifact_dir = tmp_path / "artifacts" / "generation"
    builder = PatchBuilder(artifact_dir=str(artifact_dir))

    changes = [
        GeneratedFileChange(
            path="src/auth.py",
            change_type=ChangeType.MODIFY,
            content=(
                "def verify_token(token: str):\n"
                "    if token == 'expired':\n"
                "        return False\n"
                "    return True\n"
            ),
        ),
        GeneratedFileChange(
            path="src/tokens.py",
            change_type=ChangeType.CREATE,
            content="def get_token(): return 'xyz'\n",
        ),
    ]

    patch_text = builder.build_patch(
        generation_id="GEN-1234",
        plan_id="PLAN-5678",
        changes=changes,
        context=sample_context,
    )

    assert "# AgentForge Unified Diff Patch" in patch_text
    assert "# Generation ID: GEN-1234" in patch_text
    assert "# Plan ID: PLAN-5678" in patch_text
    assert "# Total Files: 2" in patch_text
    assert "--- a/src/auth.py" in patch_text
    assert "--- /dev/null" in patch_text

    artifact_file = builder.save_artifact("GEN-1234", patch_text)
    assert artifact_file.exists()
    assert artifact_file.read_text(encoding="utf-8") == patch_text
