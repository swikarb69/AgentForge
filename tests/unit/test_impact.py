"""Unit tests for change impact analysis."""

from backend.app.issue.models import (
    Issue,
    IssueAnalysis,
    IssueContext,
    IssueMatch,
    IssueReference,
    MatchReason,
    ReferenceType,
)
from backend.app.planning.impact import ImpactAnalyzer
from backend.app.planning.models import ChangeType
from backend.app.repository.models import (
    CodeChunk,
    RepositoryFile,
    RepositoryInfo,
    Symbol,
    SymbolType,
)


def _build_sample_context(
    title: str = "Fix verify_token in app/auth.py",
    description: str = "Handle expired JWT tokens.",
    relevant_files: list[str] | None = None,
    relevant_symbols: list[Symbol] | None = None,
    matches: list[IssueMatch] | None = None,
    references: list[IssueReference] | None = None,
    unresolved_references: list[str] | None = None,
) -> IssueContext:
    files = relevant_files or ["app/auth.py", "tests/test_auth.py"]
    syms = relevant_symbols or [
        Symbol(
            name="verify_token",
            symbol_type=SymbolType.FUNCTION,
            file_path="app/auth.py",
            start_line=1,
            end_line=10,
        )
    ]
    m_list = matches or [
        IssueMatch(
            file_path="app/auth.py",
            symbol_name="verify_token",
            score=15.0,
            reasons=[
                MatchReason(
                    rule_name="exact_symbol_reference",
                    score=10.0,
                    description="matched verify_token",
                )
            ],
            chunk=CodeChunk(
                chunk_id="chunk-1",
                file_path="app/auth.py",
                language="python",
                symbol_name="verify_token",
                symbol_type=SymbolType.FUNCTION,
                start_line=1,
                end_line=10,
                content="def verify_token(): pass",
            ),
        )
    ]
    refs = references or [
        IssueReference(reference_type=ReferenceType.FILE, value="app/auth.py"),
        IssueReference(reference_type=ReferenceType.SYMBOL, value="verify_token"),
    ]
    return IssueContext(
        analysis=IssueAnalysis(
            issue=Issue(title=title, description=description),
            requirements=[],
            acceptance_criteria=[],
            constraints=[],
            keywords=["token", "auth"],
            references=refs,
            endpoints=[],
        ),
        relevant_files=files,
        relevant_symbols=syms,
        relevant_chunks=[],
        matches=m_list,
        unresolved_references=unresolved_references or [],
    )


def test_direct_impact_files_and_symbols() -> None:
    """Verify that direct files and symbols are properly extracted."""
    context = _build_sample_context()
    analyzer = ImpactAnalyzer()
    result = analyzer.analyze(context)

    assert "app/auth.py" in result.direct_files
    assert any(fc.path == "app/auth.py" for fc in result.files_to_modify)

    auth_fc = next(fc for fc in result.files_to_modify if fc.path == "app/auth.py")
    assert auth_fc.change_type == ChangeType.MODIFY
    assert "verify_token" in auth_fc.affected_symbols

    assert len(result.symbol_changes) >= 1
    sym_change = result.symbol_changes[0]
    assert sym_change.symbol_name == "verify_token"
    assert sym_change.file_path == "app/auth.py"


def test_indirect_test_impact_existing() -> None:
    """Verify that existing test files are marked in tests_to_modify."""
    context = _build_sample_context()
    repo_info = RepositoryInfo(
        repository_name="sample-project",
        root_path="/workspace/sample",
        total_files=2,
        total_source_files=1,
        total_test_files=1,
        language_counts={"python": 2},
        files=[
            RepositoryFile(
                relative_path="app/auth.py",
                file_extension=".py",
                language="python",
                size_bytes=100,
                line_count=10,
            ),
            RepositoryFile(
                relative_path="tests/test_auth.py",
                file_extension=".py",
                language="python",
                size_bytes=100,
                line_count=10,
                is_test=True,
            ),
        ],
    )
    analyzer = ImpactAnalyzer()
    result = analyzer.analyze(context, repo_info)

    assert "tests/test_auth.py" in result.existing_tests
    assert "tests/test_auth.py" in result.tests_to_modify
    assert "tests/test_auth.py" in result.indirect_files


def test_indirect_test_impact_new() -> None:
    """Verify that a new test file is planned when none exists."""
    context = _build_sample_context(
        title="Fix payment logic in app/payment.py",
        description="Update charge calculation",
        relevant_files=["app/payment.py"],
        relevant_symbols=[
            Symbol(
                name="charge",
                symbol_type=SymbolType.FUNCTION,
                file_path="app/payment.py",
                start_line=1,
                end_line=5,
            )
        ],
        matches=[
            IssueMatch(
                file_path="app/payment.py",
                symbol_name="charge",
                score=10.0,
                reasons=[],
            )
        ],
        references=[
            IssueReference(reference_type=ReferenceType.FILE, value="app/payment.py")
        ],
    )
    analyzer = ImpactAnalyzer()
    result = analyzer.analyze(context)

    assert "tests/test_payment.py" in result.tests_to_add
    assert any(fc.path == "tests/test_payment.py" for fc in result.files_to_create)


def test_new_file_creation_requested() -> None:
    """Verify that file requested to be created is added to files_to_create."""
    context = _build_sample_context(
        title="Create new file app/billing.py",
        description="Add new file app/billing.py with invoice functions",
        relevant_files=[],
        relevant_symbols=[],
        matches=[],
        references=[
            IssueReference(reference_type=ReferenceType.FILE, value="app/billing.py")
        ],
        unresolved_references=["app/billing.py"],
    )
    analyzer = ImpactAnalyzer()
    result = analyzer.analyze(context)

    assert any(fc.path == "app/billing.py" for fc in result.files_to_create)
    assert "app/billing.py" in result.direct_files


def test_file_deletion_requested() -> None:
    """Verify that file requested to be deleted is added to files_to_delete."""
    context = _build_sample_context(
        title="Delete file app/legacy.py",
        description="Remove file app/legacy.py completely",
        relevant_files=["app/legacy.py"],
        relevant_symbols=[],
        matches=[
            IssueMatch(
                file_path="app/legacy.py",
                symbol_name=None,
                score=10.0,
                reasons=[],
            )
        ],
        references=[
            IssueReference(reference_type=ReferenceType.FILE, value="app/legacy.py")
        ],
    )
    analyzer = ImpactAnalyzer()
    result = analyzer.analyze(context)

    assert any(fc.path == "app/legacy.py" for fc in result.files_to_delete)
    assert not any(fc.path == "app/legacy.py" for fc in result.files_to_modify)
