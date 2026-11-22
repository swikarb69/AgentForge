"""Code Review and Quality Assurance subsystem for AgentForge."""

from backend.app.review.exceptions import (
    ArtifactNotFoundError,
    DuplicateRuleError,
    ReviewError,
    ReviewNotFoundError,
    ReviewValidationError,
    RuleNotFoundError,
    RuleRegistryError,
)
from backend.app.review.models import (
    CodeEvidence,
    FindingCategory,
    FindingConfidence,
    FindingSeverity,
    ReviewConfiguration,
    ReviewFinding,
    ReviewReport,
    ReviewRequest,
    ReviewScope,
    ReviewStatus,
    ReviewSummary,
    redact_secrets,
)
from backend.app.review.registry import RuleRegistry, get_default_registry
from backend.app.review.rules import BUILTIN_RULES, ReviewRule
from backend.app.review.static_analyzer import (
    DeterministicStaticAnalyzer,
    compute_content_hash,
    extract_code_excerpt,
)

__all__ = [
    "ArtifactNotFoundError",
    "BUILTIN_RULES",
    "CodeEvidence",
    "DeterministicStaticAnalyzer",
    "DuplicateRuleError",
    "FindingCategory",
    "FindingConfidence",
    "FindingSeverity",
    "ReviewConfiguration",
    "ReviewError",
    "ReviewFinding",
    "ReviewNotFoundError",
    "ReviewReport",
    "ReviewRequest",
    "ReviewRule",
    "ReviewScope",
    "ReviewStatus",
    "ReviewSummary",
    "ReviewValidationError",
    "RuleNotFoundError",
    "RuleRegistry",
    "RuleRegistryError",
    "compute_content_hash",
    "extract_code_excerpt",
    "get_default_registry",
    "redact_secrets",
]
