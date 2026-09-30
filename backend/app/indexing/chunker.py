"""Structural and fallback line-based code chunker."""

import hashlib

from backend.app.repository.models import CodeChunk, Symbol, SymbolType


class CodeChunker:
    """Generates structural Python chunks and line fallback chunks."""

    def __init__(self, fallback_lines: int = 50, fallback_overlap: int = 10) -> None:
        self.fallback_lines = fallback_lines
        self.fallback_overlap = fallback_overlap

    def chunk_file(
        self,
        file_path: str,
        content: str,
        language: str,
        symbols: list[Symbol] | None = None,
    ) -> list[CodeChunk]:
        """Chunk a single file structurally if symbols present, or line-based if not."""
        lines = content.splitlines(keepends=True)
        if not lines:
            return []

        # If language is Python and we have extracted symbols, chunk structurally
        structural_symbols = [
            s
            for s in (symbols or [])
            if s.symbol_type
            in {SymbolType.CLASS, SymbolType.FUNCTION, SymbolType.METHOD}
        ]

        if language == "python" and structural_symbols:
            return self._chunk_python_structural(
                file_path, lines, language, structural_symbols
            )

        # Fallback line-based chunking
        return self._chunk_fallback_lines(file_path, lines, language)

    def _chunk_python_structural(
        self,
        file_path: str,
        lines: list[str],
        language: str,
        symbols: list[Symbol],
    ) -> list[CodeChunk]:
        """Create structural chunks bounded by symbol start and end lines."""
        chunks: list[CodeChunk] = []

        for sym in symbols:
            # 1-indexed to 0-indexed bounds
            start_idx = max(0, sym.start_line - 1)
            end_idx = min(len(lines), sym.end_line)

            chunk_lines = lines[start_idx:end_idx]
            chunk_content = "".join(chunk_lines)

            if not chunk_content.strip():
                continue

            chunk_id = self._generate_chunk_id(
                file_path, sym.name, sym.start_line, sym.end_line
            )

            chunk = CodeChunk(
                chunk_id=chunk_id,
                file_path=file_path,
                language=language,
                symbol_name=sym.name,
                symbol_type=sym.symbol_type,
                start_line=sym.start_line,
                end_line=end_idx,
                content=chunk_content,
            )
            chunks.append(chunk)

        # If no structural chunks generated, fallback to line-based
        if not chunks:
            return self._chunk_fallback_lines(file_path, lines, language)

        return chunks

    def _chunk_fallback_lines(
        self, file_path: str, lines: list[str], language: str
    ) -> list[CodeChunk]:
        """Create overlapping line-based fallback chunks for unparsed files."""
        chunks: list[CodeChunk] = []
        total_lines = len(lines)
        step = max(1, self.fallback_lines - self.fallback_overlap)

        for start_idx in range(0, total_lines, step):
            end_idx = min(total_lines, start_idx + self.fallback_lines)
            chunk_lines = lines[start_idx:end_idx]
            chunk_content = "".join(chunk_lines)

            if not chunk_content.strip():
                continue

            start_line = start_idx + 1
            end_line = end_idx

            chunk_id = self._generate_chunk_id(file_path, "block", start_line, end_line)

            chunk = CodeChunk(
                chunk_id=chunk_id,
                file_path=file_path,
                language=language,
                symbol_name=None,
                symbol_type=None,
                start_line=start_line,
                end_line=end_line,
                content=chunk_content,
            )
            chunks.append(chunk)

            if end_idx >= total_lines:
                break

        return chunks

    @staticmethod
    def _generate_chunk_id(
        file_path: str, label: str, start_line: int, end_line: int
    ) -> str:
        """Generate a deterministic chunk identifier string."""
        raw_key = f"{file_path}:{label}:{start_line}-{end_line}"
        digest = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:8]
        clean_path = file_path.replace("/", "_").replace("\\", "_")
        return f"chk_{clean_path}_{start_line}_{digest}"
