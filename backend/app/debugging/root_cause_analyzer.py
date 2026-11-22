"""Deterministic and provider-augmented root cause analysis for execution failures."""

import re

from backend.app.debugging.models import (
    DiagnosticEvidence,
    FailureCategory,
    FailureClassification,
    RepairConfiguration,
    RootCauseAnalysis,
    RootCauseType,
)
from backend.app.planning.models import ImplementationPlan


class RootCauseAnalyzer:
    """Analyzes diagnostic evidence and failure classifications for root causes."""

    UNHANDLED_EXCEPTION_PATTERNS = [
        re.compile(
            r"\b(KeyError|IndexError|AttributeError|ZeroDivisionError|ValueError|"
            r"FileNotFoundError):\s*(.+)",
            re.IGNORECASE,
        ),
    ]

    def __init__(self, config: RepairConfiguration | None = None) -> None:
        self.config = config or RepairConfiguration()

    def analyze(
        self,
        classification: FailureClassification,
        evidence: DiagnosticEvidence,
        plan: ImplementationPlan | None = None,
    ) -> RootCauseAnalysis:
        """Derive deterministic root cause analysis from failure evidence."""
        # 1. Environment and infrastructure defects
        if classification.category == FailureCategory.ENVIRONMENT_ERROR:
            return RootCauseAnalysis(
                primary_cause=RootCauseType.ENVIRONMENT_ISSUE,
                confidence=classification.confidence,
                supporting_evidence=classification.error_messages or ["Docker offline"],
                affected_files=[],
                affected_symbols=[],
                is_repairable_automatically=False,
                explanation=(
                    "Container environment or Docker runtime defect. Automated code "
                    "generation cannot remediate infrastructure."
                ),
            )

        # 2. Execution timeout
        if classification.category == FailureCategory.TIMEOUT:
            return RootCauseAnalysis(
                primary_cause=RootCauseType.ENVIRONMENT_ISSUE,
                confidence=classification.confidence,
                supporting_evidence=classification.error_messages
                or ["Execution timed out"],
                affected_files=evidence.relevant_file_paths,
                affected_symbols=evidence.relevant_symbols,
                is_repairable_automatically=False,
                explanation=(
                    "Execution exceeded timeout limit. Potential infinite loop or "
                    "deadlock requires human review."
                ),
            )

        # 3. Resource exhaustion
        if classification.category == FailureCategory.RESOURCE_EXHAUSTION:
            return RootCauseAnalysis(
                primary_cause=RootCauseType.ENVIRONMENT_ISSUE,
                confidence=classification.confidence,
                supporting_evidence=classification.error_messages or ["Out of memory"],
                affected_files=evidence.relevant_file_paths,
                affected_symbols=evidence.relevant_symbols,
                is_repairable_automatically=False,
                explanation=(
                    "Process terminated due to memory or CPU exhaustion in sandbox."
                ),
            )

        # 4. Patch application failure
        if classification.category == FailureCategory.PATCH_APPLICATION_ERROR:
            return RootCauseAnalysis(
                primary_cause=RootCauseType.INCORRECT_FILE_PATH,
                confidence=classification.confidence,
                supporting_evidence=classification.error_messages
                or ["Patch apply failed"],
                affected_files=evidence.relevant_file_paths,
                affected_symbols=evidence.relevant_symbols,
                is_repairable_automatically=False,
                explanation=(
                    "Proposed patch could not be applied cleanly or violated "
                    "file boundaries."
                ),
            )

        # 5. Syntax error
        if classification.category == FailureCategory.SYNTAX_ERROR:
            return RootCauseAnalysis(
                primary_cause=RootCauseType.INCORRECT_LOGIC,
                confidence=classification.confidence,
                supporting_evidence=classification.error_messages,
                affected_files=evidence.relevant_file_paths,
                affected_symbols=evidence.relevant_symbols,
                is_repairable_automatically=True,
                explanation="Python syntax or indentation error in modified code.",
            )

        # 6. Import errors (missing vs wrong)
        if classification.category == FailureCategory.IMPORT_ERROR:
            error_str = " ".join(classification.error_messages)
            if "cannot import name" in error_str.lower():
                primary = RootCauseType.MISSING_IMPORT
                alt: list[RootCauseType] = [RootCauseType.INCORRECT_LOGIC]
                explanation = "Symbol import mismatch or circular dependency in module."
            else:
                primary = RootCauseType.MISSING_IMPORT
                alt = [RootCauseType.MISSING_DEPENDENCY]
                explanation = "Missing module or unresolved import statement."

            return RootCauseAnalysis(
                primary_cause=primary,
                alternative_causes=alt,
                confidence=classification.confidence,
                supporting_evidence=classification.error_messages,
                affected_files=evidence.relevant_file_paths,
                affected_symbols=evidence.relevant_symbols,
                is_repairable_automatically=True,
                explanation=explanation,
            )

        # 7. Type errors
        if classification.category == FailureCategory.TYPE_ERROR:
            return RootCauseAnalysis(
                primary_cause=RootCauseType.TYPE_MISMATCH,
                confidence=classification.confidence,
                supporting_evidence=classification.error_messages,
                affected_files=evidence.relevant_file_paths,
                affected_symbols=evidence.relevant_symbols,
                is_repairable_automatically=True,
                explanation=(
                    "Incompatible type handling, argument type, or return type."
                ),
            )

        # 8. Dependency errors
        if classification.category == FailureCategory.DEPENDENCY_ERROR:
            return RootCauseAnalysis(
                primary_cause=RootCauseType.MISSING_DEPENDENCY,
                confidence=classification.confidence,
                supporting_evidence=classification.error_messages,
                affected_files=evidence.relevant_file_paths,
                affected_symbols=evidence.relevant_symbols,
                is_repairable_automatically=False,
                explanation="Required package dependency missing from environment.",
            )

        # 9. Lint errors
        if classification.category == FailureCategory.LINT_ERROR:
            return RootCauseAnalysis(
                primary_cause=RootCauseType.INCORRECT_LOGIC,
                confidence=classification.confidence,
                supporting_evidence=classification.error_messages,
                affected_files=evidence.relevant_file_paths,
                affected_symbols=evidence.relevant_symbols,
                is_repairable_automatically=True,
                explanation="Style, import order, or code quality linter violation.",
            )

        # 10. Test failures: distinguish assertion failure vs unhandled exception
        if classification.category == FailureCategory.TEST_FAILURE:

            combined_errs = " ".join(
                evidence.error_messages + classification.error_messages
            )
            unhandled_match = any(
                p.search(combined_errs) for p in self.UNHANDLED_EXCEPTION_PATTERNS
            )

            if unhandled_match:
                primary_cause = RootCauseType.INCORRECT_LOGIC
                alt_causes: list[RootCauseType] = [
                    RootCauseType.TEST_EXPECTATION_MISMATCH,
                    RootCauseType.BROKEN_INTEGRATION,
                ]
                explanation = "Unhandled runtime exception during test execution."
            else:
                primary_cause = RootCauseType.TEST_EXPECTATION_MISMATCH
                alt_causes = [RootCauseType.INCORRECT_LOGIC]
                explanation = (
                    "Test assertion failure: actual output did not match expected."
                )

            # Correlate with plan criteria if available
            related_criteria = list(evidence.acceptance_criteria)
            uncertainty_notes = None

            if plan:
                planned_files = {
                    f.path for f in plan.files_to_modify + plan.files_to_create
                }
                implicated = set(evidence.relevant_file_paths)
                out_of_scope = implicated - planned_files
                if out_of_scope:
                    uncertainty_notes = (
                        f"Implicated files {sorted(out_of_scope)} were not in the "
                        "original plan."
                    )
                    alt_causes.append(RootCauseType.INCORRECT_FILE_PATH)

            return RootCauseAnalysis(
                primary_cause=primary_cause,
                alternative_causes=alt_causes,
                confidence=classification.confidence,
                supporting_evidence=(
                    evidence.error_messages[:5] or classification.error_messages
                ),
                affected_files=evidence.relevant_file_paths,
                affected_symbols=evidence.relevant_symbols,
                related_acceptance_criteria=related_criteria,
                is_repairable_automatically=True,
                explanation=explanation,
                uncertainty_notes=uncertainty_notes,
            )

        # 11. Fallback / Unknown
        return RootCauseAnalysis(
            primary_cause=RootCauseType.UNKNOWN_CAUSE,
            alternative_causes=[RootCauseType.INCORRECT_LOGIC],
            confidence=0.35,
            supporting_evidence=classification.error_messages,
            affected_files=evidence.relevant_file_paths,
            affected_symbols=evidence.relevant_symbols,
            is_repairable_automatically=False,
            explanation=(
                "Uncategorized failure requires human diagnosis or manual review."
            ),
        )
