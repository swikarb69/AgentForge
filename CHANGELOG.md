# Changelog

All notable changes to AgentForge will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.5.0] - 2026-11-21

### Added - Sprint 4: Code Generation Engine
- **Generation Domain Models**: Strongly typed Pydantic models for `GeneratedFileChange`, `PatchValidation`, `GenerationRequest`, `GenerationResult`, `CreateGenerationRequest`, `FileContext`, and `SourceContext` (`backend/app/generation/models.py`).
- **Bounded Source Context Builder**: Source context gathering (`backend/app/generation/context.py`) enforcing target file authorization, SHA256 baseline hashing, size bounds (`MAX_CONTEXT_SIZE`), symlink escape rejection, and protected path safeguards.
- **Pluggable Provider Interface**: Abstract provider contract (`CodeGenerationProvider`) with default offline deterministic mock provider (`MockCodeGenerationProvider`) and optional Gemini REST integration (`GeminiCodeGenerationProvider`) (`backend/app/generation/provider.py`).
- **Versioned Prompt Engineering**: Versioned prompt builder (`PROMPT_VERSION = v1.0`) with prompt-injection delimiters and JSON schema response contract (`backend/app/generation/prompts.py`).
- **Structured Code Generator**: High-level generator (`CodeGenerator`) orchestrating context and provider, verifying proposal boundaries and task/requirement mappings (`backend/app/generation/generator.py`).
- **Patch Validator**: Validation engine (`PatchValidator`) verifying plan authorization, path safety, and running AST syntax parsing on proposed Python code (`backend/app/generation/validator.py`).
- **Unified Diff Patch Builder & Artifact Management**: Clean unified diff generation (`a/path` vs `b/path`, `/dev/null` for creations) and artifact persistence in `agentforge-artifacts/generation/` strictly outside the target repository (`backend/app/generation/patch.py`).
- **Generation Service & REST APIs**: Endpoints exposing `POST /api/v1/generation/create`, `GET /api/v1/generation/{generation_id}`, and `GET /api/v1/generation/{generation_id}/patch` (`backend/app/api/routes/generation.py`).
- **Tests & Immutability Suite**: Unit and integration test suite with 156 passing tests, 90.14% coverage, and verification of zero target repository mutations (`tests/unit/test_generation_*.py`, `tests/unit/test_generator.py`, `tests/unit/test_patch_*.py`, `tests/integration/test_generation_pipeline.py`).
- **Documentation**: Technical specifications in `docs/code-generation.md`.

## [0.4.0] - 2026-10-29

### Added - Sprint 3: Planning Engine
- **Planning Domain Models**: Strongly typed Pydantic models for `ImplementationPlan`, `PlanTask`, `FileChange`, `SymbolChange`, `TestPlan`, `Dependency`, `Risk`, `Assumption`, and `PlanValidation` (`backend/app/planning/models.py`).
- **Change Impact Analyzer**: Deterministic identification of directly and indirectly affected files, symbols, test files, and new files (`backend/app/planning/impact.py`).
- **Task Generator & Traceability**: Bidirectional mapping connecting requirements to tasks and acceptance criteria to concrete verification cases with deterministic execution ordering (`backend/app/planning/tasks.py`).
- **Dependency & Risk Analysis**: Task dependency graph construction with DFS-based cycle detection and architectural risk evaluation with severity rating and actionable mitigations (`backend/app/planning/dependencies.py`, `backend/app/planning/risks.py`).
- **Deterministic Plan Validator**: Complete structural, traceability, dependency cycle, repository existence, and ambiguity validation engine (`backend/app/planning/validator.py`).
- **Planning Engine Service**: High-level orchestrator connecting `IssueContext` to `ImplementationPlan` (`backend/app/planning/service.py`).
- **Planning REST API**: Endpoints exposing `POST /api/v1/planning/create` and `POST /api/v1/planning/validate` (`backend/app/api/routes/planning.py`).
- **Fixtures & Tests**: Realistic planning fixture scenarios (`agentforge-fixtures/plans/`) and comprehensive unit & integration test suite (`tests/unit/test_*.py`, `tests/integration/test_planning_pipeline.py`).
- **Documentation**: Comprehensive planning engine specification in `docs/planning-engine.md`.

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
