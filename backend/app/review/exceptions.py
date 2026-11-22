"""Exceptions for the Code Review & Quality Assurance subsystem."""


class ReviewError(Exception):
    """Base exception for all review-related errors."""


class ReviewNotFoundError(ReviewError):
    """Raised when a requested review session or report is not found."""


class ReviewValidationError(ReviewError):
    """Raised when review input parameters or configuration fail validation."""


class ArtifactNotFoundError(ReviewError):
    """Raised when an upstream artifact (generation, execution, etc.) is missing."""


class RuleRegistryError(ReviewError):
    """Base error for rule registry operations."""


class DuplicateRuleError(RuleRegistryError):
    """Raised when attempting to register a rule with an existing rule_id."""


class RuleNotFoundError(RuleRegistryError):
    """Raised when looking up a rule_id that does not exist in registry."""
