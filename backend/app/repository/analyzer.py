"""High-level repository analysis pipeline."""

from pathlib import Path

from backend.app.indexing.chunker import CodeChunker
from backend.app.indexing.index import RepositoryIndex
from backend.app.parsing.python_parser import PythonASTParser
from backend.app.repository.models import (
    CodeChunk,
    RepositoryFile,
    RepositoryInfo,
    SearchResult,
    Symbol,
)
from backend.app.repository.scanner import RepositoryScanner


class RepositoryAnalyzer:
    """Orchestrates scanning, AST parsing, chunking, and indexing for codebases."""

    def __init__(self) -> None:
        self.scanner = RepositoryScanner()
        self.parser = PythonASTParser()
        self.chunker = CodeChunker()
        self.index = RepositoryIndex()

    def analyze(self, repository_path: str | Path) -> RepositoryInfo:
        """Execute complete repository analysis pipeline.

        Returns:
            Structured RepositoryInfo summary.
        """
        root_path = Path(repository_path).resolve()
        files: list[RepositoryFile] = self.scanner.scan(root_path)

        all_symbols: list[Symbol] = []
        all_chunks: list[CodeChunk] = []
        parse_errors: list[str] = []
        language_counts: dict[str, int] = {}
        total_source_files = 0
        total_test_files = 0

        self.index.clear()

        for file_meta in files:
            if file_meta.is_ignored:
                continue

            total_source_files += 1
            if file_meta.is_test:
                total_test_files += 1

            lang = file_meta.language
            language_counts[lang] = language_counts.get(lang, 0) + 1

            abs_file_path = root_path / file_meta.relative_path
            content = self._read_file_content(abs_file_path)

            file_symbols: list[Symbol] = []

            # Parse Python AST if language is python and file was not skipped
            if lang == "python" and file_meta.parse_status == "success" and content:
                extracted_syms, parse_err = self.parser.parse_source(
                    content, file_path=file_meta.relative_path
                )
                if parse_err:
                    file_meta.parse_status = "failed"
                    file_meta.error_message = parse_err
                    parse_errors.append(f"{file_meta.relative_path}: {parse_err}")
                else:
                    file_symbols = extracted_syms
                    all_symbols.extend(extracted_syms)

            # Generate chunks
            if content and file_meta.parse_status != "skipped":
                file_chunks = self.chunker.chunk_file(
                    file_path=file_meta.relative_path,
                    content=content,
                    language=lang,
                    symbols=file_symbols if lang == "python" else None,
                )
                all_chunks.extend(file_chunks)
                self.index.add_many(file_chunks)

        repo_info = RepositoryInfo(
            repository_name=root_path.name,
            root_path=str(root_path),
            total_files=len(files),
            total_source_files=total_source_files,
            total_test_files=total_test_files,
            language_counts=language_counts,
            files=files,
            symbols=all_symbols,
            chunks=all_chunks,
            parse_errors=parse_errors,
        )

        return repo_info

    def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        """Search current active index."""
        return self.index.search(query=query, limit=limit)

    @staticmethod
    def _read_file_content(file_path: Path) -> str:
        """Safely read text content of file."""
        try:
            return file_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return ""
