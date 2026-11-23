"""Provider abstractions and implementations for code generation."""

import asyncio
import json
import logging
import re
from abc import ABC, abstractmethod
from typing import Any

import anyio
import httpx

from backend.app.core.config import settings
from backend.app.generation.models import (
    GeneratedFileChange,
    GenerationRequest,
)
from backend.app.planning.models import ChangeType

logger = logging.getLogger(__name__)


class CodeGenerationProvider(ABC):
    """Abstract interface for replaceable code generation models."""

    def __init__(self) -> None:
        self.last_usage: dict[str, Any] | None = None

    @property
    @abstractmethod
    def provider_type(self) -> str:
        """Name of provider type (e.g., 'mock', 'gemini', 'openai')."""
        ...

    @abstractmethod
    async def generate(
        self,
        request: GenerationRequest,
        context_payload: dict[str, Any],
    ) -> list[GeneratedFileChange]:
        """Generate structured code changes for authorized target files.

        Args:
            request: The generation request with plan and bounds.
            context_payload: Bounded source context, symbols, and tasks.

        Returns:
            List of GeneratedFileChange proposals.
        """
        ...


class MockCodeGenerationProvider(CodeGenerationProvider):
    """Deterministic mock provider for testing, evaluation, and offline development."""

    def __init__(
        self,
        custom_changes: list[GeneratedFileChange] | None = None,
        should_fail: bool = False,
        error_message: str = "Simulated mock provider failure",
        should_timeout: bool = False,
        out_of_scope_files: list[str] | None = None,
        empty_response: bool = False,
    ) -> None:
        super().__init__()
        self._custom_changes = custom_changes
        self._should_fail = should_fail
        self._error_message = error_message
        self._should_timeout = should_timeout
        self._out_of_scope_files = out_of_scope_files
        self._empty_response = empty_response
        self.call_count: int = 0
        self.last_request: GenerationRequest | None = None

    @property
    def provider_type(self) -> str:
        return "mock"

    async def generate(
        self,
        request: GenerationRequest,
        context_payload: dict[str, Any],
    ) -> list[GeneratedFileChange]:
        """Produce deterministic mock code proposals."""
        self.call_count += 1
        self.last_request = request

        if self._should_timeout:
            await asyncio.sleep(request.timeout_seconds + 0.1)
            raise TimeoutError("Mock generation timed out.")

        if self._should_fail:
            raise RuntimeError(self._error_message)

        if self._empty_response:
            return []

        if self._custom_changes is not None:
            return list(self._custom_changes)

        changes: list[GeneratedFileChange] = []
        file_contents: dict[str, str] = context_payload.get("file_contents", {})

        # 1. Generate modifications for authorized files
        files_to_modify = [fc.path for fc in request.plan.files_to_modify]
        for path in files_to_modify:
            original_code = file_contents.get(path, "")
            # Generate deterministic proposed modification
            proposed_code = self._generate_mock_modification(
                path=path,
                original_code=original_code,
                plan_objective=request.plan.objective,
            )
            matching_task_ids = [
                t.task_id for t in request.plan.tasks if path in t.target_files
            ]
            matching_req_ids = [
                req_id
                for t in request.plan.tasks
                if path in t.target_files
                for req_id in t.addressed_requirements
            ]
            changes.append(
                GeneratedFileChange(
                    path=path,
                    change_type=ChangeType.MODIFY,
                    content=proposed_code,
                    task_ids=list(dict.fromkeys(matching_task_ids)),
                    requirement_ids=list(dict.fromkeys(matching_req_ids)),
                    metadata={"provider": "mock", "mode": "deterministic"},
                )
            )

        # 2. Generate creations for new authorized files
        files_to_create = [fc.path for fc in request.plan.files_to_create]
        for path in files_to_create:
            proposed_code = self._generate_mock_creation(
                path=path,
                plan_objective=request.plan.objective,
            )
            matching_task_ids = [
                t.task_id for t in request.plan.tasks if path in t.target_files
            ]
            matching_req_ids = [
                req_id
                for t in request.plan.tasks
                if path in t.target_files
                for req_id in t.addressed_requirements
            ]
            changes.append(
                GeneratedFileChange(
                    path=path,
                    change_type=ChangeType.CREATE,
                    content=proposed_code,
                    task_ids=list(dict.fromkeys(matching_task_ids)),
                    requirement_ids=list(dict.fromkeys(matching_req_ids)),
                    metadata={"provider": "mock", "mode": "deterministic"},
                )
            )

        # 3. Simulate out-of-scope files if configured for testing validator
        if self._out_of_scope_files:
            for extra_path in self._out_of_scope_files:
                changes.append(
                    GeneratedFileChange(
                        path=extra_path,
                        change_type=ChangeType.MODIFY,
                        content="# Unauthorized extra modification\n",
                        task_ids=[],
                        requirement_ids=[],
                        metadata={"provider": "mock", "simulated_violation": True},
                    )
                )

        return changes

    def _generate_mock_modification(
        self,
        path: str,
        original_code: str,
        plan_objective: str,
    ) -> str:
        """Create clean mock proposed code for an existing file."""
        if not original_code:
            return f"# AgentForge Implementation for: {plan_objective}\n"

        # If verify_token is present in auth.py, add expired token validation logic
        if "def verify_token" in original_code:
            if "expired" not in original_code.lower():
                if "def verify_token(self, token: str)" in original_code:
                    replacement = (
                        "def verify_token(self, token: str) -> bool:\n"
                        '        """Validate token and check expiry."""\n'
                        '        if token == "expired":\n'
                        "            return False"
                    )
                    target_str = (
                        "def verify_token(self, token: str) -> bool:\n"
                        '        """Verify user authentication token."""'
                    )
                    return original_code.replace(
                        target_str,
                        replacement,
                    )
                if "def verify_token(token: str):" in original_code:
                    replacement = (
                        "def verify_token(token: str):\n"
                        '    """Validate token and check expiry."""\n'
                        '    if token == "expired":\n'
                        "        return False"
                    )
                    return original_code.replace(
                        "def verify_token(token: str):",
                        replacement,
                    )
        # Default modification: append structured patch comment
        banner = f"\n# [AgentForge Proposed Modification]: {plan_objective}\n"
        if banner not in original_code:
            return original_code.rstrip() + banner
        return original_code

    def _generate_mock_creation(
        self,
        path: str,
        plan_objective: str,
    ) -> str:
        """Create clean mock proposed code for a new file."""
        return (
            f'"""Implementation of {path} for AgentForge."""\n\n'
            f"# Objective: {plan_objective}\n\n"
            f"def handle_request() -> bool:\n"
            f'    """Fulfill {plan_objective}."""\n'
            f"    return True\n"
        )


def _build_changes_from_json(
    items: list[dict[str, Any]],
    provider_name: str,
    usage: dict[str, Any] | None = None,
) -> list[GeneratedFileChange]:
    """Build GeneratedFileChange list from parsed JSON items."""
    results: list[GeneratedFileChange] = []
    meta: dict[str, Any] = {"provider": provider_name}
    if usage:
        meta["usage"] = usage

    for item in items:
        change_type_val = item.get("change_type", "modify")
        try:
            change_type = ChangeType(change_type_val)
        except ValueError:
            change_type = ChangeType.MODIFY

        results.append(
            GeneratedFileChange(
                path=item["path"],
                change_type=change_type,
                content=item["content"],
                task_ids=item.get("task_ids", []),
                requirement_ids=item.get("requirement_ids", []),
                metadata=dict(meta),
            )
        )
    return results


def _parse_markdown_code_blocks(
    raw_text: str,
    provider_name: str,
    request: GenerationRequest | None = None,
    usage: dict[str, Any] | None = None,
) -> list[GeneratedFileChange]:
    """Fallback parser extracting code proposals from markdown fences."""
    fence_pattern = re.compile(
        r"```(?:[a-zA-Z0-9_\-]+)?(?::([^\n]+))?\n(.*?)```",
        re.DOTALL,
    )
    matches = list(fence_pattern.finditer(raw_text))

    permitted_files = request.permitted_files if request else []
    plan = request.plan if request else None
    results: list[GeneratedFileChange] = []
    meta: dict[str, Any] = {"provider": provider_name, "fallback_parsed": True}
    if usage:
        meta["usage"] = usage

    if matches:
        for idx, match in enumerate(matches):
            fence_file = (match.group(1) or "").strip()
            content = match.group(2)

            target_path = fence_file
            if not target_path:
                pre_text = raw_text[: match.start()].splitlines()
                for line in reversed(pre_text[-5:]):
                    for pf in permitted_files:
                        if pf in line:
                            target_path = pf
                            break
                    if target_path:
                        break

            if not target_path and idx < len(permitted_files):
                target_path = permitted_files[idx]
            elif not target_path and permitted_files:
                target_path = permitted_files[0]

            if not target_path:
                continue

            change_type = ChangeType.MODIFY
            if plan:
                create_paths = [fc.path for fc in plan.files_to_create]
                if target_path in create_paths:
                    change_type = ChangeType.CREATE

            task_ids: list[str] = []
            req_ids: list[str] = []
            if plan:
                for task in plan.tasks:
                    if target_path in task.target_files:
                        task_ids.append(task.task_id)
                        req_ids.extend(task.addressed_requirements)

            results.append(
                GeneratedFileChange(
                    path=target_path,
                    change_type=change_type,
                    content=content,
                    task_ids=list(dict.fromkeys(task_ids)),
                    requirement_ids=list(dict.fromkeys(req_ids)),
                    metadata=dict(meta),
                )
            )

    if not results and permitted_files:
        single_path = permitted_files[0]
        results.append(
            GeneratedFileChange(
                path=single_path,
                change_type=ChangeType.MODIFY,
                content=raw_text.strip(),
                task_ids=[],
                requirement_ids=[],
                metadata=dict(meta),
            )
        )

    return results


def parse_llm_code_response(
    raw_text: str,
    provider_name: str,
    request: GenerationRequest | None = None,
    usage: dict[str, Any] | None = None,
) -> list[GeneratedFileChange]:
    """Parse LLM output into structured GeneratedFileChange proposals.

    Handles valid JSON, markdown-wrapped JSON, and fallback markdown code fences.
    """
    cleaned = raw_text.strip()

    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    try:
        data = json.loads(cleaned)
        if isinstance(data, dict) and "changes" in data:
            return _build_changes_from_json(data["changes"], provider_name, usage)
    except (json.JSONDecodeError, ValueError, KeyError) as err:
        logger.debug("Direct JSON decode failed, trying substring: %s", err)

    first_brace = raw_text.find("{")
    last_brace = raw_text.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        candidate = raw_text[first_brace : last_brace + 1]
        try:
            data = json.loads(candidate)
            if isinstance(data, dict) and "changes" in data:
                return _build_changes_from_json(data["changes"], provider_name, usage)
        except (json.JSONDecodeError, ValueError, KeyError) as err:
            logger.debug("Substring JSON decode failed: %s", err)

    return _parse_markdown_code_blocks(raw_text, provider_name, request, usage)


class GeminiCodeGenerationProvider(CodeGenerationProvider):
    """Production provider integrating with Google Gemini REST API using header auth."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        max_attempts: int = 3,
        initial_delay: float = 1.0,
        backoff_factor: float = 2.0,
        max_delay: float = 8.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__()
        self._api_key = api_key or (
            settings.GEMINI_API_KEY.get_secret_value()
            if settings.GEMINI_API_KEY
            else None
        )
        self._model = model or settings.GEMINI_MODEL
        self._max_attempts = max_attempts
        self._initial_delay = initial_delay
        self._backoff_factor = backoff_factor
        self._max_delay = max_delay
        self._http_client = http_client

    @property
    def provider_type(self) -> str:
        return "gemini"

    async def generate(
        self,
        request: GenerationRequest,
        context_payload: dict[str, Any],
    ) -> list[GeneratedFileChange]:
        """Invoke Gemini API to generate structured code changes."""
        if not self._api_key:
            raise ValueError(
                "Gemini API key is not configured. Set GEMINI_API_KEY in environment."
            )

        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self._model}:generateContent"
        )
        headers = {
            "x-goog-api-key": self._api_key,
            "Content-Type": "application/json",
        }
        prompt = context_payload.get("prompt", "")

        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "responseMimeType": "application/json",
            },
        }

        async def _execute_with_client(client: httpx.AsyncClient) -> httpx.Response:
            last_err: Exception | None = None
            for attempt in range(1, self._max_attempts + 1):
                try:
                    response = await client.post(url, headers=headers, json=payload)
                    if response.status_code == 200:
                        return response

                    if response.status_code in (429, 500, 502, 503, 504):
                        last_err = RuntimeError(
                            f"Gemini API error (HTTP {response.status_code}): "
                            f"{response.text}"
                        )
                        if attempt < self._max_attempts:
                            delay = min(
                                self._initial_delay
                                * (self._backoff_factor ** (attempt - 1)),
                                self._max_delay,
                            )
                            logger.warning(
                                "Gemini API rate limit or server error (HTTP %d). "
                                "Retrying %d/%d after %.2fs.",
                                response.status_code,
                                attempt,
                                self._max_attempts,
                                delay,
                            )
                            await anyio.sleep(delay)
                            continue
                        raise last_err

                    raise RuntimeError(
                        f"Gemini API error (HTTP {response.status_code}): "
                        f"{response.text}"
                    )
                except (httpx.NetworkError, httpx.TimeoutException) as exc:
                    last_err = exc
                    if attempt < self._max_attempts:
                        delay = min(
                            self._initial_delay
                            * (self._backoff_factor ** (attempt - 1)),
                            self._max_delay,
                        )
                        logger.warning(
                            "Gemini API network error: %s. Retrying %d/%d after %.2fs.",
                            exc,
                            attempt,
                            self._max_attempts,
                            delay,
                        )
                        await anyio.sleep(delay)
                        continue
                    raise

            if last_err is not None:
                raise last_err
            raise RuntimeError("Gemini API call failed with unknown error.")

        if self._http_client is not None:
            resp = await _execute_with_client(self._http_client)
        else:
            async with httpx.AsyncClient(
                timeout=float(request.timeout_seconds)
            ) as client:
                resp = await _execute_with_client(client)

        data = resp.json()

        if usage_meta := data.get("usageMetadata"):
            self.last_usage = {
                "prompt_tokens": usage_meta.get("promptTokenCount", 0),
                "completion_tokens": usage_meta.get("candidatesTokenCount", 0),
                "total_tokens": usage_meta.get("totalTokenCount", 0),
            }

        candidates = data.get("candidates", [])
        if not candidates:
            return []

        raw_text = (
            candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        )

        return parse_llm_code_response(
            raw_text=raw_text,
            provider_name="gemini",
            request=request,
            usage=self.last_usage,
        )


class OpenAICodeGenerationProvider(CodeGenerationProvider):
    """Production provider integrating with OpenAI Chat Completions API."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        max_attempts: int = 3,
        initial_delay: float = 1.0,
        backoff_factor: float = 2.0,
        max_delay: float = 8.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__()
        self._api_key = api_key or (
            settings.OPENAI_API_KEY.get_secret_value()
            if settings.OPENAI_API_KEY
            else None
        )
        self._model = model or settings.OPENAI_MODEL
        self._max_attempts = max_attempts
        self._initial_delay = initial_delay
        self._backoff_factor = backoff_factor
        self._max_delay = max_delay
        self._http_client = http_client

    @property
    def provider_type(self) -> str:
        return "openai"

    async def generate(
        self,
        request: GenerationRequest,
        context_payload: dict[str, Any],
    ) -> list[GeneratedFileChange]:
        """Invoke OpenAI API to generate structured code changes."""
        if not self._api_key:
            raise ValueError(
                "OpenAI API key is not configured. Set OPENAI_API_KEY in environment."
            )

        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        prompt = context_payload.get("prompt", "")

        payload = {
            "model": self._model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are an autonomous AI software engineer. "
                        "Return valid JSON matching the requested schema."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
        }

        async def _execute_with_client(client: httpx.AsyncClient) -> httpx.Response:
            last_err: Exception | None = None
            for attempt in range(1, self._max_attempts + 1):
                try:
                    response = await client.post(url, headers=headers, json=payload)
                    if response.status_code == 200:
                        return response

                    if response.status_code in (429, 500, 502, 503, 504):
                        last_err = RuntimeError(
                            f"OpenAI API error (HTTP {response.status_code}): "
                            f"{response.text}"
                        )
                        if attempt < self._max_attempts:
                            delay = min(
                                self._initial_delay
                                * (self._backoff_factor ** (attempt - 1)),
                                self._max_delay,
                            )
                            logger.warning(
                                "OpenAI API rate limit or server error (HTTP %d). "
                                "Retrying %d/%d after %.2fs.",
                                response.status_code,
                                attempt,
                                self._max_attempts,
                                delay,
                            )
                            await anyio.sleep(delay)
                            continue
                        raise last_err

                    raise RuntimeError(
                        f"OpenAI API error (HTTP {response.status_code}): "
                        f"{response.text}"
                    )
                except (httpx.NetworkError, httpx.TimeoutException) as exc:
                    last_err = exc
                    if attempt < self._max_attempts:
                        delay = min(
                            self._initial_delay
                            * (self._backoff_factor ** (attempt - 1)),
                            self._max_delay,
                        )
                        logger.warning(
                            "OpenAI API network error: %s. Retrying %d/%d after %.2fs.",
                            exc,
                            attempt,
                            self._max_attempts,
                            delay,
                        )
                        await anyio.sleep(delay)
                        continue
                    raise

            if last_err is not None:
                raise last_err
            raise RuntimeError("OpenAI API call failed with unknown error.")

        if self._http_client is not None:
            resp = await _execute_with_client(self._http_client)
        else:
            async with httpx.AsyncClient(
                timeout=float(request.timeout_seconds)
            ) as client:
                resp = await _execute_with_client(client)

        data = resp.json()

        if usage_meta := data.get("usage"):
            self.last_usage = {
                "prompt_tokens": usage_meta.get("prompt_tokens", 0),
                "completion_tokens": usage_meta.get("completion_tokens", 0),
                "total_tokens": usage_meta.get("total_tokens", 0),
            }

        choices = data.get("choices", [])
        if not choices:
            return []

        raw_text = choices[0].get("message", {}).get("content", "")

        return parse_llm_code_response(
            raw_text=raw_text,
            provider_name="openai",
            request=request,
            usage=self.last_usage,
        )


class ProviderFactory:
    """Factory creating configured CodeGenerationProvider instances."""

    @staticmethod
    def create(
        provider_type: str = "mock",
        **kwargs: Any,
    ) -> CodeGenerationProvider:
        """Create and return a configured provider instance."""
        normalized = provider_type.lower().strip()
        if normalized == "mock":
            return MockCodeGenerationProvider(**kwargs)
        if normalized == "gemini":
            return GeminiCodeGenerationProvider(**kwargs)
        if normalized == "openai":
            return OpenAICodeGenerationProvider(**kwargs)
        raise ValueError(
            f"Unsupported provider type: '{provider_type}'. "
            "Supported: 'mock', 'gemini', 'openai'."
        )


def get_provider(
    provider_type: str = "mock",
    **kwargs: Any,
) -> CodeGenerationProvider:
    """Factory creating configured CodeGenerationProvider instance."""
    return ProviderFactory.create(provider_type, **kwargs)
