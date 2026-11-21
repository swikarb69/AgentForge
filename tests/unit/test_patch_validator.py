"""Unit tests for PatchValidator."""

import pytest

from backend.app.generation.models import (
    FileContext,
    GeneratedFileChange,
    GenerationStatus,
    SourceContext,
)
from backend.app.generation.validator import PatchValidator
from backend.app.planning.models import (
    ChangeType,
    FileChange,
    ImplementationPlan,
)


@pytest.fixture
def sample_plan() -> ImplementationPlan:
    """Create sample plan with authorized target files."""
    return ImplementationPlan(
        id="PLAN-VAL-1",
        objective="Fix token expiry",
        summary="Token validation",
        files_to_modify=[
            FileChange(
                path="src/auth.py",
                change_type=ChangeType.MODIFY,
                reason="Update verify_token",
            )
        ],
        files_to_create=[
            FileChange(
                path="src/tokens.py",
                change_type=ChangeType.CREATE,
                reason="Add token helper",
            )
        ],
    )


@pytest.fixture
def sample_context() -> SourceContext:
    """Create sample context matching authorized target files."""
    return SourceContext(
        repository_path="d:/fake/repo",
        target_files={
            "src/auth.py": FileContext(
                path="src/auth.py",
                change_type=ChangeType.MODIFY,
                original_content="def verify_token(token: str):\n    return True\n",
                original_hash="fakehash",
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


def test_validator_valid_changes(
    sample_plan: ImplementationPlan, sample_context: SourceContext
) -> None:
    """Test that well-formed, authorized changes pass validation."""
    validator = PatchValidator()
    changes = [
        GeneratedFileChange(
            path="src/auth.py",
            change_type=ChangeType.MODIFY,
            content=(
                "def verify_token(token: str):\n"
                '    """Updated token check."""\n'
                '    if token == "expired":\n'
                "        return False\n"
                "    return True\n"
            ),
        ),
        GeneratedFileChange(
            path="src/tokens.py",
            change_type=ChangeType.CREATE,
            content='"""Token helpers."""\n\ndef create_token():\n    return "token"\n',
        ),
    ]

    result = validator.validate(changes, sample_plan, sample_context)

    assert result.valid is True
    assert result.status == GenerationStatus.VALIDATED
    assert len(result.errors) == 0
    assert len(result.plan_scope_violations) == 0
    assert len(result.path_safety_findings) == 0
    assert "src/auth.py" in result.files_checked
    assert "src/tokens.py" in result.files_checked


def test_validator_unauthorized_file(
    sample_plan: ImplementationPlan, sample_context: SourceContext
) -> None:
    """Test that proposing change to unauthorized file is flagged and rejected."""
    validator = PatchValidator()
    changes = [
        GeneratedFileChange(
            path="src/rogue.py",
            change_type=ChangeType.CREATE,
            content="print('rogue')\n",
        )
    ]

    result = validator.validate(changes, sample_plan, sample_context)

    assert result.valid is False
    assert result.status == GenerationStatus.REJECTED
    assert any("not authorized" in v for v in result.plan_scope_violations)


def test_validator_path_traversal(
    sample_plan: ImplementationPlan, sample_context: SourceContext
) -> None:
    """Test that path traversal is caught and flagged."""
    validator = PatchValidator()
    # Note: GeneratedFileChange validates path on model creation, but if constructed
    # we test validator catches path traversal safety
    changes = [
        GeneratedFileChange(
            path="src/auth.py",
            change_type=ChangeType.MODIFY,
            content="def verify(): pass\n",
        )
    ]
    # Manually modify to bypass model validator for unit test
    changes[0].path = "../outside.py"

    result = validator.validate(changes, sample_plan, sample_context)

    assert result.valid is False
    assert any("Path traversal" in f for f in result.path_safety_findings)


def test_validator_protected_path(
    sample_plan: ImplementationPlan, sample_context: SourceContext
) -> None:
    """Test that touching protected paths like .env is rejected."""
    validator = PatchValidator()
    changes = [
        GeneratedFileChange(
            path="src/auth.py",
            change_type=ChangeType.MODIFY,
            content="SECRET=1\n",
        )
    ]
    changes[0].path = ".env"

    result = validator.validate(changes, sample_plan, sample_context)

    assert result.valid is False
    assert any("protected path" in f for f in result.path_safety_findings)


def test_validator_python_syntax_error(
    sample_plan: ImplementationPlan, sample_context: SourceContext
) -> None:
    """Test that invalid Python syntax is caught by ast.parse."""
    validator = PatchValidator()
    changes = [
        GeneratedFileChange(
            path="src/auth.py",
            change_type=ChangeType.MODIFY,
            content="def broken_syntax(:\n    return True\n",
        )
    ]

    result = validator.validate(changes, sample_plan, sample_context)

    assert result.valid is False
    assert result.status == GenerationStatus.REJECTED
    assert any("Python syntax error" in err for err in result.errors)


def test_validator_empty_content(
    sample_plan: ImplementationPlan, sample_context: SourceContext
) -> None:
    """Test that proposing empty content is rejected."""
    validator = PatchValidator()
    changes = [
        GeneratedFileChange(
            path="src/auth.py",
            change_type=ChangeType.MODIFY,
            content="   \n  ",
        )
    ]

    result = validator.validate(changes, sample_plan, sample_context)

    assert result.valid is False
    assert any("empty" in err for err in result.errors)


def test_validator_exceeds_size_limit(
    sample_plan: ImplementationPlan, sample_context: SourceContext
) -> None:
    """Test that exceeding max output bytes is rejected."""
    validator = PatchValidator(max_output_bytes=20)
    changes = [
        GeneratedFileChange(
            path="src/tokens.py",
            change_type=ChangeType.CREATE,
            content="a" * 100,
        )
    ]

    result = validator.validate(changes, sample_plan, sample_context)

    assert result.valid is False
    assert any("exceeds maximum output limit" in err for err in result.errors)


def test_validator_no_changes(
    sample_plan: ImplementationPlan, sample_context: SourceContext
) -> None:
    """Test that empty change list produces warning."""
    validator = PatchValidator()
    result = validator.validate([], sample_plan, sample_context)

    assert result.valid is True
    assert any("No file changes" in w for w in result.warnings)
