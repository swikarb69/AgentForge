"""Unit tests for CodeChunker."""

from backend.app.indexing.chunker import CodeChunker
from backend.app.repository.models import Symbol, SymbolType


def test_chunk_python_structural() -> None:
    """Verify structural chunking for Python file with symbols."""
    content = """def greet(name: str) -> str:
    return f"Hello {name}"

class Math:
    def add(self, a: int, b: int) -> int:
        return a + b
"""
    symbols = [
        Symbol(
            name="greet",
            symbol_type=SymbolType.FUNCTION,
            file_path="app/greet.py",
            start_line=1,
            end_line=2,
        ),
        Symbol(
            name="Math",
            symbol_type=SymbolType.CLASS,
            file_path="app/greet.py",
            start_line=4,
            end_line=6,
        ),
        Symbol(
            name="add",
            symbol_type=SymbolType.METHOD,
            file_path="app/greet.py",
            start_line=5,
            end_line=6,
            parent_symbol="Math",
        ),
    ]

    chunker = CodeChunker()
    chunks = chunker.chunk_file("app/greet.py", content, "python", symbols)

    assert len(chunks) == 3
    symbol_names = [c.symbol_name for c in chunks]
    assert "greet" in symbol_names
    assert "Math" in symbol_names
    assert "add" in symbol_names

    greet_chunk = next(c for c in chunks if c.symbol_name == "greet")
    assert greet_chunk.start_line == 1
    assert "def greet" in greet_chunk.content


def test_chunk_fallback_non_python() -> None:
    """Verify fallback line-based chunking for non-Python file."""
    content = "\n".join([f"line {i}" for i in range(1, 101)])  # 100 lines

    chunker = CodeChunker(fallback_lines=30, fallback_overlap=10)
    chunks = chunker.chunk_file("docs/readme.md", content, "markdown")

    assert len(chunks) > 1
    assert all(c.language == "markdown" for c in chunks)
    assert all(c.symbol_name is None for c in chunks)
    assert chunks[0].start_line == 1
    assert chunks[0].end_line == 30
