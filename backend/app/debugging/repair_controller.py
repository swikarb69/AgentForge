"""Bounded repair controller orchestrating automated debugging and repair loop."""

import hashlib
import uuid
from datetime import UTC, datetime
from pathlib import Path

from backend.app.debugging.evaluation import RepairEvaluator
from backend.app.debugging.evidence_collector import DiagnosticEvidenceCollector
from backend.app.debugging.failure_classifier import FailureClassifier
from backend.app.debugging.models import (
    RepairAttempt,
    RepairEvaluationVerdict,
    RepairRequest,
    RepairResult,
    RepairState,
)
from backend.app.debugging.repair_engine import RepairProposalEngine
from backend.app.debugging.repair_strategy import RepairStrategyEngine
from backend.app.debugging.root_cause_analyzer import RootCauseAnalyzer
from backend.app.debugging.state_machine import RepairStateMachine
from backend.app.execution.models import (
    CreateExecutionRequest,
    VerificationProfile,
    VerificationReport,
)
from backend.app.execution.service import ExecutionService
from backend.app.planning.models import ImplementationPlan


class BoundedRepairController:
    """Orchestrates bounded, observable, and policy-governed automated repair loops."""

    def __init__(
        self,
        failure_classifier: FailureClassifier | None = None,
        evidence_collector: DiagnosticEvidenceCollector | None = None,
        root_cause_analyzer: RootCauseAnalyzer | None = None,
        repair_strategy_engine: RepairStrategyEngine | None = None,
        repair_proposal_engine: RepairProposalEngine | None = None,
        repair_evaluator: RepairEvaluator | None = None,
        execution_service: ExecutionService | None = None,
    ) -> None:
        self.failure_classifier = failure_classifier or FailureClassifier()
        self.evidence_collector = evidence_collector or DiagnosticEvidenceCollector()
        self.root_cause_analyzer = root_cause_analyzer or RootCauseAnalyzer()
        self.repair_strategy_engine = repair_strategy_engine or RepairStrategyEngine()
        self.repair_proposal_engine = repair_proposal_engine or RepairProposalEngine()
        self.repair_evaluator = repair_evaluator or RepairEvaluator()
        self.execution_service = execution_service or ExecutionService()

    async def run_repair(
        self,
        request: RepairRequest,
        initial_report: VerificationReport,
        plan: ImplementationPlan | None = None,
    ) -> RepairResult:
        """Run an automated repair loop bounded by configuration and safety policies."""
        repair_id = f"repair-{uuid.uuid4().hex[:12]}"
        state_machine = RepairStateMachine(initial_state=RepairState.PENDING)
        seen_patch_hashes: set[str] = set()

        config = request.config
        max_attempts = min(max(1, config.max_attempts), 5)

        repair_result = RepairResult(
            repair_id=repair_id,
            repository_path=request.repository_path,
            initial_execution_id=request.execution_id,
            current_state=RepairState.PENDING,
            config=config,
            metadata=request.metadata,
        )

        current_report = initial_report

        for attempt_idx in range(1, max_attempts + 1):
            attempt = RepairAttempt(
                attempt_number=attempt_idx,
                started_at=datetime.now(UTC).isoformat(),
            )

            # 1. ANALYZING: Failure classification & evidence collection
            if state_machine.current_state in (
                RepairState.PENDING,
                RepairState.EVALUATING,
            ):
                trans = state_machine.transition_to(
                    RepairState.ANALYZING,
                    reason=f"Analyzing failure patterns for attempt {attempt_idx}",
                )
                attempt.state_transitions.append(trans)

            classification = self.failure_classifier.classify(current_report)
            attempt.classification = classification

            # Check if failure is an unrepairable environment or infrastructure defect
            from backend.app.debugging.models import FailureCategory

            is_env_defect = (
                classification.category
                in (
                    FailureCategory.ENVIRONMENT_ERROR,
                    FailureCategory.TIMEOUT,
                    FailureCategory.RESOURCE_EXHAUSTION,
                )
                or not classification.auto_repair_permitted
                or classification.human_intervention_required
            )
            if is_env_defect:
                reason = classification.explanation or (
                    classification.error_messages[0]
                    if classification.error_messages
                    else "Environment defect"
                )
                trans = state_machine.transition_to(
                    RepairState.NEEDS_HUMAN_REVIEW,
                    reason=f"Environment defect detected: {reason}",
                )
                attempt.state_transitions.append(trans)
                attempt.completed_at = datetime.now(UTC).isoformat()
                repair_result.attempts.append(attempt)
                repair_result.total_attempts = len(repair_result.attempts)
                repair_result.current_state = state_machine.current_state
                repair_result.human_intervention_required = True
                repair_result.human_intervention_reason = reason
                repair_result.completed_at = datetime.now(UTC).isoformat()
                return repair_result

            evidence = self.evidence_collector.collect(
                report=current_report,
                plan=plan,
                previous_attempt_count=attempt_idx - 1,
            )

            # 2. DIAGNOSING: Root cause analysis & strategy selection
            trans = state_machine.transition_to(
                RepairState.DIAGNOSING,
                reason=(
                    f"Diagnosing root cause and determining strategy for "
                    f"attempt {attempt_idx}"
                ),
            )
            attempt.state_transitions.append(trans)

            root_cause = self.root_cause_analyzer.analyze(
                classification=classification,
                evidence=evidence,
                plan=plan,
            )
            attempt.root_cause = root_cause

            if not root_cause.is_repairable_automatically:
                trans = state_machine.transition_to(
                    RepairState.NEEDS_HUMAN_REVIEW,
                    reason=(
                        f"Defect cannot be repaired automatically: "
                        f"{root_cause.explanation}"
                    ),
                )
                attempt.state_transitions.append(trans)
                attempt.completed_at = datetime.now(UTC).isoformat()
                repair_result.attempts.append(attempt)
                repair_result.total_attempts = len(repair_result.attempts)
                repair_result.current_state = state_machine.current_state
                repair_result.human_intervention_required = True
                repair_result.human_intervention_reason = root_cause.explanation
                repair_result.completed_at = datetime.now(UTC).isoformat()
                return repair_result

            strategy = self.repair_strategy_engine.select_strategy(
                root_cause=root_cause,
                classification=classification,
                evidence=evidence,
                plan=plan,
            )
            attempt.strategy = strategy

            if not strategy.code_generation_permitted:
                trans = state_machine.transition_to(
                    RepairState.NEEDS_HUMAN_REVIEW,
                    reason=(
                        f"Code generation not permitted for strategy "
                        f"'{strategy.strategy_type.value}': {strategy.reason}"
                    ),
                )
                attempt.state_transitions.append(trans)
                attempt.completed_at = datetime.now(UTC).isoformat()
                repair_result.attempts.append(attempt)
                repair_result.total_attempts = len(repair_result.attempts)
                repair_result.current_state = state_machine.current_state
                repair_result.human_intervention_required = True
                repair_result.human_intervention_reason = strategy.reason
                repair_result.completed_at = datetime.now(UTC).isoformat()
                return repair_result

            # Check human approval requirement
            if (
                strategy.human_approval_required
                and config.require_approval_for_high_risk
            ):
                trans = state_machine.transition_to(
                    RepairState.AWAITING_APPROVAL,
                    reason=(
                        f"Repair strategy requires human approval: {strategy.reason}"
                    ),
                )
                attempt.state_transitions.append(trans)
                attempt.completed_at = datetime.now(UTC).isoformat()
                repair_result.attempts.append(attempt)
                repair_result.total_attempts = len(repair_result.attempts)
                repair_result.current_state = state_machine.current_state
                repair_result.human_intervention_required = True
                repair_result.human_intervention_reason = (
                    f"Strategy '{strategy.strategy_type.value}' has "
                    f"{strategy.risk_level.value} risk requiring human approval."
                )
                repair_result.completed_at = datetime.now(UTC).isoformat()
                return repair_result

            # 3. GENERATING_REPAIR: Proposal generation
            trans = state_machine.transition_to(
                RepairState.GENERATING_REPAIR,
                reason=f"Generating bounded code proposal for attempt {attempt_idx}",
            )
            attempt.state_transitions.append(trans)

            effective_plan = plan or ImplementationPlan(
                id=f"PLAN-{repair_id}",
                issue_id="ISSUE-REPAIR",
                objective="Automated repair session",
                summary="Automated repair plan",
            )
            if not effective_plan.files_to_modify and strategy.target_files:
                from backend.app.planning.models import ChangeType, FileChange

                effective_plan.files_to_modify = [
                    FileChange(
                        path=tf,
                        change_type=ChangeType.MODIFY,
                        reason="Automated repair target",
                    )
                    for tf in strategy.target_files
                ]
            proposal = await self.repair_proposal_engine.generate_proposal(
                repair_id=repair_id,
                attempt_number=attempt_idx,
                strategy=strategy,
                root_cause=root_cause,
                evidence=evidence,
                plan=effective_plan,
                target_repo_path=Path(request.repository_path),
            )
            attempt.proposal = proposal

            # Loop Detection: SHA-256 hash check
            patch_hash = hashlib.sha256(
                proposal.patch_content.encode("utf-8")
            ).hexdigest()
            attempt.patch_hash = patch_hash

            if patch_hash in seen_patch_hashes:
                trans = state_machine.transition_to(
                    RepairState.FAILED,
                    reason=(
                        "Repeated identical patch proposal detected "
                        "(infinite loop prevented)."
                    ),
                )
                attempt.state_transitions.append(trans)
                attempt.error_message = (
                    "Repeated identical patch proposal detected "
                    "(infinite loop prevented)."
                )
                attempt.completed_at = datetime.now(UTC).isoformat()
                repair_result.attempts.append(attempt)
                repair_result.total_attempts = len(repair_result.attempts)
                repair_result.current_state = state_machine.current_state
                repair_result.error_message = (
                    "Repeated identical patch proposal detected "
                    "(infinite loop prevented)."
                )
                repair_result.completed_at = datetime.now(UTC).isoformat()
                return repair_result

            seen_patch_hashes.add(patch_hash)

            # 4. VALIDATING_REPAIR: Patch validation
            trans = state_machine.transition_to(
                RepairState.VALIDATING_REPAIR,
                reason=f"Validating proposed repair patch for attempt {attempt_idx}",
            )
            attempt.state_transitions.append(trans)

            if not proposal.validation or not proposal.validation.valid:
                val_errors = (
                    proposal.validation.errors
                    if proposal.validation
                    else ["Empty proposal"]
                )
                error_msg = f"Proposal patch validation failed: {'; '.join(val_errors)}"
                attempt.error_message = error_msg
                trans = state_machine.transition_to(
                    RepairState.FAILED,
                    reason=error_msg,
                )
                attempt.state_transitions.append(trans)
                attempt.completed_at = datetime.now(UTC).isoformat()
                repair_result.attempts.append(attempt)
                repair_result.total_attempts = len(repair_result.attempts)
                repair_result.current_state = state_machine.current_state
                repair_result.error_message = error_msg
                repair_result.completed_at = datetime.now(UTC).isoformat()
                return repair_result

            # 5. EXECUTING_REPAIR: Sandbox execution
            trans = state_machine.transition_to(
                RepairState.EXECUTING_REPAIR,
                reason=(
                    f"Executing repair verification in sandbox for attempt "
                    f"{attempt_idx}"
                ),
            )
            attempt.state_transitions.append(trans)

            if not config.auto_execute_repairs:
                attempt.completed_at = datetime.now(UTC).isoformat()
                repair_result.attempts.append(attempt)
                repair_result.total_attempts = len(repair_result.attempts)
                repair_result.current_state = state_machine.current_state
                repair_result.completed_at = datetime.now(UTC).isoformat()
                return repair_result

            exec_request = CreateExecutionRequest(
                repository_path=request.repository_path,
                patch_content=proposal.patch_content,
                verification_profile=VerificationProfile.PYTHON_UNIT_TESTS,
            )
            new_report = await self.execution_service.execute_and_verify(exec_request)
            attempt.execution_report = new_report

            # 6. EVALUATING: Outcome evaluation
            trans = state_machine.transition_to(
                RepairState.EVALUATING,
                reason=f"Evaluating repair outcomes for attempt {attempt_idx}",
            )
            attempt.state_transitions.append(trans)

            evaluation = self.repair_evaluator.evaluate(
                pre_report=current_report,
                post_report=new_report,
                plan=plan,
                proposal=proposal,
            )
            attempt.evaluation = evaluation
            attempt.completed_at = datetime.now(UTC).isoformat()
            repair_result.attempts.append(attempt)
            repair_result.total_attempts = len(repair_result.attempts)

            if evaluation.verdict == RepairEvaluationVerdict.FIXED:
                trans = state_machine.transition_to(
                    RepairState.SUCCEEDED,
                    reason=(
                        "All original failures resolved cleanly without regressions."
                    ),
                )
                attempt.state_transitions.append(trans)
                repair_result.current_state = state_machine.current_state
                repair_result.final_verdict = evaluation.verdict
                repair_result.final_patch = proposal.patch_content
                repair_result.completed_at = datetime.now(UTC).isoformat()
                return repair_result

            # If not completely fixed, check if max attempts reached
            if attempt_idx >= max_attempts:
                trans = state_machine.transition_to(
                    RepairState.MAX_ATTEMPTS_REACHED,
                    reason=(
                        f"Maximum repair attempts ({max_attempts}) reached "
                        f"without full fix."
                    ),
                )
                attempt.state_transitions.append(trans)
                repair_result.current_state = state_machine.current_state
                repair_result.final_verdict = evaluation.verdict
                repair_result.error_message = (
                    f"Maximum repair attempts ({max_attempts}) reached. "
                    f"Final verdict: {evaluation.verdict.value}."
                )
                repair_result.completed_at = datetime.now(UTC).isoformat()
                return repair_result

            # Prepare for next attempt with post-repair report as new baseline
            current_report = new_report

        repair_result.completed_at = datetime.now(UTC).isoformat()
        return repair_result
