"""Unit tests for Repository-Aware Issue Matcher."""

from backend.app.indexing.index import RepositoryIndex
from backend.app.issue.matcher import IssueMatcher
from backend.app.issue.models import (
    EndpointReference,
    Issue,
    IssueAnalysis,
    IssueReference,
    ReferenceType,
)
from backend.app.repository.models import (
    CodeChunk,
    RepositoryFile,
    RepositoryInfo,
    Symbol,
    SymbolType,
)


def test_match_exact_file_and_symbol_references() -> None:
    """Verify exact file and symbol matching with scoring."""
    index = RepositoryIndex()
    chunk1 = CodeChunk(
        chunk_id="chunk1",
        file_path="backend/app/auth.py",
        language="python",
        symbol_name="verify_token",
        symbol_type=SymbolType.FUNCTION,
        start_line=1,
        end_line=10,
        content="def verify_token(token: str):\n    pass\n",
    )
    chunk2 = CodeChunk(
        chunk_id="chunk2",
        file_path="backend/app/db.py",
        language="python",
        symbol_name="get_db",
        symbol_type=SymbolType.FUNCTION,
        start_line=1,
        end_line=5,
        content="def get_db():\n    pass\n",
    )
    index.add_many([chunk1, chunk2])

    analysis = IssueAnalysis(
        issue=Issue(
            title="Fix authentication in backend/app/auth.py",
            description="Update `verify_token` function.",
        ),
        requirements=[],
        acceptance_criteria=[],
        constraints=[],
        keywords=["authentication", "token"],
        references=[
            IssueReference(
                reference_type=ReferenceType.FILE, value="backend/app/auth.py"
            ),
            IssueReference(reference_type=ReferenceType.SYMBOL, value="verify_token"),
            IssueReference(
                reference_type=ReferenceType.FILE, value="backend/app/missing.py"
            ),
        ],
        endpoints=[],
    )

    matcher = IssueMatcher()
    matches, unresolved = matcher.match(analysis, index)

    assert len(matches) > 0
    top_match = matches[0]
    assert top_match.file_path == "backend/app/auth.py"
    assert top_match.score >= 20.0  # +10 file, +10 symbol, plus keyword points
    assert "backend/app/missing.py" in unresolved


def test_match_endpoint_and_test_boost() -> None:
    """Verify endpoint matching and test file boost."""
    index = RepositoryIndex()
    src_chunk = CodeChunk(
        chunk_id="src1",
        file_path="backend/app/routes.py",
        language="python",
        symbol_name="login_route",
        symbol_type=SymbolType.FUNCTION,
        start_line=1,
        end_line=15,
        content='@app.post("/api/v1/auth/login")\ndef login_route():\n    pass\n',
    )
    test_chunk = CodeChunk(
        chunk_id="test1",
        file_path="tests/test_routes.py",
        language="python",
        symbol_name="test_login",
        symbol_type=SymbolType.FUNCTION,
        start_line=1,
        end_line=10,
        content="def test_login():\n    pass\n",
    )
    index.add_many([src_chunk, test_chunk])

    analysis = IssueAnalysis(
        issue=Issue(
            title="Add test for POST /api/v1/auth/login endpoint",
            description="Write integration unit test for login route.",
        ),
        requirements=[],
        acceptance_criteria=[],
        constraints=[],
        keywords=["login", "auth"],
        references=[],
        endpoints=[EndpointReference(method="POST", path="/api/v1/auth/login")],
    )

    repo_info = RepositoryInfo(
        repository_name="AgentForge",
        root_path="/workspace/AgentForge",
        total_files=2,
        total_source_files=1,
        total_test_files=1,
        language_counts={"python": 2},
        files=[
            RepositoryFile(
                relative_path="backend/app/routes.py",
                file_extension=".py",
                language="python",
                size_bytes=100,
                line_count=15,
            ),
            RepositoryFile(
                relative_path="tests/test_routes.py",
                file_extension=".py",
                language="python",
                size_bytes=80,
                line_count=10,
                is_test=True,
            ),
        ],
        symbols=[
            Symbol(
                name="login_route",
                symbol_type=SymbolType.FUNCTION,
                file_path="backend/app/routes.py",
                start_line=1,
                end_line=15,
            ),
            Symbol(
                name="test_login",
                symbol_type=SymbolType.FUNCTION,
                file_path="tests/test_routes.py",
                start_line=1,
                end_line=10,
            ),
        ],
        chunks=[src_chunk, test_chunk],
    )

    matcher = IssueMatcher()
    matches, unresolved = matcher.match(analysis, index, repo_info)

    assert len(matches) == 2
    # Verify src_chunk got endpoint match (+8)
    route_match = next(m for m in matches if m.file_path == "backend/app/routes.py")
    assert any(r.rule_name == "endpoint_reference" for r in route_match.reasons)

    # Verify test_chunk got test file boost (+2)
    test_match = next(m for m in matches if m.file_path == "tests/test_routes.py")
    assert any(r.rule_name == "test_file_boost" for r in test_match.reasons)
