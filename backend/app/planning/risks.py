"""Engineering risk, assumption, and ambiguity analysis engine."""

from backend.app.issue.models import IssueContext
from backend.app.planning.impact import ImpactResult
from backend.app.planning.models import Assumption, Risk, RiskSeverity


class RiskAnalyzer:
    """Evaluates codebase changes for architectural, security, and stability risks."""

    SECURITY_KEYWORDS = {
        "auth",
        "token",
        "jwt",
        "password",
        "secret",
        "permission",
        "credential",
        "login",
        "crypto",
        "oauth",
        "session",
        "access_token",
    }

    DB_KEYWORDS = {
        "db",
        "database",
        "migration",
        "schema",
        "sql",
        "table",
        "query",
        "transaction",
    }

    def analyze_risks(
        self,
        context: IssueContext,
        impact: ImpactResult,
    ) -> list[Risk]:
        """Identify concrete engineering risks from IssueContext and ImpactResult.

        Returns:
            List of grounded Risk items with severity and actionable mitigations.
        """
        risks: list[Risk] = []
        combined_text = (
            f"{context.analysis.issue.title}\n"
            f"{context.analysis.issue.description}\n"
            f"{' '.join(impact.direct_files)}\n"
            f"{' '.join(s.symbol_name for s in impact.symbol_changes)}"
        ).lower()

        # 1. Security / Authentication Risk
        if any(kw in combined_text for kw in self.SECURITY_KEYWORDS):
            risks.append(
                Risk(
                    risk="Security-sensitive authentication logic modified",
                    severity=RiskSeverity.HIGH,
                    reason=(
                        "Changes touch authentication, token verification, "
                        "or credential paths which may cause auth bypass."
                    ),
                    mitigation=(
                        "Preserve existing valid credential handling, verify expiry "
                        "and signature rules, and execute auth test suites."
                    ),
                )
            )

        # 2. API Compatibility Risk
        if context.analysis.endpoints or any(
            "api" in f or "routes" in f for f in impact.direct_files
        ):
            endpoint_names = (
                ", ".join(f"{e.method} {e.path}" for e in context.analysis.endpoints)
                if context.analysis.endpoints
                else "API routes"
            )
            risks.append(
                Risk(
                    risk="Potential breaking change to external HTTP API endpoints",
                    severity=RiskSeverity.MEDIUM,
                    reason=(
                        f"Modifications touch {endpoint_names}, which may disrupt "
                        "downstream client integrations."
                    ),
                    mitigation=(
                        "Preserve endpoint contract schemas, HTTP status codes, "
                        "and error response structures."
                    ),
                )
            )

        # 3. Database Schema / Transaction Risk
        if any(kw in combined_text for kw in self.DB_KEYWORDS):
            risks.append(
                Risk(
                    risk="Database schema or persistence interaction risk",
                    severity=RiskSeverity.HIGH,
                    reason=(
                        "Modifications involve database access or schema patterns "
                        "which could induce locking or migration failures."
                    ),
                    mitigation=(
                        "Ensure transaction isolation, test rollbacks, and maintain "
                        "backward-compatible schema migrations."
                    ),
                )
            )

        # 4. Missing Tests Risk
        if impact.tests_to_add and not impact.existing_tests:
            test_list = ", ".join(impact.tests_to_add)
            risks.append(
                Risk(
                    risk="Absence of pre-existing test suite for target components",
                    severity=RiskSeverity.MEDIUM,
                    reason=(
                        f"New test files must be created ({test_list}) as no "
                        "baseline tests currently cover these components."
                    ),
                    mitigation=(
                        "Establish comprehensive test baseline before modifying "
                        "source logic."
                    ),
                )
            )

        # 5. Unresolved References Risk
        if context.unresolved_references:
            refs_str = ", ".join(context.unresolved_references)
            risks.append(
                Risk(
                    risk="Unresolved repository entity references in issue",
                    severity=RiskSeverity.HIGH,
                    reason=(
                        f"References {refs_str} could not be resolved against "
                        "existing repository symbols or files."
                    ),
                    mitigation=(
                        "Confirm target entity names with maintainers or verify "
                        "if new file creation is intended."
                    ),
                )
            )

        # 6. Ambiguous Requirements Risk
        if context.analysis.is_ambiguous:
            reasons = "; ".join(context.analysis.ambiguity_reasons)
            risks.append(
                Risk(
                    risk="Ambiguous or underspecified issue requirements",
                    severity=RiskSeverity.HIGH,
                    reason=(
                        reasons or "Issue description lacks technical specifications."
                    ),
                    mitigation=(
                        "Request requirements clarification from author before "
                        "executing changes."
                    ),
                )
            )

        # 7. Multi-File Blast Radius Risk
        total_affected = len(impact.direct_files) + len(impact.indirect_files)
        if total_affected >= 3:
            risks.append(
                Risk(
                    risk="Broad cross-file blast radius",
                    severity=RiskSeverity.MEDIUM,
                    reason=(
                        f"Change touches {total_affected} files across the repository "
                        f"({len(impact.direct_files)} direct, "
                        f"{len(impact.indirect_files)} indirect)."
                    ),
                    mitigation=(
                        "Decompose implementation into staged, testable tasks."
                    ),
                )
            )

        return risks

    def extract_assumptions(
        self,
        context: IssueContext,
        impact: ImpactResult,
    ) -> list[Assumption]:
        """Extract explicit engineering assumptions based on repository evidence.

        Returns:
            List of grounded Assumption models.
        """
        assumptions: list[Assumption] = []

        # Dependency preservation assumption
        assumptions.append(
            Assumption(
                assumption=(
                    "Existing third-party libraries and frameworks remain in use"
                ),
                reason=(
                    "The issue does not request upgrading or replacing dependencies."
                ),
            )
        )

        # Test regression baseline assumption
        if impact.existing_tests:
            assumptions.append(
                Assumption(
                    assumption="Existing test suite defines baseline behavior",
                    reason=(
                        f"Repository contains {len(impact.existing_tests)} existing "
                        "test files which must continue passing."
                    ),
                )
            )

        # Public interface backward compatibility
        if impact.symbol_changes:
            sym_names = ", ".join(s.symbol_name for s in impact.symbol_changes)
            assumptions.append(
                Assumption(
                    assumption=(
                        f"Public signatures for symbols ({sym_names}) remain compatible"
                    ),
                    reason="Prevent breaking external callers not modified in plan.",
                )
            )

        return assumptions

    def extract_unresolved_questions(self, context: IssueContext) -> list[str]:
        """Surface missing or ambiguous requirements that require clarification."""
        questions: list[str] = []

        if context.unresolved_references:
            for ref in context.unresolved_references:
                questions.append(
                    f"Entity reference '{ref}' was not found in repository. "
                    "Should this entity be newly created or is it misnamed?"
                )

        if context.analysis.is_ambiguous:
            for reason in context.analysis.ambiguity_reasons:
                questions.append(
                    f"Ambiguity detected: {reason}. Can you provide more details?"
                )

        return questions
