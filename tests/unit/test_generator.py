"""Unit tests for CodeGenerator and prompt construction."""

from pathlib import Path

import pytest

from backend.app.generation.context import SourceContextBuilder
from backend.app.generation.generator import CodeGenerator
from backend.app.generation.models import (
    FileContext,
    GeneratedFileChange,
    SourceContext,
)
from backend.app.generation.prompts import (
    PROMPT_VERSION,
    build_generation_prompt,
)
from backend.app.generation.provider import MockCodeGenerationProvider
from backend.app.planning.models import (
    ChangeType,
    FileChange,
    ImplementationPlan,
    PlanTask,
    TestPlan,
)


@pytest.fixture
def temp_repo(tmp_path: Path) -> Path:
    """Create a temporary repository structure for generator testing."""
    repo = tmp_path / "sample_repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "tests").mkdir()

    (repo / "src" / "auth.py").write_text(
        "def verify_token(token: str):\n    return token == 'valid'\n",
        encoding="utf-8",
    )
    (repo / "tests" / "test_auth.py").write_text(
        "def test_verify():\n    assert True\n",
        encoding="utf-8",
    )
    return repo


@pytest.fixture
def sample_plan() -> ImplementationPlan:
    """Create a sample implementation plan for generator testing."""
    return ImplementationPlan(
        id="PLAN-GEN-1",
        objective="Fix token expiry check",
        summary="Add token expiration validation to verify_token",
        files_to_modify=[
            FileChange(
                path="src/auth.py",
                change_type=ChangeType.MODIFY,
                reason="Add token expiration check",
                affected_symbols=["verify_token"],
            )
        ],
        files_to_create=[
            FileChange(
                path="src/tokens.py",
                change_type=ChangeType.CREATE,
                reason="Helper token utilities",
            )
        ],
        tasks=[
            PlanTask(
                task_id="TASK-1",
                title="Update token verification",
                description="Check expiry in verify_token",
                order=1,
                target_files=["src/auth.py"],
                target_symbols=["verify_token"],
                change_type=ChangeType.MODIFY,
                verification="Run pytest",
                addressed_requirements=["REQ-1"],
            ),
            PlanTask(
                task_id="TASK-2",
                title="Create token helper",
                description="Token helper methods",
                order=2,
                target_files=["src/tokens.py"],
                target_symbols=[],
                change_type=ChangeType.CREATE,
                verification="Check import",
                addressed_requirements=["REQ-2"],
            ),
        ],
        test_plan=TestPlan(
            existing_tests=["tests/test_auth.py"],
            tests_to_modify=[],
            tests_to_add=[],
        ),
    )


@pytest.fixture
def sample_context() -> SourceContext:
    """Create a valid sample source context."""
    return SourceContext(
        repository_path="d:/fake/repo",
        target_files={
            "src/auth.py": FileContext(
                path="src/auth.py",
                change_type=ChangeType.MODIFY,
                original_content="def verify_token(token: str):\n    return True\n",
                original_hash="fakehash123",
                size_bytes=42,
                target_symbols=["verify_token"],
                exists=True,
            ),
            "src/tokens.py": FileContext(
                path="src/tokens.py",
                change_type=ChangeType.CREATE,
                original_content="",
                original_hash=None,
                size_bytes=0,
                target_symbols=[],
                exists=False,
            ),
        },
        reference_files={
            "tests/test_auth.py": "def test_ok(): assert True",
        },
        total_bytes=42,
        warnings=[],
        errors=[],
    )


def test_build_generation_prompt(
    sample_plan: ImplementationPlan, sample_context: SourceContext
) -> None:
    """Test that the generation prompt has versioning, schema, plan, and context."""
    prompt = build_generation_prompt(sample_plan, sample_context)

    assert f"SYSTEM INSTRUCTIONS (v{PROMPT_VERSION})" in prompt
    assert "RESPONSE SCHEMA" in prompt
    assert "IMPLEMENTATION PLAN" in prompt
    assert "Objective: Fix token expiry check" in prompt
    assert "[TASK-1] Update token verification" in prompt
    assert "AUTHORIZED TARGET FILES" in prompt
    assert "- src/auth.py" in prompt
    assert "- src/tokens.py" in prompt
    assert "UNTRUSTED SOURCE CONTEXT" in prompt
    assert "def verify_token" in prompt
    assert "tests/test_auth.py" in prompt


@pytest.mark.anyio
async def test_code_generator_success(
    sample_plan: ImplementationPlan, sample_context: SourceContext
) -> None:
    """Test successful generation with mock provider."""
    generator = CodeGenerator(provider=MockCodeGenerationProvider())
    changes = await generator.generate_changes(sample_plan, sample_context)

    assert len(changes) == 2
    paths = [c.path for c in changes]
    assert "src/auth.py" in paths
    assert "src/tokens.py" in paths

    auth_change = next(c for c in changes if c.path == "src/auth.py")
    assert "def verify_token" in auth_change.content
    assert "TASK-1" in auth_change.task_ids
    assert "REQ-1" in auth_change.requirement_ids

    tokens_change = next(c for c in changes if c.path == "src/tokens.py")
    assert "TASK-2" in tokens_change.task_ids
    assert "REQ-2" in tokens_change.requirement_ids


@pytest.mark.anyio
async def test_code_generator_invalid_context(
    sample_plan: ImplementationPlan,
) -> None:
    """Test error when source context has errors."""
    bad_context = SourceContext(
        repository_path="d:/fake/repo",
        target_files={},
        reference_files={},
        total_bytes=0,
        warnings=[],
        errors=["Repository path not found"],
    )
    generator = CodeGenerator(provider=MockCodeGenerationProvider())
    with pytest.raises(ValueError, match="Invalid source context"):
        await generator.generate_changes(sample_plan, bad_context)


@pytest.mark.anyio
async def test_code_generator_rejects_unauthorized_file(
    sample_plan: ImplementationPlan, sample_context: SourceContext
) -> None:
    """Test error when provider attempts to modify an unauthorized file."""
    rogue_change = GeneratedFileChange(
        path="src/unauthorized.py",
        change_type=ChangeType.CREATE,
        content="# Rogue code",
    )
    mock_provider = MockCodeGenerationProvider(custom_changes=[rogue_change])
    generator = CodeGenerator(provider=mock_provider)

    with pytest.raises(ValueError, match="unauthorized file"):
        await generator.generate_changes(sample_plan, sample_context)


@pytest.mark.anyio
async def test_code_generator_rejects_empty_content(
    sample_plan: ImplementationPlan, sample_context: SourceContext
) -> None:
    """Test error when provider returns empty content for a file."""
    empty_change = GeneratedFileChange(
        path="src/auth.py",
        change_type=ChangeType.MODIFY,
        content="   \n  ",
    )
    mock_provider = MockCodeGenerationProvider(custom_changes=[empty_change])
    generator = CodeGenerator(provider=mock_provider)

    with pytest.raises(ValueError, match="empty content"):
        await generator.generate_changes(sample_plan, sample_context)


@pytest.mark.anyio
async def test_code_generator_provider_failure(
    sample_plan: ImplementationPlan, sample_context: SourceContext
) -> None:
    """Test propagation of provider failure."""
    failing_provider = MockCodeGenerationProvider(should_fail=True)
    generator = CodeGenerator(provider=failing_provider)

    with pytest.raises(RuntimeError, match="Simulated mock provider failure"):
        await generator.generate_changes(sample_plan, sample_context)


@pytest.mark.anyio
async def test_generator_with_real_temp_repo(
    temp_repo: Path, sample_plan: ImplementationPlan
) -> None:
    """Test end-to-end integration between context builder and code generator."""
    builder = SourceContextBuilder()
    context = builder.build_context(str(temp_repo), sample_plan)
    assert context.is_valid

    generator = CodeGenerator()
    changes = await generator.generate_changes(sample_plan, context)
    assert len(changes) == 2
