"""Unit tests for live AI provider integrations, retry loops, and auth boundaries."""

import json
import os
from typing import Any

import httpx
import pytest

from backend.app.generation.models import (
    CreateGenerationRequest,
    GenerationRequest,
)
from backend.app.generation.provider import (
    GeminiCodeGenerationProvider,
    MockCodeGenerationProvider,
    OpenAICodeGenerationProvider,
    ProviderFactory,
    get_provider,
    parse_llm_code_response,
)
from backend.app.generation.service import GenerationService
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
        id="PLAN-AUTH-001",
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
async def test_gemini_header_based_authentication(
    sample_plan: ImplementationPlan,
) -> None:
    """Verify Gemini provider passes API key in header and NEVER in URL query."""
    captured_requests: list[httpx.Request] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        resp_data = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": json.dumps(
                                    {
                                        "changes": [
                                            {
                                                "path": "app/auth.py",
                                                "change_type": "modify",
                                                "content": "def verify(): return False",
                                                "task_ids": ["TASK-001"],
                                                "requirement_ids": ["REQ-1"],
                                            }
                                        ]
                                    }
                                )
                            }
                        ]
                    }
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 120,
                "candidatesTokenCount": 85,
                "totalTokenCount": 205,
            },
        }
        return httpx.Response(200, json=resp_data)

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = GeminiCodeGenerationProvider(
            api_key="secret-gemini-test-key",
            model="gemini-1.5-flash",
            http_client=client,
        )

        request = GenerationRequest(
            generation_id="GEN-TEST-01",
            plan=sample_plan,
            repository_path="/workspace/test",
            permitted_files=["app/auth.py"],
        )

        changes = await provider.generate(request, {"prompt": "Fix token expiration."})

        assert len(changes) == 1
        assert changes[0].path == "app/auth.py"
        assert changes[0].change_type == ChangeType.MODIFY
        assert "verify(): return False" in changes[0].content

        # Verify header auth security boundary: key MUST NOT appear in URL query
        assert len(captured_requests) == 1
        req = captured_requests[0]
        assert req.headers.get("x-goog-api-key") == "secret-gemini-test-key"
        assert "key=" not in str(req.url)
        assert req.headers.get("Content-Type") == "application/json"


@pytest.mark.anyio
async def test_gemini_rate_limit_retry_with_exponential_backoff(
    sample_plan: ImplementationPlan,
) -> None:
    """Verify Gemini retries HTTP 429 rate limits and succeeds on retry."""
    attempts = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, text="Resource has been exhausted (rate limit)")

        resp_data = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": json.dumps(
                                    {
                                        "changes": [
                                            {
                                                "path": "app/auth.py",
                                                "change_type": "modify",
                                                "content": "# Patched on retry\n",
                                            }
                                        ]
                                    }
                                )
                            }
                        ]
                    }
                }
            ]
        }
        return httpx.Response(200, json=resp_data)

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = GeminiCodeGenerationProvider(
            api_key="test-key",
            max_attempts=3,
            initial_delay=0.001,  # Fast in test
            backoff_factor=2.0,
            http_client=client,
        )

        request = GenerationRequest(
            generation_id="GEN-RETRY-01",
            plan=sample_plan,
            repository_path="/workspace/test",
            permitted_files=["app/auth.py"],
        )

        changes = await provider.generate(request, {"prompt": "Fix token."})
        assert attempts == 2
        assert len(changes) == 1
        assert "Patched on retry" in changes[0].content


@pytest.mark.anyio
async def test_gemini_server_error_retry_and_exhaustion(
    sample_plan: ImplementationPlan,
) -> None:
    """Verify transient server errors (HTTP 500) retry and eventually raise."""
    attempts = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(500, text="Internal Server Error")

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = GeminiCodeGenerationProvider(
            api_key="test-key",
            max_attempts=3,
            initial_delay=0.001,
            http_client=client,
        )

        request = GenerationRequest(
            generation_id="GEN-FAIL-01",
            plan=sample_plan,
            repository_path="/workspace/test",
            permitted_files=["app/auth.py"],
        )

        with pytest.raises(RuntimeError, match="Gemini API error \\(HTTP 500\\)"):
            await provider.generate(request, {"prompt": "Fix token."})

        assert attempts == 3


@pytest.mark.anyio
async def test_markdown_code_block_extraction_when_provider_returns_backticks(
    sample_plan: ImplementationPlan,
) -> None:
    """Verify fallback parser recovers when provider returns markdown fences."""
    # Case 1: Markdown wrapped JSON
    wrapped_json = (
        "Here is the result:\n```json\n"
        + json.dumps(
            {
                "changes": [
                    {
                        "path": "app/auth.py",
                        "change_type": "modify",
                        "content": "def verify(): pass\n",
                    }
                ]
            }
        )
        + "\n```"
    )

    request = GenerationRequest(
        generation_id="GEN-PARSE-01",
        plan=sample_plan,
        repository_path="/workspace/test",
        permitted_files=["app/auth.py"],
    )

    changes_1 = parse_llm_code_response(
        raw_text=wrapped_json,
        provider_name="gemini",
        request=request,
    )
    assert len(changes_1) == 1
    assert changes_1[0].path == "app/auth.py"
    assert "verify(): pass" in changes_1[0].content

    # Case 2: Direct Python code block with no JSON structure
    raw_markdown = (
        "I have updated the code to check for expired tokens:\n"
        "```python\n"
        "def verify_token(token: str) -> bool:\n"
        '    if token == "expired":\n'
        "        return False\n"
        "    return True\n"
        "```\n"
    )

    changes_2 = parse_llm_code_response(
        raw_text=raw_markdown,
        provider_name="gemini",
        request=request,
    )
    assert len(changes_2) == 1
    assert changes_2[0].path == "app/auth.py"
    assert changes_2[0].change_type == ChangeType.MODIFY
    assert 'token == "expired"' in changes_2[0].content

    # Case 3: Multiple file fences with path markers
    multi_file_markdown = (
        "Here are the changes:\n"
        "```python:app/auth.py\n"
        "def verify_token(): return False\n"
        "```\n\n"
        "```python:app/tokens.py\n"
        "def generate_token(): return 'tok'\n"
        "```\n"
    )
    multi_request = GenerationRequest(
        generation_id="GEN-PARSE-02",
        plan=sample_plan,
        repository_path="/workspace/test",
        permitted_files=["app/auth.py", "app/tokens.py"],
    )
    changes_3 = parse_llm_code_response(
        raw_text=multi_file_markdown,
        provider_name="gemini",
        request=multi_request,
    )
    assert len(changes_3) == 2
    assert {c.path for c in changes_3} == {"app/auth.py", "app/tokens.py"}


@pytest.mark.anyio
async def test_token_tracking_in_generation_result(
    tmp_path: Any,
    sample_plan: ImplementationPlan,
) -> None:
    """Verify provider token metadata is tracked and exposed in GenerationResult."""
    # Create sample repository file
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    auth_file = repo_dir / "app" / "auth.py"
    auth_file.parent.mkdir(parents=True)
    auth_file.write_text("def verify_token(token: str):\n    return True\n")

    def mock_handler(request: httpx.Request) -> httpx.Response:
        resp_data = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": json.dumps(
                                    {
                                        "changes": [
                                            {
                                                "path": "app/auth.py",
                                                "change_type": "modify",
                                                "content": (
                                                    "def verify_token(token: str):\n"
                                                    "    return token != 'expired'\n"
                                                ),
                                            }
                                        ]
                                    }
                                )
                            }
                        ]
                    }
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 250,
                "candidatesTokenCount": 110,
                "totalTokenCount": 360,
            },
        }
        return httpx.Response(200, json=resp_data)

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = GeminiCodeGenerationProvider(
            api_key="test-key",
            http_client=client,
        )

        service = GenerationService()
        # Override service generator provider
        service.generator.provider = provider

        payload = CreateGenerationRequest(
            repository_path=str(repo_dir),
            plan=sample_plan,
        )

        result = await service.generate_code(payload)
        assert result.status.value in ("validated", "generated")
        assert result.usage_metadata is not None
        assert "tokens" in result.usage_metadata
        tokens = result.usage_metadata["tokens"]
        assert tokens["prompt_tokens"] == 250
        assert tokens["completion_tokens"] == 110
        assert tokens["total_tokens"] == 360


@pytest.mark.anyio
async def test_openai_provider_header_auth_and_usage(
    sample_plan: ImplementationPlan,
) -> None:
    """Verify OpenAI provider uses Bearer token auth and extracts token usage."""
    captured_requests: list[httpx.Request] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        resp_data = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "changes": [
                                    {
                                        "path": "app/auth.py",
                                        "change_type": "modify",
                                        "content": "def verify(): return True\n",
                                    }
                                ]
                            }
                        )
                    }
                }
            ],
            "usage": {
                "prompt_tokens": 140,
                "completion_tokens": 60,
                "total_tokens": 200,
            },
        }
        return httpx.Response(200, json=resp_data)

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = OpenAICodeGenerationProvider(
            api_key="sk-test-openai-key",
            model="gpt-4o",
            http_client=client,
        )

        request = GenerationRequest(
            generation_id="GEN-OAI-01",
            plan=sample_plan,
            repository_path="/workspace/test",
            permitted_files=["app/auth.py"],
        )

        changes = await provider.generate(request, {"prompt": "Generate auth code."})
        assert len(changes) == 1
        assert captured_requests[0].headers.get("Authorization") == (
            "Bearer sk-test-openai-key"
        )
        assert provider.last_usage == {
            "prompt_tokens": 140,
            "completion_tokens": 60,
            "total_tokens": 200,
        }


def test_provider_factory_resolution() -> None:
    """Verify ProviderFactory resolves mock, gemini, and openai cleanly."""
    mock_prov = ProviderFactory.create("mock")
    assert isinstance(mock_prov, MockCodeGenerationProvider)
    assert mock_prov.provider_type == "mock"

    gemini_prov = ProviderFactory.create("gemini", api_key="dummy-gemini")
    assert isinstance(gemini_prov, GeminiCodeGenerationProvider)
    assert gemini_prov.provider_type == "gemini"

    openai_prov = ProviderFactory.create("openai", api_key="dummy-openai")
    assert isinstance(openai_prov, OpenAICodeGenerationProvider)
    assert openai_prov.provider_type == "openai"

    get_prov_res = get_provider("mock")
    assert get_prov_res.provider_type == "mock"

    with pytest.raises(ValueError, match="Unsupported provider type"):
        ProviderFactory.create("anthropic-unsupported")


@pytest.mark.skipif(
    os.environ.get("AGENTFORGE_LIVE_INTEGRATION_TESTS") != "true"
    or not os.environ.get("GEMINI_API_KEY"),
    reason=(
        "Live Gemini integration test requires AGENTFORGE_LIVE_INTEGRATION_TESTS=true "
        "and GEMINI_API_KEY"
    ),
)
@pytest.mark.anyio
async def test_live_gemini_integration(sample_plan: ImplementationPlan) -> None:
    """Optional live test validating integration against Google Gemini endpoint."""
    provider = GeminiCodeGenerationProvider()
    request = GenerationRequest(
        generation_id="GEN-LIVE-01",
        plan=sample_plan,
        repository_path=".",
        permitted_files=["app/auth.py"],
    )
    changes = await provider.generate(
        request,
        {
            "prompt": (
                "Update auth.py to reject token 'expired'. Return JSON with changes."
            )
        },
    )
    assert len(changes) > 0
    assert provider.last_usage is not None
