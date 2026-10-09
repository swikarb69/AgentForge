# Changelog

All notable changes to AgentForge will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2026-10-09

### Added - Sprint 2: Issue Intelligence
- **Issue Parser**: Markdown section, bullet point list, and code block parser (`backend/app/issue/parser.py`).
- **Requirement & Criteria Extractor**: Deterministic functional requirement and acceptance criteria extractor (`backend/app/issue/extractor.py`).
- **Reference & Constraint Extractor**: Extraction of file paths, AST symbols, HTTP API endpoints, constraints, and keywords (`backend/app/issue/references.py`).
- **Repository-Aware Issue Matcher**: Deterministic scoring engine mapping issue requirements to code chunks/symbols with explainable match reasons (`backend/app/issue/matcher.py`).
- **Issue Context Builder**: High-level pipeline service assembling structured `IssueAnalysis` and mapping it to `IssueContext` (`backend/app/issue/service.py`).
- **Issues REST API**: OpenAPI endpoints (`POST /api/v1/issues/analyze` and `POST /api/v1/issues/context`) (`backend/app/api/routes/issues.py`).
- **Issue Fixtures & Tests**: Sample issue fixtures (`agentforge-fixtures/issues/`) and integration test suite (`tests/integration/test_issue_pipeline.py`).
- **Documentation**: Technical specifications in `docs/issue-intelligence.md`.

## [0.2.0] - 2026-09-30

### Added - Sprint 1: Repository Intelligence
- **Repository Scanner**: Recursive file tree scanner (`backend/app/repository/scanner.py`) enforcing path traversal validation, file size limits (`MAX_FILE_SIZE = 1MB`), binary file detection, and `.gitignore` pattern filtering (`filters.py`).
- **Language Detection & Metadata**: Extension-to-language mapping and test file classifier (`backend/app/repository/metadata.py`).
- **Python AST Code Intelligence**: Standard library AST parser (`backend/app/parsing/python_parser.py`) extracting classes, functions, class methods with parent bindings, import statements, parameter signatures, and docstrings.
- **Structural Code Chunking**: Semantic Python chunker (`backend/app/indexing/chunker.py`) creating symbol-bounded code chunks, and line-based fallback chunking for non-Python codebases.
- **In-Memory Repository Index & Deterministic Search**: Fast in-memory index engine (`backend/app/indexing/index.py`) providing ranked keyword and symbol search (`search()`, `get_by_file()`, `get_by_symbol()`).
- **Repository APIs**: OpenAPI endpoints (`POST /api/v1/repositories/analyze` and `POST /api/v1/repositories/search`).
- **Test Fixtures & Pipeline Tests**: Sample fixture codebase (`agentforge-fixtures/sample-project/`) and integration test suite (`tests/integration/test_repository_pipeline.py`).
- **Documentation**: Technical specifications in `docs/repository-intelligence.md`.

## [0.1.0] - 2026-09-25

### Added - Sprint 0: Foundation
- **Project Structure**: Standardized directory hierarchy (`backend/`, `sandbox/`, `tests/`, `docs/`, `.github/`).
- **FastAPI Core**: Base API foundation exposing `/api/v1/health` and `/api/v1/version`.
- **Configuration Engine**: Pydantic Settings implementation (`backend/app/core/config.py`).
- **Sandbox Baseline**: `sandbox/Dockerfile` and `sandbox/policies/security_policy.json` foundation.
- **Testing Architecture**: Pytest unit & integration test framework with coverage measurement.
- **Quality & CI/CD**: GitHub Actions workflow (`.github/workflows/ci.yml`) enforcing Ruff, MyPy, Pytest, and Bandit security scans.
- **Licensing**: Standard MIT License (Swikar Bhattarai).
- **Documentation**: Comprehensive suite (`docs/development-methodology.md`, `architecture.md`, `agent-system.md`, `security.md`, `testing.md`, `deployment.md`, `roadmap.md`, `README.md`).
