"""Helper utilities for AST node inspection and signature formatting."""

import ast


def extract_docstring(node: ast.AST) -> str | None:
    """Extract docstring from an AST node if present."""
    if isinstance(
        node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)
    ):
        doc = ast.get_docstring(node, clean=True)
        return doc if doc else None
    return None


def format_function_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Format parameter signature string for a function or method AST node."""
    args = node.args
    params: list[str] = []

    # Positional args
    for arg in args.args:
        ann = f": {ast.unparse(arg.annotation)}" if arg.annotation else ""
        params.append(f"{arg.arg}{ann}")

    # Varargs (*args)
    if args.vararg:
        ann = (
            f": {ast.unparse(args.vararg.annotation)}" if args.vararg.annotation else ""
        )
        params.append(f"*{args.vararg.arg}{ann}")

    # Kwargs (**kwargs)
    if args.kwarg:
        ann = f": {ast.unparse(args.kwarg.annotation)}" if args.kwarg.annotation else ""
        params.append(f"**{args.kwarg.arg}{ann}")

    returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    return f"def {node.name}({', '.join(params)}){returns}"
