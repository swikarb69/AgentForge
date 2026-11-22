"""Debugging and Automatic Repair Engine module."""

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
    "FailureCategory",
    "FailureClassification",
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
