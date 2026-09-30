"""Unit tests for AST symbol extraction helpers."""

import ast

from backend.app.parsing.symbols import extract_docstring, format_function_signature


def test_extract_docstring() -> None:
    """Verify docstring extraction from function and class nodes."""
    source = '''
def foo():
    """Docstring for foo."""
    pass

class Bar:
    """Docstring for Bar."""
    pass
'''
    tree = ast.parse(source)
    func_node = tree.body[0]
    class_node = tree.body[1]

    assert extract_docstring(func_node) == "Docstring for foo."
    assert extract_docstring(class_node) == "Docstring for Bar."


def test_format_function_signature() -> None:
    """Verify function signature formatting."""
    source = "def add(a: int, b: int = 10) -> int: return a + b"
    tree = ast.parse(source)
    func_node = tree.body[0]
    assert isinstance(func_node, ast.FunctionDef)

    sig = format_function_signature(func_node)
    assert "def add(a: int, b: int) -> int" in sig
