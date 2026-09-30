"""Python AST parser extracting classes, functions, methods, imports, and docstrings."""

import ast
from pathlib import Path

from backend.app.parsing.symbols import extract_docstring, format_function_signature
from backend.app.repository.models import Symbol, SymbolType


class PythonASTParser:
    """Parser utilizing Python standard library ast to extract structured symbols."""

    def parse_file(self, file_path: str | Path) -> tuple[list[Symbol], str | None]:
        """Read and parse a Python source file."""
        path_obj = Path(file_path)
        try:
            source = path_obj.read_text(encoding="utf-8", errors="replace")
            return self.parse_source(source, str(file_path))
        except Exception as err:
            return [], f"Failed to read file: {err}"

    def parse_source(
        self, source_code: str, file_path: str = "<string>"
    ) -> tuple[list[Symbol], str | None]:
        """Parse Python source code string and extract AST symbols.

        Returns:
            tuple of (extracted_symbols, error_message)
        """
        try:
            tree = ast.parse(source_code, filename=file_path)
        except SyntaxError as err:
            return [], f"SyntaxError line {err.lineno}: {err.msg}"
        except Exception as err:
            return [], f"AST parse error: {err}"

        symbols: list[Symbol] = []

        # Extract Module docstring if present
        mod_doc = extract_docstring(tree)
        if mod_doc:
            symbols.append(
                Symbol(
                    name=Path(file_path).stem if file_path != "<string>" else "module",
                    symbol_type=SymbolType.MODULE,
                    file_path=file_path,
                    start_line=1,
                    end_line=len(source_code.splitlines()) or 1,
                    docstring=mod_doc,
                )
            )

        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                symbols.extend(self._extract_import_symbols(node, file_path))
            elif isinstance(node, ast.ClassDef):
                symbols.extend(self._extract_class_symbols(node, file_path))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                symbols.append(
                    self._extract_function_symbol(node, file_path, parent=None)
                )

        return symbols, None

    def _extract_class_symbols(
        self, node: ast.ClassDef, file_path: str
    ) -> list[Symbol]:
        """Extract a class symbol and its internal method symbols."""
        symbols: list[Symbol] = []
        start_line = getattr(node, "lineno", 1)
        end_line = getattr(node, "end_lineno", start_line)

        class_symbol = Symbol(
            name=node.name,
            symbol_type=SymbolType.CLASS,
            file_path=file_path,
            start_line=start_line,
            end_line=end_line,
            docstring=extract_docstring(node),
        )
        symbols.append(class_symbol)

        # Inspect class body for methods
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                method_sym = self._extract_function_symbol(
                    item, file_path, parent=node.name
                )
                method_sym.symbol_type = SymbolType.METHOD
                symbols.append(method_sym)

        return symbols

    @staticmethod
    def _extract_function_symbol(
        node: ast.FunctionDef | ast.AsyncFunctionDef, file_path: str, parent: str | None
    ) -> Symbol:
        """Extract a top-level function or class method symbol."""
        start_line = getattr(node, "lineno", 1)
        end_line = getattr(node, "end_lineno", start_line)
        sig = format_function_signature(node)

        return Symbol(
            name=node.name,
            symbol_type=SymbolType.FUNCTION if not parent else SymbolType.METHOD,
            file_path=file_path,
            start_line=start_line,
            end_line=end_line,
            parent_symbol=parent,
            signature=sig,
            docstring=extract_docstring(node),
        )

    @staticmethod
    def _extract_import_symbols(
        node: ast.Import | ast.ImportFrom, file_path: str
    ) -> list[Symbol]:
        """Extract import statement symbols."""
        symbols: list[Symbol] = []
        start_line = getattr(node, "lineno", 1)

        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name
                symbols.append(
                    Symbol(
                        name=f"import {name}",
                        symbol_type=SymbolType.IMPORT,
                        file_path=file_path,
                        start_line=start_line,
                        end_line=start_line,
                    )
                )
        elif isinstance(node, ast.ImportFrom):
            module_name = node.module or ""
            for alias in node.names:
                name = alias.asname or alias.name
                symbols.append(
                    Symbol(
                        name=f"from {module_name} import {name}",
                        symbol_type=SymbolType.IMPORT,
                        file_path=file_path,
                        start_line=start_line,
                        end_line=start_line,
                    )
                )

        return symbols
