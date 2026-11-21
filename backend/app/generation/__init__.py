"""Code Generation Engine package for AgentForge."""

from backend.app.generation.context import SourceContextBuilder
from backend.app.generation.generator import CodeGenerator
from backend.app.generation.models import (
    CreateGenerationRequest,
    FileContext,
    GeneratedFileChange,
    GenerationRequest,
    GenerationResult,
    GenerationStatus,
    PatchValidation,
    SourceContext,
)
from backend.app.generation.prompts import (
    PROMPT_VERSION,
    build_generation_prompt,
)
from backend.app.generation.provider import (
    CodeGenerationProvider,
    GeminiCodeGenerationProvider,
    MockCodeGenerationProvider,
    get_provider,
)

__all__ = [
    "PROMPT_VERSION",
    "CodeGenerationProvider",
    "CodeGenerator",
    "CreateGenerationRequest",
    "FileContext",
    "GeminiCodeGenerationProvider",
    "GeneratedFileChange",
    "GenerationRequest",
    "GenerationResult",
    "GenerationStatus",
    "MockCodeGenerationProvider",
    "PatchValidation",
    "SourceContext",
    "SourceContextBuilder",
    "build_generation_prompt",
    "get_provider",
]
