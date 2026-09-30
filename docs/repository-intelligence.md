# AgentForge Repository Intelligence System

This document details the architecture, design principles, components, and API specification of the **Repository Intelligence** subsystem implemented in Sprint 1.

---

## 1. System Architecture & Pipeline

The Repository Intelligence layer provides deterministic codebase understanding without relying on external LLM inference or vector databases.

```text
Repository Root Path
        │
        ▼
 ┌──────────────┐
 │ File Scanner │ ──► Ignore Rules (.git, .venv, node_modules, .gitignore)
 └──────┬───────┘
        │
        ▼
 ┌──────────────┐
 │ Metadata     │ ──► Language Detection & Test File Classifier
 └──────┬───────┘
        │
        ▼
 ┌──────────────┐
 │ AST Parser   │ ──► Python AST Symbols (Classes, Functions, Methods, Imports)
 └──────┬───────┘
        │
        ▼
 ┌──────────────┐
 │ Code Chunker │ ──► Structural Python Chunks & Line Fallback Chunks
 └──────┬───────┘
        │
        ▼
 ┌──────────────┐
 │ Index Engine │ ──► In-Memory Index & Deterministic Keyword Search
 └──────────────┘
```

---

## 2. Core Components

### A. Repository Scanner (`backend/app/repository/scanner.py`)
- Recursively walks repository trees while enforcing path traversal validation (`validate_safe_path`).
- Excludes common build, virtual environment, and dependency directories (`.git`, `.venv`, `node_modules`, `__pycache__`).
- Respects custom `.gitignore` rules.
- Enforces file size limits (`MAX_FILE_SIZE = 1MB`) and detects binary file encodings safely.

### B. Metadata & Language Detection (`backend/app/repository/metadata.py`)
- Maps file extensions to canonical language identifiers (Python, JavaScript, TypeScript, Java, C, C++, Go, Rust, Markdown, JSON, YAML, TOML, CSS, HTML, Bash, SQL).
- Classifies test files based on directory naming (`tests/`, `__tests__/`) and file suffix conventions (`test_*.py`, `*.spec.ts`, `*.test.js`).

### C. Python AST Parser (`backend/app/parsing/python_parser.py`)
- Uses Python's standard `ast` module to extract code symbols deterministically.
- Captures:
  - Classes (`class ClassName`)
  - Functions (`def func_name(...)`)
  - Class Methods (`def method_name(self, ...)`) with parent class bindings.
  - Import statements (`import x`, `from y import z`)
  - Function signatures and docstrings.
- Gracefully captures syntax errors without aborting repository scanning.

### D. Structural Code Chunker (`backend/app/indexing/chunker.py`)
- Generates structural chunks bounded by symbol start and end lines for Python files.
- Uses an overlapping line-based fallback strategy (50 lines with 10-line overlap) for non-Python or unparsed files.

### E. In-Memory Index & Deterministic Search (`backend/app/indexing/index.py`)
- Fast in-memory index supporting `get_by_file()`, `get_by_symbol()`, and `search()`.
- Deterministic scoring: Symbol match (weight 3.0), File path match (weight 2.0), Content match (weight 1.0).

---

## 3. API Endpoints

### POST `/api/v1/repositories/analyze`
Accepts a local repository path and returns a full structural breakdown.

**Request:**
```json
{
  "path": "/workspace/sample-project"
}
```

**Response:**
```json
{
  "repository_name": "sample-project",
  "root_path": "/workspace/sample-project",
  "total_files": 6,
  "total_source_files": 6,
  "total_test_files": 1,
  "language_counts": {
    "python": 4,
    "markdown": 1,
    "toml": 1
  },
  "symbols_count": 8,
  "chunks_count": 8,
  "parse_errors": []
}
```

### POST `/api/v1/repositories/search`
Queries the active index for relevant code chunks.

**Request:**
```json
{
  "repository_path": "/workspace/sample-project",
  "query": "authentication",
  "limit": 5
}
```
