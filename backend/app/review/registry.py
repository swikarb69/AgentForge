"""Centralized registry for managing code review and QA rules."""

from backend.app.review.exceptions import DuplicateRuleError, RuleNotFoundError
from backend.app.review.models import FindingCategory
from backend.app.review.rules import BUILTIN_RULES, ReviewRule


class RuleRegistry:
    """Manages active, configurable rules for the code review subsystem."""

    def __init__(self, load_defaults: bool = True) -> None:
        self._rules: dict[str, ReviewRule] = {}
        if load_defaults:
            self.load_builtin_rules()

    def register(self, rule: ReviewRule, overwrite: bool = False) -> None:
        """Register a new review rule.

        Raises:
            DuplicateRuleError: If rule_id is already registered and overwrite is False.
        """
        if rule.rule_id in self._rules and not overwrite:
            raise DuplicateRuleError(
                f"Rule '{rule.rule_id}' is already registered in the registry."
            )
        self._rules[rule.rule_id] = rule.model_copy()

    def unregister(self, rule_id: str) -> bool:
        """Remove a rule by ID. Returns True if removed, False if not present."""
        if rule_id in self._rules:
            del self._rules[rule_id]
            return True
        return False

    def get_rule(self, rule_id: str) -> ReviewRule | None:
        """Retrieve a rule by its unique identifier."""
        return self._rules.get(rule_id)

    def list_rules(
        self,
        category: FindingCategory | None = None,
        enabled_only: bool = False,
    ) -> list[ReviewRule]:
        """List rules with optional filtering by category and enabled status."""
        results: list[ReviewRule] = []
        for rule in self._rules.values():
            if enabled_only and not rule.enabled:
                continue
            if category is not None and rule.category != category:
                continue
            results.append(rule.model_copy())
        return results

    def enable_rule(self, rule_id: str) -> None:
        """Enable a rule by ID.

        Raises:
            RuleNotFoundError: If rule_id does not exist.
        """
        rule = self._rules.get(rule_id)
        if rule is None:
            raise RuleNotFoundError(f"Rule '{rule_id}' not found in registry.")
        self._rules[rule_id] = rule.model_copy(update={"enabled": True})

    def disable_rule(self, rule_id: str) -> None:
        """Disable a rule by ID.

        Raises:
            RuleNotFoundError: If rule_id does not exist.
        """
        rule = self._rules.get(rule_id)
        if rule is None:
            raise RuleNotFoundError(f"Rule '{rule_id}' not found in registry.")
        self._rules[rule_id] = rule.model_copy(update={"enabled": False})

    def clear(self) -> None:
        """Remove all registered rules."""
        self._rules.clear()

    def load_builtin_rules(self, overwrite: bool = True) -> None:
        """Load standard built-in rules into the registry."""
        for rule in BUILTIN_RULES:
            self.register(rule, overwrite=overwrite)

    def count(self, enabled_only: bool = False) -> int:
        """Return the count of registered rules."""
        if enabled_only:
            return sum(1 for r in self._rules.values() if r.enabled)
        return len(self._rules)


_GLOBAL_REGISTRY: RuleRegistry | None = None


def get_default_registry() -> RuleRegistry:
    """Retrieve or initialize the global default RuleRegistry."""
    global _GLOBAL_REGISTRY
    if _GLOBAL_REGISTRY is None:
        _GLOBAL_REGISTRY = RuleRegistry(load_defaults=True)
    return _GLOBAL_REGISTRY
