"""Debugging and Automatic Repair Engine module."""

from backend.app.debugging.evidence_collector import DiagnosticEvidenceCollector
from backend.app.debugging.failure_classifier import FailureClassifier
from backend.app.debugging.models import (
    DiagnosticEvidence,
    FailureCategory,
    FailureClassification,
    RepairAttempt,
    RepairConfiguration,
    RepairEvaluation,
    RepairEvaluationVerdict,
    RepairProposal,
    RepairRequest,
    RepairResult,
    RepairState,
    RepairStrategy,
    RepairStrategyType,
    RiskLevel,
    RootCauseAnalysis,
    RootCauseType,
    StateTransition,
)
from backend.app.debugging.state_machine import (
    InvalidStateTransitionError,
    RepairStateMachine,
)

__all__ = [
    "DiagnosticEvidence",
    "DiagnosticEvidenceCollector",
    "FailureCategory",
    "FailureClassification",
    "FailureClassifier",
    "InvalidStateTransitionError",
    "RepairAttempt",
    "RepairConfiguration",
    "RepairEvaluation",
    "RepairEvaluationVerdict",
    "RepairProposal",
    "RepairRequest",
    "RepairResult",
    "RepairState",
    "RepairStateMachine",
    "RepairStrategy",
    "RepairStrategyType",
    "RiskLevel",
    "RootCauseAnalysis",
    "RootCauseType",
    "StateTransition",
]
