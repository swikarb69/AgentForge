"""In-memory deterministic repository index and search engine."""

from collections.abc import Sequence

from backend.app.repository.models import CodeChunk, SearchResult


class RepositoryIndex:
    """In-memory index supporting deterministic symbol and keyword retrieval."""

    def __init__(self) -> None:
        self._chunks: dict[str, CodeChunk] = {}
        self._by_file: dict[str, list[CodeChunk]] = {}
        self._by_symbol: dict[str, list[CodeChunk]] = {}

    def add(self, chunk: CodeChunk) -> None:
        """Add a single chunk to the index."""
        self._chunks[chunk.chunk_id] = chunk

        # Index by file
        self._by_file.setdefault(chunk.file_path, []).append(chunk)

        # Index by symbol if present
        if chunk.symbol_name:
            self._by_symbol.setdefault(chunk.symbol_name, []).append(chunk)

    def add_many(self, chunks: Sequence[CodeChunk]) -> None:
        """Add multiple chunks to the index."""
        for chunk in chunks:
            self.add(chunk)

    def get_by_file(self, file_path: str) -> list[CodeChunk]:
        """Retrieve all indexed chunks for a given file path."""
        return self._by_file.get(file_path, [])

    def get_by_symbol(self, symbol_name: str) -> list[CodeChunk]:
        """Retrieve all indexed chunks for a given symbol name."""
        return self._by_symbol.get(symbol_name, [])

    def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        """Perform deterministic keyword and symbol search over indexed chunks.

        Scoring rules:
        - Match in symbol_name: 3.0 points
        - Match in file_path: 2.0 points
        - Match in chunk content: 1.0 point

        Returns:
            Ranked list of SearchResult objects sorted by score descending.
        """
        if not query.strip() or not self._chunks:
            return []

        keywords = [k.lower() for k in query.split() if k.strip()]
        results: list[SearchResult] = []

        for chunk in self._chunks.values():
            score = 0.0
            matched_fields: list[str] = []

            symbol_lower = (chunk.symbol_name or "").lower()
            file_lower = chunk.file_path.lower()
            content_lower = chunk.content.lower()

            for kw in keywords:
                if chunk.symbol_name and kw in symbol_lower:
                    score += 3.0
                    if "symbol_name" not in matched_fields:
                        matched_fields.append("symbol_name")

                if kw in file_lower:
                    score += 2.0
                    if "file_path" not in matched_fields:
                        matched_fields.append("file_path")

                if kw in content_lower:
                    score += 1.0
                    if "content" not in matched_fields:
                        matched_fields.append("content")

            if score > 0.0:
                # Normalize score (cap max score at 1.0 for interface consistency)
                norm_score = min(1.0, round(score / (len(keywords) * 6.0), 2))
                results.append(
                    SearchResult(
                        chunk=chunk,
                        score=norm_score,
                        matched_fields=matched_fields,
                    )
                )

        # Sort descending by score
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]

    def clear(self) -> None:
        """Clear all indexed entries."""
        self._chunks.clear()
        self._by_file.clear()
        self._by_symbol.clear()

    def __len__(self) -> int:
        return len(self._chunks)
