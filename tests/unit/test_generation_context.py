"""Unit tests for SourceContextBuilder."""

import hashlib
from pathlib import Path

import pytest

from backend.app.generation.context import SourceContextBuilder
from backend.app.planning.models import (
    ChangeType,
    FileChange,
    ImplementationPlan,
    PlanTask,
    SymbolChange,
    TestPlan,
)


@pytest.fixture
def temp_repo(tmp_path: Path) -> Path:
    """Create a temporary repository structure for context testing."""
    repo = tmp_path / "sample_repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "tests").mkdir()

    # Existing files
    (repo / "src" / "auth.py").write_text(
        "def verify_token(token: str):\n    return token == 'valid'\n",
        encoding="utf-8",
    )
    (repo / "tests" / "test_auth.py").write_text(
        "def test_verify():\n    assert True\n",
        encoding="utf-8",
    )
    return repo


def test_build_context_normal(temp_repo: Path) -> None:
    """Test standard context building with modify and create targets."""
    plan = ImplementationPlan(
        id="PLAN-001",
        objective="Fix token expiry",
        summary="Add token expiry validation",
        files_to_modify=[
            FileChange(
                path="src/auth.py",
                change_type=ChangeType.MODIFY,
                reason="Add expiry check",
                affected_symbols=["verify_token"],
            )
        ],
        files_to_create=[
            FileChange(
                path="src/utils.py",
                change_type=ChangeType.CREATE,
                reason="Helper utilities",
            )
        ],
        tasks=[
            PlanTask(
                task_id="TASK-1",
                title="Update token verification",
                description="Check expiry",
                order=1,
                target_files=["src/auth.py"],
                target_symbols=["verify_token"],
                change_type=ChangeType.MODIFY,
                verification="Run test_auth.py",
            )
        ],
        test_plan=TestPlan(
            existing_tests=["tests/test_auth.py"],
            tests_to_modify=[],
            tests_to_add=[],
        ),
    )

    builder = SourceContextBuilder()
    context = builder.build_context(str(temp_repo), plan)

    assert context.is_valid
    assert len(context.errors) == 0
    assert "src/auth.py" in context.target_files
    assert "src/utils.py" in context.target_files

    auth_ctx = context.target_files["src/auth.py"]
    assert auth_ctx.exists is True
    assert "def verify_token" in auth_ctx.original_content
    expected_hash = hashlib.sha256(
        auth_ctx.original_content.encode("utf-8")
    ).hexdigest()
    assert auth_ctx.original_hash == expected_hash
    assert auth_ctx.size_bytes > 0
    assert "verify_token" in auth_ctx.target_symbols

    utils_ctx = context.target_files["src/utils.py"]
    assert utils_ctx.exists is False
    assert utils_ctx.original_content == ""
    assert utils_ctx.original_hash is None
    assert utils_ctx.size_bytes == 0

    assert "tests/test_auth.py" in context.reference_files
    assert "def test_verify" in context.reference_files["tests/test_auth.py"]


def test_build_context_missing_repo(tmp_path: Path) -> None:
    """Test non-existent repository path."""
    non_existent = tmp_path / "does_not_exist"
    plan = ImplementationPlan(
        objective="Test",
        summary="Test",
        files_to_modify=[
            FileChange(
                path="src/auth.py",
                change_type=ChangeType.MODIFY,
                reason="Test",
            )
        ],
    )
    builder = SourceContextBuilder()
    context = builder.build_context(str(non_existent), plan)

    assert not context.is_valid
    assert any("does not exist" in err for err in context.errors)


def test_build_context_missing_file_to_modify(temp_repo: Path) -> None:
    """Test file marked for modification that does not exist."""
    plan = ImplementationPlan(
        objective="Test",
        summary="Test",
        files_to_modify=[
            FileChange(
                path="src/non_existent.py",
                change_type=ChangeType.MODIFY,
                reason="Modify missing",
            )
        ],
    )
    builder = SourceContextBuilder()
    context = builder.build_context(str(temp_repo), plan)

    assert not context.is_valid
    assert any("not found" in err for err in context.errors)


def test_build_context_create_already_exists(temp_repo: Path) -> None:
    """Test file marked for creation that already exists on disk."""
    plan = ImplementationPlan(
        objective="Test",
        summary="Test",
        files_to_create=[
            FileChange(
                path="src/auth.py",
                change_type=ChangeType.CREATE,
                reason="Already exists",
            )
        ],
    )
    builder = SourceContextBuilder()
    context = builder.build_context(str(temp_repo), plan)

    assert context.is_valid
    assert any("already exists" in warn for warn in context.warnings)
    assert context.target_files["src/auth.py"].exists is True


def test_build_context_path_traversal(temp_repo: Path) -> None:
    """Test path traversal rejection."""
    plan = ImplementationPlan(
        objective="Test",
        summary="Test",
        files_to_modify=[
            FileChange(
                path="../outside.py",
                change_type=ChangeType.MODIFY,
                reason="Traversal",
            )
        ],
    )
    builder = SourceContextBuilder()
    context = builder.build_context(str(temp_repo), plan)

    assert not context.is_valid
    assert any("Path traversal" in err for err in context.errors)


def test_build_context_protected_paths(temp_repo: Path) -> None:
    """Test rejection of protected configuration or credentials paths."""
    (temp_repo / ".env").write_text("SECRET=123\n", encoding="utf-8")
    plan = ImplementationPlan(
        objective="Test",
        summary="Test",
        files_to_modify=[
            FileChange(
                path=".env",
                change_type=ChangeType.MODIFY,
                reason="Touch env",
            )
        ],
    )
    builder = SourceContextBuilder()
    context = builder.build_context(str(temp_repo), plan)

    assert not context.is_valid
    assert any("protected path" in err for err in context.errors)


def test_build_context_reject_deletion(temp_repo: Path) -> None:
    """Test rejection when plan requests file deletion."""
    plan = ImplementationPlan(
        objective="Test",
        summary="Test",
        files_to_delete=[
            FileChange(
                path="src/auth.py",
                change_type=ChangeType.DELETE,
                reason="Delete file",
            )
        ],
    )
    builder = SourceContextBuilder()
    context = builder.build_context(str(temp_repo), plan)

    assert not context.is_valid
    assert any("prohibited" in err for err in context.errors)


def test_build_context_file_count_limit(temp_repo: Path) -> None:
    """Test error when target file count exceeds maximum allowed."""
    files = [
        FileChange(
            path=f"src/file_{i}.py",
            change_type=ChangeType.CREATE,
            reason="Batch",
        )
        for i in range(5)
    ]
    plan = ImplementationPlan(
        objective="Test",
        summary="Test",
        files_to_create=files,
    )
    builder = SourceContextBuilder(max_files=3)
    context = builder.build_context(str(temp_repo), plan)

    assert not context.is_valid
    assert any("exceeds maximum allowed limit" in err for err in context.errors)


def test_build_context_size_limit(temp_repo: Path) -> None:
    """Test error when gathered context size exceeds maximum bytes limit."""
    plan = ImplementationPlan(
        objective="Test",
        summary="Test",
        files_to_modify=[
            FileChange(
                path="src/auth.py",
                change_type=ChangeType.MODIFY,
                reason="Modify",
            )
        ],
    )
    builder = SourceContextBuilder(max_context_size=10)
    context = builder.build_context(str(temp_repo), plan)

    assert not context.is_valid
    assert any("exceeds maximum limit" in err for err in context.errors)


def test_build_context_binary_file(temp_repo: Path) -> None:
    """Test rejection of binary un-decodable files."""
    bin_file = temp_repo / "src" / "binary.dat"
    bin_file.write_bytes(b"\x80\x81\xff\xfe")

    plan = ImplementationPlan(
        objective="Test",
        summary="Test",
        files_to_modify=[
            FileChange(
                path="src/binary.dat",
                change_type=ChangeType.MODIFY,
                reason="Binary",
            )
        ],
    )
    builder = SourceContextBuilder()
    context = builder.build_context(str(temp_repo), plan)

    assert not context.is_valid
    assert any("binary" in err for err in context.errors)


def test_build_context_symbol_aggregation(temp_repo: Path) -> None:
    """Test aggregation of symbols from symbol_changes and tasks."""
    plan = ImplementationPlan(
        objective="Test",
        summary="Test",
        files_to_modify=[
            FileChange(
                path="src/auth.py",
                change_type=ChangeType.MODIFY,
                reason="Update",
            )
        ],
        symbol_changes=[
            SymbolChange(
                file_path="src/auth.py",
                symbol_name="login",
                symbol_type="function",
                change_type=ChangeType.MODIFY,
                reason="Update login",
            )
        ],
        tasks=[
            PlanTask(
                task_id="TASK-1",
                title="Task",
                description="Desc",
                order=1,
                target_files=["src/auth.py"],
                target_symbols=["verify_token", "hash_password"],
                change_type=ChangeType.MODIFY,
                verification="Test",
            )
        ],
    )
    builder = SourceContextBuilder()
    context = builder.build_context(str(temp_repo), plan)

    assert context.is_valid
    auth_ctx = context.target_files["src/auth.py"]
    assert "login" in auth_ctx.target_symbols
    assert "verify_token" in auth_ctx.target_symbols
    assert "hash_password" in auth_ctx.target_symbols
