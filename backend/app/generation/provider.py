"""Provider abstractions and implementations for code generation."""

import asyncio
from abc import ABC, abstractmethod
from typing import Any

import httpx

from backend.app.core.config import settings
from backend.app.generation.models import (
    GeneratedFileChange,
    GenerationRequest,
)
from backend.app.planning.models import ChangeType


class CodeGenerationProvider(ABC):
    """Abstract interface for replaceable code generation models."""

    @property
    @abstractmethod
    def provider_type(self) -> str:
        """Name of provider type (e.g., 'mock', 'gemini')."""
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


class GeminiCodeGenerationProvider(CodeGenerationProvider):
    """Optional real provider integrating with Google Gemini REST API."""

    def __init__(self, api_key: str | None = None, model: str = "gemini-1.5-flash"):
        self._api_key = api_key or (
            settings.GEMINI_API_KEY.get_secret_value()
            if settings.GEMINI_API_KEY
            else None
        )
        self._model = model

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
            f"{self._model}:generateContent?key={self._api_key}"
        )
        prompt = context_payload.get("prompt", "")

        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "responseMimeType": "application/json",
            },
        }

        async with httpx.AsyncClient(timeout=float(request.timeout_seconds)) as client:
            response = await client.post(url, json=payload)
            if response.status_code != 200:
                raise RuntimeError(
                    f"Gemini API error (HTTP {response.status_code}): {response.text}"
                )

            data = response.json()
            raw_text = (
                data.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "")
            )
            # Parse structured changes from JSON response
            return self._parse_json_changes(raw_text)

    def _parse_json_changes(self, raw_json: str) -> list[GeneratedFileChange]:
        """Parse structured changes returned by LLM JSON output."""
        import json

        data = json.loads(raw_json)
        changes_raw = data.get("changes", [])
        result: list[GeneratedFileChange] = []
        for item in changes_raw:
            result.append(
                GeneratedFileChange(
                    path=item["path"],
                    change_type=ChangeType(item["change_type"]),
                    content=item["content"],
                    task_ids=item.get("task_ids", []),
                    requirement_ids=item.get("requirement_ids", []),
                    metadata={"provider": "gemini"},
                )
            )
        return result


def get_provider(
    provider_type: str = "mock",
    **kwargs: Any,
) -> CodeGenerationProvider:
    """Factory creating configured CodeGenerationProvider instance."""
    normalized = provider_type.lower().strip()
    if normalized == "mock":
        return MockCodeGenerationProvider(**kwargs)
    if normalized == "gemini":
        return GeminiCodeGenerationProvider(**kwargs)
    raise ValueError(
        f"Unsupported provider type: '{provider_type}'. Supported: 'mock', 'gemini'."
    )
