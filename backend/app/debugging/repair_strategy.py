"""Repair strategy selection engine based on diagnosed root causes."""

from backend.app.debugging.models import (
    DiagnosticEvidence,
    FailureClassification,
    RepairConfiguration,
    RepairStrategy,
    RepairStrategyType,
    RiskLevel,
    RootCauseAnalysis,
    RootCauseType,
)
from backend.app.planning.models import ImplementationPlan


class RepairStrategyEngine:
    """Selects targeted, bounded repair strategies and determines approvals."""

    def __init__(self, config: RepairConfiguration | None = None) -> None:
        self.config = config or RepairConfiguration()

    def select_strategy(
        self,
        root_cause: RootCauseAnalysis,
        classification: FailureClassification,
        evidence: DiagnosticEvidence,
        plan: ImplementationPlan | None = None,
    ) -> RepairStrategy:
        """Derive bounded repair strategy, risk assessment, and approvals."""
        # Determine authorized target files
        planned_files: set[str] = set()
        if plan:
            planned_files = {
                f.path for f in plan.files_to_modify + plan.files_to_create
            }

        implicated_files = root_cause.affected_files or evidence.relevant_file_paths

        # If plan is present, constrain target files to planned files where possible
        if planned_files and implicated_files:
            target_files = [f for f in implicated_files if f in planned_files]
            if not target_files:
                target_files = (
                    [f.path for f in plan.files_to_modify] if plan else implicated_files
                )
        else:
            target_files = implicated_files

        target_symbols = root_cause.affected_symbols or evidence.relevant_symbols

        # 1. Environment and infrastructure failure -> STOP_ENVIRONMENT_FAILURE
        if root_cause.primary_cause == RootCauseType.ENVIRONMENT_ISSUE:
            return RepairStrategy(
                strategy_type=RepairStrategyType.STOP_ENVIRONMENT_FAILURE,
                reason=(
                    f"Failure caused by {root_cause.primary_cause.value} cannot "
                    "be safely repaired automatically. Docker/sandbox issue."
                ),
                target_files=target_files,
                target_symbols=target_symbols,
                expected_changes=["Docker environment inspection required."],
                risk_level=RiskLevel.CRITICAL,
                required_evidence=root_cause.supporting_evidence,
                code_generation_permitted=False,
                human_approval_required=True,
            )

        # 2. Dependency defect -> RECOMMEND_DEPENDENCY_UPDATE
        if root_cause.primary_cause == RootCauseType.MISSING_DEPENDENCY:
            return RepairStrategy(
                strategy_type=RepairStrategyType.RECOMMEND_DEPENDENCY_UPDATE,
                reason="Package dependency missing; environment adjustment required.",
                target_files=target_files,
                target_symbols=target_symbols,
                expected_changes=[
                    "Install required package or update dependency specification"
                ],
                risk_level=RiskLevel.HIGH,
                required_evidence=root_cause.supporting_evidence,
                code_generation_permitted=False,
                human_approval_required=True,
            )

        # 3. Configuration defect -> PROPOSE_CONFIGURATION_CORRECTION
        if root_cause.primary_cause == RootCauseType.INVALID_CONFIGURATION:
            return RepairStrategy(
                strategy_type=RepairStrategyType.PROPOSE_CONFIGURATION_CORRECTION,
                reason="Invalid environment configuration or build options.",
                target_files=target_files,
                target_symbols=target_symbols,
                expected_changes=["Correct configuration parameters or variables"],
                risk_level=RiskLevel.MEDIUM,
                required_evidence=root_cause.supporting_evidence,
                code_generation_permitted=True,
                human_approval_required=False,
            )

        # 4. Non-repairable or human intervention mandatory -> REQUEST_HUMAN_REVIEW
        if (
            not root_cause.is_repairable_automatically
            or not classification.auto_repair_permitted
            or classification.human_intervention_required
        ):
            return RepairStrategy(
                strategy_type=RepairStrategyType.REQUEST_HUMAN_REVIEW,
                reason=(
                    f"Failure caused by {root_cause.primary_cause.value} requires "
                    "human review before further actions."
                ),
                target_files=target_files,
                target_symbols=target_symbols,
                expected_changes=["Human investigation and resolution required."],
                risk_level=RiskLevel.CRITICAL,
                required_evidence=root_cause.supporting_evidence,
                code_generation_permitted=False,
                human_approval_required=True,
            )

        # 5. Missing / Wrong import -> ADD_OR_CORRECT_IMPORT
        if root_cause.primary_cause == RootCauseType.MISSING_IMPORT:
            return RepairStrategy(
                strategy_type=RepairStrategyType.ADD_OR_CORRECT_IMPORT,
                reason="Add or correct missing module or symbol import statement.",
                target_files=target_files,
                target_symbols=target_symbols,
                expected_changes=["Add or update import statements"],
                risk_level=RiskLevel.LOW,
                required_evidence=root_cause.supporting_evidence,
                code_generation_permitted=True,
                human_approval_required=False,
            )

        # 6. Type mismatch -> CORRECT_TYPE_HANDLING
        if root_cause.primary_cause == RootCauseType.TYPE_MISMATCH:
            return RepairStrategy(
                strategy_type=RepairStrategyType.CORRECT_TYPE_HANDLING,
                reason=(
                    "Align function argument, return, or variable types with "
                    "expectations."
                ),
                target_files=target_files,
                target_symbols=target_symbols,
                expected_changes=[
                    "Adjust type annotations or add explicit conversions"
                ],
                risk_level=RiskLevel.MEDIUM,
                required_evidence=root_cause.supporting_evidence,
                code_generation_permitted=True,
                human_approval_required=False,
            )

        # 7. Signature defect -> UPDATE_SIGNATURE_AND_CALLERS
        if root_cause.primary_cause == RootCauseType.INCORRECT_SIGNATURE:
            return RepairStrategy(
                strategy_type=RepairStrategyType.UPDATE_SIGNATURE_AND_CALLERS,
                reason=(
                    "Correct function arguments, parameter count, or caller "
                    "invocations."
                ),
                target_files=target_files,
                target_symbols=target_symbols,
                expected_changes=["Update function signature and callers"],
                risk_level=RiskLevel.MEDIUM,
                required_evidence=root_cause.supporting_evidence,
                code_generation_permitted=True,
                human_approval_required=False,
            )

        # 8. Test expectation mismatch or logic error -> MODIFY_AFFECTED_LOGIC
        # Risk assessment: single-file / single-symbol -> MEDIUM, multi-file -> HIGH
        if len(target_files) > 1 or len(target_symbols) > 1:
            risk_level = RiskLevel.HIGH
            reason = "Modify affected computational logic across multiple target files."
            expected_changes = [
                "Re-architect calculation or flow across multiple symbols"
            ]
        else:
            risk_level = RiskLevel.MEDIUM
            reason = "Adjust implementation logic in target function to satisfy test."
            expected_changes = [
                "Update function implementation to return expected value"
            ]

        needs_approval = (
            risk_level == RiskLevel.HIGH and self.config.require_approval_for_high_risk
        )

        return RepairStrategy(
            strategy_type=RepairStrategyType.MODIFY_AFFECTED_LOGIC,
            reason=reason,
            target_files=target_files,
            target_symbols=target_symbols,
            expected_changes=expected_changes,
            risk_level=risk_level,
            required_evidence=root_cause.supporting_evidence,
            code_generation_permitted=True,
            human_approval_required=needs_approval,
        )
