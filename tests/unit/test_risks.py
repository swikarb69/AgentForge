"""Unit tests for risk, assumption, and ambiguity analysis."""

from backend.app.issue.models import (
    EndpointReference,
    Issue,
    IssueAnalysis,
    IssueContext,
    IssueRequirement,
    RequirementType,
)
from backend.app.planning.impact import ImpactResult
from backend.app.planning.models import (
    ChangeType,
    FileChange,
    RiskSeverity,
    SymbolChange,
)
from backend.app.planning.risks import RiskAnalyzer
from backend.app.repository.models import SymbolType


def test_security_risk_detection() -> None:
    """Verify detection of security and authentication risks."""
    context = IssueContext(
        analysis=IssueAnalysis(
            issue=Issue(
                title="Update JWT token verification",
                description="Reject expired auth tokens in verify_token",
            ),
            requirements=[
                IssueRequirement(
                    id="REQ-1",
                    description="Check token expiry",
                    requirement_type=RequirementType.FUNCTIONAL,
                )
            ],
            acceptance_criteria=[],
            constraints=[],
            keywords=["jwt", "token"],
            references=[],
            endpoints=[],
        ),
        relevant_files=["app/auth.py"],
        relevant_symbols=[],
        relevant_chunks=[],
        matches=[],
    )

    impact = ImpactResult(
        files_to_modify=[
            FileChange(
                path="app/auth.py",
                change_type=ChangeType.MODIFY,
                reason="Update token logic",
                affected_symbols=["verify_token"],
            )
        ],
        symbol_changes=[
            SymbolChange(
                file_path="app/auth.py",
                symbol_name="verify_token",
                symbol_type=SymbolType.FUNCTION,
                change_type=ChangeType.MODIFY,
                reason="Update token logic",
            )
        ],
        direct_files=["app/auth.py"],
        existing_tests=["tests/test_auth.py"],
    )

    analyzer = RiskAnalyzer()
    risks = analyzer.analyze_risks(context, impact)

    auth_risk = next(
        (
            r
            for r in risks
            if r.severity == RiskSeverity.HIGH and "auth" in r.risk.lower()
        ),
        None,
    )
    assert auth_risk is not None
    assert "token" in auth_risk.reason.lower()
    assert (
        "mitigation" in auth_risk.mitigation.lower() or len(auth_risk.mitigation) > 10
    )


def test_api_compatibility_and_database_risks() -> None:
    """Verify detection of API endpoint changes and database schema risks."""
    context = IssueContext(
        analysis=IssueAnalysis(
            issue=Issue(
                title="Migrate database schema for orders table",
                description="Alter table sql migration and update REST API endpoint",
            ),
            requirements=[],
            acceptance_criteria=[],
            constraints=[],
            keywords=["database", "sql"],
            references=[],
            endpoints=[EndpointReference(method="POST", path="/api/v1/orders")],
        ),
        relevant_files=["app/database.py", "app/api/orders.py"],
        relevant_symbols=[],
        relevant_chunks=[],
        matches=[],
    )

    impact = ImpactResult(
        files_to_modify=[
            FileChange(
                path="app/database.py",
                change_type=ChangeType.MODIFY,
                reason="Update connection and schema",
            ),
            FileChange(
                path="app/api/orders.py",
                change_type=ChangeType.MODIFY,
                reason="Update endpoint",
            ),
        ],
        direct_files=["app/database.py", "app/api/orders.py"],
    )

    analyzer = RiskAnalyzer()
    risks = analyzer.analyze_risks(context, impact)

    assert any("database" in r.risk.lower() for r in risks)
    assert any("api" in r.risk.lower() for r in risks)


def test_unresolved_references_and_ambiguity_risks() -> None:
    """Verify detection of unresolved references and ambiguity risks."""
    context = IssueContext(
        analysis=IssueAnalysis(
            issue=Issue(
                title="Fix bug",
                description="Fix the thing",
            ),
            requirements=[],
            acceptance_criteria=[],
            constraints=[],
            keywords=[],
            references=[],
            endpoints=[],
            is_ambiguous=True,
            ambiguity_reasons=["Description too short"],
        ),
        relevant_files=[],
        relevant_symbols=[],
        relevant_chunks=[],
        matches=[],
        unresolved_references=["app/missing.py::unknown_func"],
    )

    impact = ImpactResult(
        tests_to_add=["tests/test_missing.py"],
        existing_tests=[],
    )

    analyzer = RiskAnalyzer()
    risks = analyzer.analyze_risks(context, impact)

    assert any("ambiguous" in r.risk.lower() for r in risks)
    assert any("unresolved" in r.risk.lower() for r in risks)
    assert any("absence of pre-existing test suite" in r.risk.lower() for r in risks)

    # Check unresolved questions
    questions = analyzer.extract_unresolved_questions(context)
    assert len(questions) >= 2
    assert any("unknown_func" in q for q in questions)


def test_extract_assumptions() -> None:
    """Verify grounded assumptions extraction."""
    context = IssueContext(
        analysis=IssueAnalysis(
            issue=Issue(title="Update auth", description="desc"),
            requirements=[],
            acceptance_criteria=[],
            constraints=[],
            keywords=[],
            references=[],
            endpoints=[],
        ),
        relevant_files=["app/auth.py"],
        relevant_symbols=[],
        relevant_chunks=[],
        matches=[],
    )

    impact = ImpactResult(
        existing_tests=["tests/test_auth.py"],
        symbol_changes=[
            SymbolChange(
                file_path="app/auth.py",
                symbol_name="login",
                symbol_type=SymbolType.FUNCTION,
                change_type=ChangeType.MODIFY,
                reason="login update",
            )
        ],
    )

    analyzer = RiskAnalyzer()
    assumptions = analyzer.extract_assumptions(context, impact)

    assert len(assumptions) >= 2
    assert any("third-party" in a.assumption.lower() for a in assumptions)
    assert any("test suite" in a.assumption.lower() for a in assumptions)
