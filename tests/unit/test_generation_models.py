"""Unit tests for code generation domain models and schema validation."""

import pytest
from pydantic import ValidationError

from backend.app.generation.models import (
    CreateGenerationRequest,
    GeneratedFileChange,
    GenerationRequest,
    GenerationResult,
    GenerationStatus,
    PatchValidation,
)
from backend.app.planning.models import ChangeType, ImplementationPlan


def test_generated_file_change_valid() -> None:
    """Verify creation and serialization of valid GeneratedFileChange."""
    change = GeneratedFileChange(
        path="backend/app/auth.py",
        change_type=ChangeType.MODIFY,
        content="def verify_token(token: str): return True\n",
        task_ids=["TASK-001"],
        requirement_ids=["REQ-001"],
        metadata={"model": "test-mock"},
    )
    assert change.path == "backend/app/auth.py"
    assert change.change_type == ChangeType.MODIFY
    assert change.content.startswith("def verify_token")
    assert change.task_ids == ["TASK-001"]
    assert change.requirement_ids == ["REQ-001"]

    dumped = change.model_dump()
    assert dumped["path"] == "backend/app/auth.py"
    assert dumped["change_type"] == "modify"


def test_generated_file_change_rejects_delete_change_type() -> None:
    """Verify that file deletion is rejected for code generation proposals."""
    with pytest.raises(ValidationError) as exc_info:
        GeneratedFileChange(
            path="backend/app/auth.py",
            change_type=ChangeType.DELETE,
            content="",
        )
    assert "Only 'create' and 'modify' are permitted" in str(exc_info.value)


@pytest.mark.parametrize(
    "invalid_path",
    [
        "/absolute/path/file.py",
        "../traversal/file.py",
        "app/../../secret.py",
        "C:\\Windows\\system.py",
        "   ",
        "",
    ],
)
def test_generated_file_change_rejects_unsafe_paths(invalid_path: str) -> None:
    """Verify path traversal, absolute paths, and drive letters are rejected."""
    with pytest.raises(ValidationError):
        GeneratedFileChange(
            path=invalid_path,
            change_type=ChangeType.MODIFY,
            content="print('unsafe')",
        )


def test_patch_validation_model() -> None:
    """Verify PatchValidation model fields."""
    pv = PatchValidation(
        valid=True,
        status=GenerationStatus.VALIDATED,
        errors=[],
        warnings=["Non-critical warning"],
        files_checked=["app/auth.py"],
        plan_scope_violations=[],
        path_safety_findings=[],
    )
    assert pv.valid is True
    assert pv.status == GenerationStatus.VALIDATED
    assert len(pv.files_checked) == 1


def test_generation_request_model() -> None:
    """Verify GenerationRequest structure and defaults."""
    plan = ImplementationPlan(
        id="PLAN-001",
        objective="Reject expired tokens",
        summary="Update auth validation",
    )
    req = GenerationRequest(
        generation_id="GEN-123",
        plan=plan,
        repository_path="/workspace/sample",
        permitted_files=["app/auth.py"],
    )
    assert req.generation_id == "GEN-123"
    assert req.provider_type == "mock"
    assert req.max_files == 10
    assert req.timeout_seconds == 60


def test_generation_result_model() -> None:
    """Verify GenerationResult model instantiation and serialization."""
    pv = PatchValidation(
        valid=True,
        status=GenerationStatus.VALIDATED,
        files_checked=["app/auth.py"],
    )
    res = GenerationResult(
        generation_id="GEN-123",
        status=GenerationStatus.VALIDATED,
        changes=[
            GeneratedFileChange(
                path="app/auth.py",
                change_type=ChangeType.MODIFY,
                content="def test(): pass\n",
            )
        ],
        validation=pv,
        patch_content="--- a/app/auth.py\n+++ b/app/auth.py\n@@ -1 +1 @@\n",
        provider_type="mock",
    )
    assert res.generation_id == "GEN-123"
    assert res.status == GenerationStatus.VALIDATED
    assert len(res.changes) == 1
    assert "--- a/app/auth.py" in res.patch_content
    assert res.created_at is not None


def test_create_generation_request_model() -> None:
    """Verify CreateGenerationRequest model validation."""
    req = CreateGenerationRequest(
        repository_path="agentforge-fixtures/sample-project",
        title="Fix token validation",
        description="Reject expired access tokens with 401",
    )
    assert req.repository_path == "agentforge-fixtures/sample-project"
    assert req.plan is None
    assert req.provider_type is None
