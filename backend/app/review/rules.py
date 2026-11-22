"""Rule specifications and core rule catalog for code review."""

from pydantic import BaseModel, Field

from backend.app.review.models import (
    FindingCategory,
    FindingConfidence,
    FindingSeverity,
)


class ReviewRule(BaseModel):
    """Specification of an individual code review or quality assurance rule."""

    rule_id: str = Field(description="Unique rule code, e.g., 'SEC-001'")
    title: str = Field(description="Short title summarizing rule purpose")
    description: str = Field(description="Detailed explanation of pattern detected")
    category: FindingCategory = Field(description="Primary review category")
    severity: FindingSeverity = Field(description="Default severity rating")
    confidence: FindingConfidence = Field(
        default=FindingConfidence.HIGH,
        description="Default confidence level for detections",
    )
    enabled: bool = Field(default=True, description="Whether rule is active by default")
    remediation_guidance: str = Field(
        default="", description="Actionable advice on resolving findings"
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Categorical or standards tags (e.g. 'cwe-89')",
    )
    requires_human_review: bool = Field(
        default=False,
        description="Whether triggered findings require explicit human approval",
    )


# ---------------------------------------------------------------------------
# Core Rule Catalog
# ---------------------------------------------------------------------------

BUILTIN_RULES: list[ReviewRule] = [
    # --- Correctness ---
    ReviewRule(
        rule_id="CORR-001",
        title="Undefined Name Usage",
        description=(
            "Referencing a variable, function, or symbol that is not defined "
            "or imported."
        ),
        category=FindingCategory.CORRECTNESS,
        severity=FindingSeverity.HIGH,
        confidence=FindingConfidence.HIGH,
        remediation_guidance=(
            "Ensure the symbol is defined in scope or properly imported."
        ),
        tags=["correctness", "ast", "runtime-error"],
    ),
    ReviewRule(
        rule_id="CORR-002",
        title="Unreachable Code Detected",
        description=(
            "Statements placed after return, raise, break, or continue will "
            "never execute."
        ),
        category=FindingCategory.CORRECTNESS,
        severity=FindingSeverity.LOW,
        confidence=FindingConfidence.HIGH,
        remediation_guidance="Remove or relocate unreachable statements.",
        tags=["correctness", "dead-code"],
    ),
    ReviewRule(
        rule_id="CORR-003",
        title="Mutable Default Argument",
        description=(
            "Using mutable default arguments (lists, dicts, sets) can cause "
            "unexpected state retention across function invocations."
        ),
        category=FindingCategory.CORRECTNESS,
        severity=FindingSeverity.MEDIUM,
        confidence=FindingConfidence.HIGH,
        remediation_guidance=(
            "Use None as default and initialize the mutable structure inside "
            "the function body."
        ),
        tags=["correctness", "python-gotcha"],
    ),
    ReviewRule(
        rule_id="CORR-004",
        title="Suspicious Identity Comparison",
        description=(
            "Using 'is' or 'is not' to compare literals (e.g., strings or "
            "integers) instead of equality operators ('==' or '!=')."
        ),
        category=FindingCategory.CORRECTNESS,
        severity=FindingSeverity.MEDIUM,
        confidence=FindingConfidence.HIGH,
        remediation_guidance="Use '==' or '!=' for value comparisons.",
        tags=["correctness", "identity-comparison"],
    ),
    # --- Security ---
    ReviewRule(
        rule_id="SEC-001",
        title="Potential SQL Injection",
        description=(
            "Constructing SQL statements using string formatting or concatenation "
            "rather than parameterized queries."
        ),
        category=FindingCategory.SECURITY,
        severity=FindingSeverity.CRITICAL,
        confidence=FindingConfidence.HIGH,
        remediation_guidance=(
            "Use parameterized queries or ORM builders to pass user inputs safely."
        ),
        tags=["security", "cwe-89", "injection"],
        requires_human_review=True,
    ),
    ReviewRule(
        rule_id="SEC-002",
        title="Unsafe Shell Command Execution",
        description=(
            "Invoking shell commands with shell=True or unvalidated user input "
            "exposes system to command injection."
        ),
        category=FindingCategory.SECURITY,
        severity=FindingSeverity.CRITICAL,
        confidence=FindingConfidence.HIGH,
        remediation_guidance=(
            "Avoid shell=True; pass arguments as an explicit sequence without "
            "invoking system shells."
        ),
        tags=["security", "cwe-78", "command-injection"],
        requires_human_review=True,
    ),
    ReviewRule(
        rule_id="SEC-003",
        title="Dynamic Code Execution",
        description=(
            "Using eval(), exec(), or compile() on dynamic input allows "
            "arbitrary code execution."
        ),
        category=FindingCategory.SECURITY,
        severity=FindingSeverity.CRITICAL,
        confidence=FindingConfidence.HIGH,
        remediation_guidance=(
            "Replace dynamic evaluation with static data parsers (e.g., "
            "ast.literal_eval or json.loads)."
        ),
        tags=["security", "cwe-95", "rce"],
        requires_human_review=True,
    ),
    ReviewRule(
        rule_id="SEC-004",
        title="Hardcoded Secret or Token",
        description=(
            "Plaintext credentials, secret keys, or authentication tokens "
            "embedded directly in code."
        ),
        category=FindingCategory.SECURITY,
        severity=FindingSeverity.HIGH,
        confidence=FindingConfidence.MEDIUM,
        remediation_guidance=(
            "Store secrets in environment variables or a dedicated secret "
            "management service."
        ),
        tags=["security", "cwe-798", "secrets"],
        requires_human_review=True,
    ),
    ReviewRule(
        rule_id="SEC-005",
        title="Path Traversal Risk",
        description=(
            "Accessing filesystem paths constructed from unvalidated inputs "
            "without sanitization."
        ),
        category=FindingCategory.SECURITY,
        severity=FindingSeverity.HIGH,
        confidence=FindingConfidence.HIGH,
        remediation_guidance=(
            "Resolve paths and verify they reside within the intended base directory."
        ),
        tags=["security", "cwe-22", "path-traversal"],
        requires_human_review=True,
    ),
    ReviewRule(
        rule_id="SEC-006",
        title="Insecure Deserialization",
        description=(
            "Unpickling untrusted data using pickle or marshal can result in "
            "remote code execution."
        ),
        category=FindingCategory.SECURITY,
        severity=FindingSeverity.CRITICAL,
        confidence=FindingConfidence.HIGH,
        remediation_guidance=(
            "Use secure serialization formats such as JSON or Protocol Buffers."
        ),
        tags=["security", "cwe-502", "deserialization"],
        requires_human_review=True,
    ),
    ReviewRule(
        rule_id="SEC-007",
        title="Insecure Temporary File Creation",
        description=(
            "Using mktemp() or unsafe file paths creates race conditions (TOCTOU)."
        ),
        category=FindingCategory.SECURITY,
        severity=FindingSeverity.MEDIUM,
        confidence=FindingConfidence.HIGH,
        remediation_guidance=(
            "Use tempfile.NamedTemporaryFile or tempfile.TemporaryDirectory."
        ),
        tags=["security", "cwe-377", "insecure-temp"],
    ),
    # --- Reliability ---
    ReviewRule(
        rule_id="REL-001",
        title="Bare Exception Handler",
        description=(
            "Catching all exceptions with a bare 'except:' catches system signals "
            "like KeyboardInterrupt and SystemExit."
        ),
        category=FindingCategory.RELIABILITY,
        severity=FindingSeverity.MEDIUM,
        confidence=FindingConfidence.HIGH,
        remediation_guidance=(
            "Catch specific exception classes or at minimum 'except Exception:'."
        ),
        tags=["reliability", "exceptions"],
    ),
    ReviewRule(
        rule_id="REL-002",
        title="Silent / Empty Exception Suppression",
        description=(
            "Catching exceptions and silently ignoring them with 'pass' hides "
            "errors and impairs diagnosability."
        ),
        category=FindingCategory.RELIABILITY,
        severity=FindingSeverity.MEDIUM,
        confidence=FindingConfidence.HIGH,
        remediation_guidance=(
            "Log caught exceptions or explicitly explain why suppression is safe."
        ),
        tags=["reliability", "suppression"],
    ),
    ReviewRule(
        rule_id="REL-003",
        title="Unhandled Resource Cleanup",
        description=(
            "Opening files or connections without context managers ('with' "
            "statements) risks file descriptor or connection leakage."
        ),
        category=FindingCategory.RELIABILITY,
        severity=FindingSeverity.LOW,
        confidence=FindingConfidence.MEDIUM,
        remediation_guidance=(
            "Use context managers ('with open(...)') or ensure cleanup in a "
            "finally block."
        ),
        tags=["reliability", "resource-leak"],
    ),
    ReviewRule(
        rule_id="REL-004",
        title="Global Mutable State Modification",
        description=(
            "Modifying module-level global variables inside functions can lead "
            "to race conditions and test pollution."
        ),
        category=FindingCategory.RELIABILITY,
        severity=FindingSeverity.LOW,
        confidence=FindingConfidence.HIGH,
        remediation_guidance=(
            "Encapsulate state within class instances or pass as explicit parameters."
        ),
        tags=["reliability", "global-state"],
    ),
    # --- Maintainability ---
    ReviewRule(
        rule_id="MAINT-001",
        title="Excessively Long Function",
        description=(
            "Functions exceeding 50 statements or lines are difficult to comprehend, "
            "test, and maintain."
        ),
        category=FindingCategory.MAINTAINABILITY,
        severity=FindingSeverity.LOW,
        confidence=FindingConfidence.HIGH,
        remediation_guidance=(
            "Decompose long functions into smaller, single-purpose helper functions."
        ),
        tags=["maintainability", "refactoring"],
    ),
    ReviewRule(
        rule_id="MAINT-002",
        title="Excessive Function Parameters",
        description=(
            "Functions accepting more than 6 parameters indicate high coupling "
            "and should be encapsulated."
        ),
        category=FindingCategory.MAINTAINABILITY,
        severity=FindingSeverity.LOW,
        confidence=FindingConfidence.HIGH,
        remediation_guidance=(
            "Encapsulate related parameters into a configuration model or dataclass."
        ),
        tags=["maintainability", "parameter-list"],
    ),
    ReviewRule(
        rule_id="MAINT-003",
        title="Deeply Nested Control Flow",
        description=(
            "Control structures nested deeper than 4 levels impair readability "
            "and increase cognitive complexity."
        ),
        category=FindingCategory.MAINTAINABILITY,
        severity=FindingSeverity.LOW,
        confidence=FindingConfidence.HIGH,
        remediation_guidance=(
            "Use early returns/guard clauses or extract inner blocks into separate "
            "functions."
        ),
        tags=["maintainability", "nesting"],
    ),
    ReviewRule(
        rule_id="MAINT-004",
        title="High Cyclomatic Complexity",
        description=(
            "Functions with excessive branching paths are prone to regression "
            "bugs and hard to thoroughly test."
        ),
        category=FindingCategory.MAINTAINABILITY,
        severity=FindingSeverity.MEDIUM,
        confidence=FindingConfidence.HIGH,
        remediation_guidance=(
            "Refactor decision branches and simplify conditional expressions."
        ),
        tags=["maintainability", "complexity"],
    ),
    ReviewRule(
        rule_id="MAINT-005",
        title="Missing Function Docstring",
        description=(
            "Public functions and methods should have clear docstrings explaining "
            "purpose and parameters."
        ),
        category=FindingCategory.MAINTAINABILITY,
        severity=FindingSeverity.INFORMATIONAL,
        confidence=FindingConfidence.HIGH,
        remediation_guidance=(
            "Add a concise docstring describing behavior, parameters, and return types."
        ),
        tags=["maintainability", "documentation"],
    ),
    # --- Performance ---
    ReviewRule(
        rule_id="PERF-001",
        title="Quadratic Loop Pattern",
        description=(
            "Iterating over a collection inside another loop without indexing or "
            "mapping creates O(n^2) complexity."
        ),
        category=FindingCategory.PERFORMANCE,
        severity=FindingSeverity.MEDIUM,
        confidence=FindingConfidence.MEDIUM,
        remediation_guidance=(
            "Pre-index the collection into a set or dict for O(1) lookups."
        ),
        tags=["performance", "algorithmic-complexity"],
    ),
    ReviewRule(
        rule_id="PERF-002",
        title="Repeated Inefficient Computation in Loop",
        description=(
            "Calling expensive functions or compiling regex patterns repeatedly "
            "inside a loop."
        ),
        category=FindingCategory.PERFORMANCE,
        severity=FindingSeverity.LOW,
        confidence=FindingConfidence.HIGH,
        remediation_guidance=(
            "Hoist invariant computations or regex compilation outside the loop."
        ),
        tags=["performance", "hoisting"],
    ),
    # --- Architecture ---
    ReviewRule(
        rule_id="ARCH-001",
        title="Circular Import Dependency",
        description=(
            "Module imports form a circular dependency loop, leading to runtime "
            "initialization errors."
        ),
        category=FindingCategory.ARCHITECTURE,
        severity=FindingSeverity.HIGH,
        confidence=FindingConfidence.HIGH,
        remediation_guidance=(
            "Extract shared interfaces/models into a lower-tier module or restructure "
            "dependencies."
        ),
        tags=["architecture", "circular-imports"],
        requires_human_review=True,
    ),
    ReviewRule(
        rule_id="ARCH-002",
        title="Layer Boundary Violation",
        description=(
            "Lower-tier business or data modules directly importing higher-tier "
            "presentation or API routes."
        ),
        category=FindingCategory.ARCHITECTURE,
        severity=FindingSeverity.HIGH,
        confidence=FindingConfidence.HIGH,
        remediation_guidance=(
            "Invert dependency using dependency injection or service interfaces."
        ),
        tags=["architecture", "layering"],
    ),
    # --- Regression ---
    ReviewRule(
        rule_id="REG-001",
        title="Unauthorized File Modification",
        description=(
            "Code changes modify files outside the authorized scope specified by "
            "the implementation plan."
        ),
        category=FindingCategory.REGRESSION,
        severity=FindingSeverity.HIGH,
        confidence=FindingConfidence.HIGH,
        remediation_guidance=(
            "Restrict changes strictly to files authorized by the plan or update "
            "the plan first."
        ),
        tags=["regression", "scope-creep"],
        requires_human_review=True,
    ),
    ReviewRule(
        rule_id="REG-002",
        title="Missing Planned Target Implementation",
        description=(
            "Files or tasks designated for change in the implementation plan were "
            "omitted from code generation."
        ),
        category=FindingCategory.REGRESSION,
        severity=FindingSeverity.MEDIUM,
        confidence=FindingConfidence.HIGH,
        remediation_guidance=(
            "Ensure all authorized target files in the plan receive their planned "
            "modifications."
        ),
        tags=["regression", "completeness"],
    ),
    ReviewRule(
        rule_id="REG-003",
        title="Unresolved Test Execution Failure",
        description=(
            "Verification report indicates tests failed and were not repaired prior "
            "to code review."
        ),
        category=FindingCategory.REGRESSION,
        severity=FindingSeverity.CRITICAL,
        confidence=FindingConfidence.HIGH,
        remediation_guidance=(
            "Resolve test failures through automated repair or manual intervention "
            "before approval."
        ),
        tags=["regression", "broken-tests"],
        requires_human_review=True,
    ),
    ReviewRule(
        rule_id="REG-004",
        title="Test Regression Detected",
        description="Repairs or changes caused previously passing tests to fail.",
        category=FindingCategory.REGRESSION,
        severity=FindingSeverity.CRITICAL,
        confidence=FindingConfidence.HIGH,
        remediation_guidance=(
            "Revert breaking changes and preserve previously working test assertions."
        ),
        tags=["regression", "regression-tests"],
        requires_human_review=True,
    ),
    # --- Testing ---
    ReviewRule(
        rule_id="TEST-001",
        title="Empty Test Function",
        description="Test function contains no assertions or executable statements.",
        category=FindingCategory.TESTING,
        severity=FindingSeverity.MEDIUM,
        confidence=FindingConfidence.HIGH,
        remediation_guidance=(
            "Implement meaningful assertions verifying the expected functionality."
        ),
        tags=["testing", "empty-test"],
    ),
    ReviewRule(
        rule_id="TEST-002",
        title="Disabled or Skipped Test",
        description=(
            "Test marked as skip or commented out without documented rationale."
        ),
        category=FindingCategory.TESTING,
        severity=FindingSeverity.LOW,
        confidence=FindingConfidence.HIGH,
        remediation_guidance=(
            "Fix the underlying issue or document why skipping is legitimate."
        ),
        tags=["testing", "skipped-test"],
    ),
]
