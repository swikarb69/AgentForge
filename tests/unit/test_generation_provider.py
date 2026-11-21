"""Unit tests for code generation provider interface and mock provider."""

import pytest

from backend.app.generation.models import (
    GeneratedFileChange,
    GenerationRequest,
)
from backend.app.generation.provider import (
    GeminiCodeGenerationProvider,
    MockCodeGenerationProvider,
    get_provider,
)
from backend.app.planning.models import (
    ChangeType,
    FileChange,
    ImplementationPlan,
    PlanTask,
)


@pytest.fixture
def sample_plan() -> ImplementationPlan:
    """Fixture returning an implementation plan with authorized targets."""
    return ImplementationPlan(
        id="PLAN-AUTH",
        objective="Reject expired tokens",
        summary="Update verify_token function",
        files_to_modify=[
            FileChange(
                path="app/auth.py",
                change_type=ChangeType.MODIFY,
                reason="Update expired token check",
                affected_symbols=["verify_token"],
            )
        ],
        files_to_create=[
            FileChange(
                path="app/tokens.py",
                change_type=ChangeType.CREATE,
                reason="Create token helper",
            )
        ],
        tasks=[
            PlanTask(
                task_id="TASK-001",
                title="Update verify_token",
                description="Reject expired token",
                order=1,
                target_files=["app/auth.py"],
                change_type=ChangeType.MODIFY,
                verification="Check expired token fails",
                addressed_requirements=["REQ-1"],
            ),
            PlanTask(
                task_id="TASK-002",
                title="Create token helper",
                description="Add helper module",
                order=2,
                target_files=["app/tokens.py"],
                change_type=ChangeType.CREATE,
                verification="Check module syntax",
                addressed_requirements=["REQ-2"],
            ),
        ],
    )


@pytest.mark.anyio
async def test_mock_provider_generates_changes(
    sample_plan: ImplementationPlan,
) -> None:
    """Verify mock provider generates changes for authorized plan files."""
    provider = MockCodeGenerationProvider()
    request = GenerationRequest(
        generation_id="GEN-001",
        plan=sample_plan,
        repository_path="/workspace/sample",
        permitted_files=["app/auth.py", "app/tokens.py"],
    )
    context_payload = {
        "file_contents": {
            "app/auth.py": ("def verify_token(token: str):\n    return True\n")
        }
    }

    changes = await provider.generate(request, context_payload)
    assert len(changes) == 2

    # Verify modification
    mod_change = next(c for c in changes if c.path == "app/auth.py")
    assert mod_change.change_type == ChangeType.MODIFY
    assert "expired" in mod_change.content
    assert "TASK-001" in mod_change.task_ids

    # Verify creation
    create_change = next(c for c in changes if c.path == "app/tokens.py")
    assert create_change.change_type == ChangeType.CREATE
    assert "TASK-002" in create_change.task_ids
    assert provider.call_count == 1


@pytest.mark.anyio
async def test_mock_provider_failure_injection(
    sample_plan: ImplementationPlan,
) -> None:
    """Verify mock provider error simulation."""
    provider = MockCodeGenerationProvider(
        should_fail=True,
        error_message="Custom API error",
    )
    request = GenerationRequest(
        generation_id="GEN-FAIL",
        plan=sample_plan,
        repository_path="/workspace/sample",
    )
    with pytest.raises(RuntimeError, match="Custom API error"):
        await provider.generate(request, {})


@pytest.mark.anyio
async def test_mock_provider_empty_response(
    sample_plan: ImplementationPlan,
) -> None:
    """Verify mock provider empty response simulation."""
    provider = MockCodeGenerationProvider(empty_response=True)
    request = GenerationRequest(
        generation_id="GEN-EMPTY",
        plan=sample_plan,
        repository_path="/workspace/sample",
    )
    changes = await provider.generate(request, {})
    assert changes == []


@pytest.mark.anyio
async def test_mock_provider_custom_changes(
    sample_plan: ImplementationPlan,
) -> None:
    """Verify mock provider returns custom injected changes."""
    custom = [
        GeneratedFileChange(
            path="app/auth.py",
            change_type=ChangeType.MODIFY,
            content="custom code\n",
        )
    ]
    provider = MockCodeGenerationProvider(custom_changes=custom)
    request = GenerationRequest(
        generation_id="GEN-CUSTOM",
        plan=sample_plan,
        repository_path="/workspace/sample",
    )
    changes = await provider.generate(request, {})
    assert len(changes) == 1
    assert changes[0].content == "custom code\n"


@pytest.mark.anyio
async def test_gemini_provider_requires_api_key(
    sample_plan: ImplementationPlan,
) -> None:
    """Verify Gemini provider raises error when api_key is missing."""
    provider = GeminiCodeGenerationProvider(api_key=None)
    # Ensure api_key is None even if .env has something
    provider._api_key = None
    request = GenerationRequest(
        generation_id="GEN-GEMINI",
        plan=sample_plan,
        repository_path="/workspace/sample",
    )
    with pytest.raises(ValueError, match="Gemini API key is not configured"):
        await provider.generate(request, {})


def test_get_provider_factory() -> None:
    """Verify get_provider factory resolves mock and rejects unknown types."""
    mock_prov = get_provider("mock")
    assert mock_prov.provider_type == "mock"

    gemini_prov = get_provider("gemini", api_key="dummy-key")
    assert gemini_prov.provider_type == "gemini"

    with pytest.raises(ValueError, match="Unsupported provider type"):
        get_provider("invalid-vendor")
