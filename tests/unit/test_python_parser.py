"""Unit tests for PythonASTParser."""

from backend.app.parsing.python_parser import PythonASTParser
from backend.app.repository.models import SymbolType


def test_python_parser_extracts_symbols() -> None:
    """Verify parsing functions, classes, methods, imports, and docstrings."""
    clean_source = '''"""Module docstring."""
import os
from pathlib import Path

def calculate_sum(a: int, b: int) -> int:
    """Calculate sum of two integers."""
    return a + b

class User:
    """User entity representation."""

    def __init__(self, name: str) -> None:
        self.name = name

    def login(self) -> bool:
        """User login method."""
        return True
'''

    parser = PythonASTParser()
    symbols, error = parser.parse_source(clean_source, file_path="app/user.py")

    assert error is None
    symbol_names = [s.name for s in symbols]

    assert "calculate_sum" in symbol_names
    assert "User" in symbol_names
    assert "__init__" in symbol_names
    assert "login" in symbol_names
    assert "import os" in symbol_names
    assert "from pathlib import Path" in symbol_names

    # Check symbol types and parent bindings
    method_login = next(s for s in symbols if s.name == "login")
    assert method_login.symbol_type == SymbolType.METHOD
    assert method_login.parent_symbol == "User"
    assert method_login.docstring == "User login method."


def test_python_parser_syntax_error_handling() -> None:
    """Verify parser handles syntax errors gracefully without crashing."""
    broken_source = "def broken_func(:\n    pass"

    parser = PythonASTParser()
    symbols, error = parser.parse_source(broken_source, file_path="app/broken.py")

    assert len(symbols) == 0
    assert error is not None
    assert "SyntaxError" in error
