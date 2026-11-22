"""Unit tests for ReviewRule and RuleRegistry."""

import pytest

from backend.app.review.exceptions import DuplicateRuleError, RuleNotFoundError
from backend.app.review.models import (
    FindingCategory,
    FindingConfidence,
    FindingSeverity,
)
from backend.app.review.registry import RuleRegistry, get_default_registry
from backend.app.review.rules import BUILTIN_RULES, ReviewRule


def test_builtin_rules_catalog_not_empty() -> None:
    """Verify built-in rule catalog has comprehensive coverage."""
    assert len(BUILTIN_RULES) >= 20
    rule_ids = [r.rule_id for r in BUILTIN_RULES]
    assert len(rule_ids) == len(set(rule_ids)), "Rule IDs must be unique"


def test_builtin_rules_categories_covered() -> None:
    """Verify all critical categories have representative rules."""
    categories = {r.category for r in BUILTIN_RULES}
    assert FindingCategory.CORRECTNESS in categories
    assert FindingCategory.SECURITY in categories
    assert FindingCategory.RELIABILITY in categories
    assert FindingCategory.MAINTAINABILITY in categories
    assert FindingCategory.PERFORMANCE in categories
    assert FindingCategory.ARCHITECTURE in categories
    assert FindingCategory.REGRESSION in categories
    assert FindingCategory.TESTING in categories


def test_registry_init_with_defaults() -> None:
    """Verify registry loads built-in rules by default."""
    registry = RuleRegistry(load_defaults=True)
    assert registry.count() == len(BUILTIN_RULES)
    rule = registry.get_rule("SEC-001")
    assert rule is not None
    assert rule.title == "Potential SQL Injection"
    assert rule.severity == FindingSeverity.CRITICAL


def test_registry_init_empty() -> None:
    """Verify registry can initialize empty without defaults."""
    registry = RuleRegistry(load_defaults=False)
    assert registry.count() == 0
    assert registry.get_rule("SEC-001") is None


def test_register_custom_rule() -> None:
    """Verify custom rule registration and retrieval."""
    registry = RuleRegistry(load_defaults=False)
    custom = ReviewRule(
        rule_id="CUSTOM-001",
        title="Custom Pattern Check",
        description="Verify custom standard",
        category=FindingCategory.MAINTAINABILITY,
        severity=FindingSeverity.LOW,
        confidence=FindingConfidence.HIGH,
    )
    registry.register(custom)
    assert registry.count() == 1
    retrieved = registry.get_rule("CUSTOM-001")
    assert retrieved is not None
    assert retrieved.title == "Custom Pattern Check"


def test_register_duplicate_rule_rejected() -> None:
    """Verify duplicate rule registration raises DuplicateRuleError."""
    registry = RuleRegistry(load_defaults=False)
    rule1 = ReviewRule(
        rule_id="DUP-001",
        title="Rule 1",
        description="First definition",
        category=FindingCategory.CORRECTNESS,
        severity=FindingSeverity.LOW,
    )
    rule2 = ReviewRule(
        rule_id="DUP-001",
        title="Rule 2",
        description="Second definition",
        category=FindingCategory.CORRECTNESS,
        severity=FindingSeverity.HIGH,
    )
    registry.register(rule1)
    with pytest.raises(
        DuplicateRuleError, match="Rule 'DUP-001' is already registered"
    ):
        registry.register(rule2, overwrite=False)

    # With overwrite=True, succeeds
    registry.register(rule2, overwrite=True)
    assert registry.get_rule("DUP-001") is not None
    assert registry.get_rule("DUP-001").severity == FindingSeverity.HIGH  # type: ignore[union-attr]


def test_unregister_rule() -> None:
    """Verify unregistering existing and non-existing rules."""
    registry = RuleRegistry(load_defaults=False)
    rule = ReviewRule(
        rule_id="TEMP-001",
        title="Temp",
        description="Temp rule",
        category=FindingCategory.TESTING,
        severity=FindingSeverity.LOW,
    )
    registry.register(rule)
    assert registry.unregister("TEMP-001") is True
    assert registry.unregister("TEMP-001") is False
    assert registry.get_rule("TEMP-001") is None


def test_list_rules_filtering() -> None:
    """Verify filtering rules by category and enabled status."""
    registry = RuleRegistry(load_defaults=True)
    sec_rules = registry.list_rules(category=FindingCategory.SECURITY)
    assert len(sec_rules) >= 5
    assert all(r.category == FindingCategory.SECURITY for r in sec_rules)

    # Disable one rule and test enabled_only filter
    registry.disable_rule("SEC-001")
    enabled_sec_rules = registry.list_rules(
        category=FindingCategory.SECURITY, enabled_only=True
    )
    assert len(enabled_sec_rules) == len(sec_rules) - 1
    assert all(r.rule_id != "SEC-001" for r in enabled_sec_rules)


def test_enable_and_disable_rule() -> None:
    """Verify toggling enabled state of rules."""
    registry = RuleRegistry(load_defaults=True)
    registry.disable_rule("CORR-001")
    rule = registry.get_rule("CORR-001")
    assert rule is not None
    assert rule.enabled is False

    registry.enable_rule("CORR-001")
    rule = registry.get_rule("CORR-001")
    assert rule is not None
    assert rule.enabled is True


def test_enable_disable_nonexistent_rule_raises() -> None:
    """Verify modifying non-existent rule raises RuleNotFoundError."""
    registry = RuleRegistry(load_defaults=False)
    with pytest.raises(RuleNotFoundError):
        registry.enable_rule("NON-EXISTENT")
    with pytest.raises(RuleNotFoundError):
        registry.disable_rule("NON-EXISTENT")


def test_clear_registry() -> None:
    """Verify clear method empties all registered rules."""
    registry = RuleRegistry(load_defaults=True)
    assert registry.count() > 0
    registry.clear()
    assert registry.count() == 0


def test_get_default_registry_singleton() -> None:
    """Verify get_default_registry returns consistent singleton."""
    reg1 = get_default_registry()
    reg2 = get_default_registry()
    assert reg1 is reg2
    assert reg1.count() >= 20
