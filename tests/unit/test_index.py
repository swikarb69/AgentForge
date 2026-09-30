"""Unit tests for RepositoryIndex."""

from backend.app.indexing.index import RepositoryIndex
from backend.app.repository.models import CodeChunk, SymbolType


def test_index_add_and_retrieval() -> None:
    """Verify adding chunks and retrieving by file and symbol."""
    chunk1 = CodeChunk(
        chunk_id="c1",
        file_path="app/auth.py",
        language="python",
        symbol_name="authenticate_user",
        symbol_type=SymbolType.FUNCTION,
        start_line=1,
        end_line=10,
        content="def authenticate_user(token):\n    return True",
    )
    chunk2 = CodeChunk(
        chunk_id="c2",
        file_path="app/auth.py",
        language="python",
        symbol_name="AuthService",
        symbol_type=SymbolType.CLASS,
        start_line=12,
        end_line=25,
        content="class AuthService:\n    pass",
    )

    index = RepositoryIndex()
    index.add_many([chunk1, chunk2])

    assert len(index) == 2
    assert len(index.get_by_file("app/auth.py")) == 2
    assert len(index.get_by_symbol("authenticate_user")) == 1

    symbol_chunk = index.get_by_symbol("authenticate_user")[0]
    assert symbol_chunk.chunk_id == "c1"


def test_index_search_ranking() -> None:
    """Verify deterministic search ranking by field matches."""
    chunk1 = CodeChunk(
        chunk_id="c1",
        file_path="app/auth.py",
        language="python",
        symbol_name="login",
        symbol_type=SymbolType.FUNCTION,
        start_line=1,
        end_line=5,
        content="def login(): return True",
    )
    chunk2 = CodeChunk(
        chunk_id="c2",
        file_path="app/utils.py",
        language="python",
        symbol_name="helper",
        symbol_type=SymbolType.FUNCTION,
        start_line=1,
        end_line=5,
        content="# perform login check\ndef helper(): pass",
    )

    index = RepositoryIndex()
    index.add_many([chunk1, chunk2])

    results = index.search("login")
    assert len(results) == 2

    # Top result should be chunk1 because 'login' matched symbol_name (weight 3)
    top_result = results[0]
    assert top_result.chunk.chunk_id == "c1"
    assert "symbol_name" in top_result.matched_fields
    assert top_result.score > results[1].score


def test_index_clear() -> None:
    """Verify clearing the index removes all entries."""
    chunk = CodeChunk(
        chunk_id="c1",
        file_path="main.py",
        language="python",
        start_line=1,
        end_line=5,
        content="print('hi')",
    )

    index = RepositoryIndex()
    index.add(chunk)
    assert len(index) == 1

    index.clear()
    assert len(index) == 0
    assert index.get_by_file("main.py") == []
