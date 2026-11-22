"""Code Review and Quality Assurance subsystem for AgentForge."""

from backend.app.review.aggregator import ReviewAggregator
from backend.app.review.architecture_analyzer import ArchitectureAnalyzer
from backend.app.review.evidence import EvidenceCollector
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
from backend.app.review.regression_analyzer import RegressionAnalyzer
from backend.app.review.rules import BUILTIN_RULES, ReviewRule
from backend.app.review.security_analyzer import SecurityAnalyzer
from backend.app.review.static_analyzer import (
    DeterministicStaticAnalyzer,
    compute_content_hash,
    extract_code_excerpt,
)

__all__ = [
    "ArchitectureAnalyzer",
    "ArtifactNotFoundError",
    "BUILTIN_RULES",
    "CodeEvidence",
    "DeterministicStaticAnalyzer",
    "DuplicateRuleError",
    "EvidenceCollector",
    "FindingCategory",
    "FindingConfidence",
    "FindingSeverity",
    "RegressionAnalyzer",
    "ReviewAggregator",
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
    "SecurityAnalyzer",
    "compute_content_hash",
    "extract_code_excerpt",
    "get_default_registry",
    "redact_secrets",
]
