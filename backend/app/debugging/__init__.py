"""Debugging and Automatic Repair Engine module."""

from backend.app.debugging.evaluation import RepairEvaluator
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
from backend.app.debugging.repair_engine import RepairProposalEngine
from backend.app.debugging.repair_strategy import RepairStrategyEngine
from backend.app.debugging.root_cause_analyzer import RootCauseAnalyzer
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
    "RepairEvaluator",
    "RepairProposal",
    "RepairProposalEngine",
    "RepairRequest",
    "RepairResult",
    "RepairState",
    "RepairStateMachine",
    "RepairStrategy",
    "RepairStrategyEngine",
    "RepairStrategyType",
    "RiskLevel",
    "RootCauseAnalysis",
    "RootCauseAnalyzer",
    "RootCauseType",
    "StateTransition",
]
