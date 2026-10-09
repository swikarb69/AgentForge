# Refactor Repository Scanner and Chunking Logic

### Requirements
- Refactor `RepositoryScanner.scan` in `backend/app/repository/scanner.py` to support multi-threading.
- Optimize `CodeChunker.chunk_file` performance in `backend/app/indexing/chunker.py`.

### Acceptance Criteria
- Codebase scanning throughput must improve by at least 20%.
- All existing tests in `tests/unit/test_scanner.py` must pass.

### Constraints
- Must not alter public API contract of `RepositoryAnalyzer`.
