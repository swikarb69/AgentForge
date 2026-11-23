# Changelog

All notable changes to AgentForge will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.10.0] - 2026-11-23

### Added - Sprint 9: End-to-End Agent Orchestration Engine
- **Workflow Domain Models & Execution Context**: Strongly typed schemas for `WorkflowRun`, `WorkflowStage`, `WorkflowRequest`, `WorkflowConfiguration`, `ExecutionLimits`, `StateTransitionRecord`, and `WorkflowArtifactReferences` with cryptographic SHA-256 fingerprint tracking (`backend/app/orchestration/models.py`, `backend/app/orchestration/context.py`).
- **Deterministic State Machines**: Lifecycle state machines for workflow runs (`WorkflowStateMachine`) and individual stages (`StageStateMachine`) with strict monotonic transition rules and invariant validation (`backend/app/orchestration/states.py`, `backend/app/orchestration/transitions.py`).
- **Stage Registry & Subsystem Adapters**: Pluggable stage registry (`StageRegistry`) with topological dependency resolution and concrete stage adapters integrating Sprints 1–8 (`RepositoryAnalysisAdapter`, `IssueAnalysisAdapter`, `ImplementationPlanningAdapter`, `CodeGenerationAdapter`, `ExecutionVerificationAdapter`, `AutomaticRepairAdapter`, `CodeReviewAdapter`, `HumanApprovalAdapter`, `ControlledReleaseAdapter`) (`backend/app/orchestration/stages.py`, `backend/app/orchestration/stage_registry.py`, `backend/app/orchestration/adapters.py`).
- **Sequential DAG Execution Engine**: Production execution engine (`WorkflowExecutor`) supporting topological ordering, stage/workflow timeouts, exponential backoff retries, conditional stage skipping, and human approval pauses (`backend/app/orchestration/executor.py`).
- **Persistence & Optimistic Concurrency**: Abstract storage interface (`WorkflowStore`) and reference implementation (`InMemoryWorkflowStore`) with version-based optimistic locking, unique idempotency keys, and granular per-workflow exclusive locks (`backend/app/orchestration/storage.py`).
- **Recovery Manager & Artifact Integrity**: Resilient workflow recovery engine (`WorkflowRecoveryManager`) supporting pause, resume, cancel, crashed run recovery, and cryptographic SHA-256 artifact integrity verification (`backend/app/orchestration/recovery.py`).
- **Event-Driven Telemetry Bus**: Asynchronous pub/sub event bus (`WorkflowEventBus`) coordinating 16 distinct lifecycle events with recursive secret redaction (`redact_sensitive_data`) masking API keys, bearer tokens, and private credentials (`backend/app/orchestration/events.py`, `backend/app/orchestration/event_bus.py`).
- **Orchestration Service Facade & REST APIs**: Central orchestration coordinator (`OrchestrationService`) and FastAPI endpoints mounted at `/api/v1/workflows` (`POST /`, `GET /`, `GET /{id}`, `GET /{id}/context`, `POST /{id}/pause`, `POST /{id}/resume`, `POST /{id}/cancel`, `GET /{id}/events`) (`backend/app/orchestration/service.py`, `backend/app/api/routes/workflows.py`).
- **Integration Test Suite & Documentation**: Comprehensive unit and integration test suites with 88 tests and 87.58% coverage, plus detailed technical guide (`docs/end-to-end-orchestration.md`).

## [0.9.0] - 2026-11-23

### Added - Sprint 8: Human Approval & Controlled Release Engine
- **Human Authorization Layer**: Explicit human approval boundary with separation of duties, role-based authorization, risk-tiered policies (LOW to CRITICAL), and expiration controls (`backend/app/approval/`).
- **Tamper-Evident Audit Trail**: Append-only SHA-256 hash-chained audit store with recursive secret sanitization and integrity verification.
- **13-Point Release Readiness Engine**: Comprehensive validation across tests, reviews, approvals, policy adherence, git hygiene, and secret scanners.
- **Controlled Release & Zero Auto-Merge**: Controlled GitHub/Mock release engine strictly creating release candidate branches and pull requests, never merging to default branches.
- **REST APIs & Test Suite**: 8 REST endpoints at `/api/v1/approval/` and 100+ passing tests.

## [0.8.0] - 2026-11-22

### Added - Sprint 7: Code Review & Quality Assurance Engine
- **Review Models & Built-in Rules Catalog**: Strongly typed models (`ReviewFinding`, `ReviewReport`, `ReviewSummary`, `CodeEvidence`, `ReviewRequest`, `ReviewConfiguration`, `ReviewStatus`, `ReviewScope`) and 24 built-in deterministic rules across 7 categories (`CORRECTNESS`, `SECURITY`, `ARCHITECTURE`, `REGRESSION`, `TEST_COMPLIANCE`, `MAINTAINABILITY`, `RELIABILITY`, `PERFORMANCE`) with centralized `RuleRegistry` (`backend/app/review/models.py`, `backend/app/review/rules.py`, `backend/app/review/registry.py`).
- **Deterministic Static Analyzer**: AST-based code review evaluating undefined symbols (`CORR-001`), unreachable code (`CORR-002`), mutable default arguments (`CORR-003`), suspicious identity checks (`CORR-004`), missing docstrings (`MAINT-001`), deeply nested control flow (`MAINT-002`), high cyclomatic complexity (`MAINT-003`), excessive parameters (`MAINT-004`), global state mutation (`MAINT-005`), bare/empty exception handlers (`REL-001`), unhandled resource cleanup (`REL-002`), quadratic string concatenation (`PERF-001`), and repeated regex compilation (`PERF-002`) (`backend/app/review/static_analyzer.py`).
- **Security Vulnerability Analyzer**: Deterministic pattern detector for SQL injection with taint tracking (`SEC-001`), shell/command execution (`SEC-002`), dynamic code execution (`SEC-003`), hardcoded API keys, bearer tokens, and private secrets with live redaction (`SEC-004`), path traversal vulnerabilities (`SEC-005`), insecure deserialization via pickle/yaml (`SEC-006`), and insecure tempfile creation (`SEC-007`) (`backend/app/review/security_analyzer.py`).
- **Architecture & Regression Analyzer**: Circular import cycle detection via DFS graph traversal (`ARCH-001`), strict unidirectional layer boundary enforcement (`ARCH-002`), unapproved third-party dependencies (`ARCH-003`), excessive module coupling (`ARCH-004`), plan scope compliance (`REG-001`), missing planned targets/ACs (`REG-002`), unaddressed verification failures (`REG-003`), persistent repair regressions (`REG-004`), and test suite assertion quality (`TEST-001`, `TEST-002`) (`backend/app/review/architecture_analyzer.py`, `backend/app/review/regression_analyzer.py`).
- **Evidence Collector & Review Aggregator**: Bounded code snippet extractor ($\le 15$ lines) with active secret redaction and SHA-256 fingerprinting (`backend/app/review/evidence.py`). Finding deduplication, severity sorting, explainable 0-100 quality scoring, gating state machine (`APPROVED`, `APPROVED_WITH_COMMENTS`, `REQUIRES_CHANGES`, `BLOCKED`), blockers list, and actionable recommendations (`backend/app/review/aggregator.py`).
- **Review Service & REST APIs**: Review pipeline orchestrator with in-memory diff application, read-only repository scanning, and markdown report generation (`backend/app/review/service.py`). REST API routes: `POST /api/v1/reviews/create`, `GET /api/v1/reviews`, `GET /api/v1/reviews/{id}`, `GET /api/v1/reviews/{id}/findings`, `GET /api/v1/reviews/{id}/report`, and `GET /api/v1/reviews/rules` (`backend/app/api/routes/review.py`).
- **Unit, Integration, & Immutability Test Suites**: 440 total passing tests with $\ge 86\%$ coverage across `backend/app/review` and zero mutation of reviewed repositories (`tests/unit/test_review_*.py`, `tests/integration/test_review_pipeline.py`).
- **Documentation**: Comprehensive guide and reference in `docs/code-review-and-quality.md`.

## [0.7.0] - 2026-11-22

### Added - Sprint 6: Debugging and Automatic Repair Engine
- **Failure Classification Taxonomy**: Deterministic classifier categorizing verification failures (`syntax_error`, `import_error`, `type_error`, `lint_error`, `test_failure`, `dependency_error`, `timeout`, `resource_exhaustion`, `patch_application_error`, `environment_error`, `unknown`) with auto-repair boundaries (`backend/app/debugging/failure_classifier.py`).
- **Diagnostic Evidence Collector**: Bounded, sanitized extraction of test failure lines, traceback frames, implicated files/symbols, with secret redaction and byte limits (`backend/app/debugging/evidence_collector.py`).
- **Root Cause Analyzer**: Defect attribution engine diagnosing primary/alternative causes, linking requirements/acceptance criteria, and identifying scope violations (`backend/app/debugging/root_cause_analyzer.py`).
- **Repair Strategy Engine**: Bounded repair strategy selector with architectural risk rating (`LOW`, `MEDIUM`, `HIGH`, `CRITICAL`), target file/symbol constraints, and human approval gates (`backend/app/debugging/repair_strategy.py`).
- **Bounded Repair Proposal Engine**: Proposal generation reusing Sprint 4 code generation and patch validation, preventing out-of-scope modifications (`backend/app/debugging/repair_engine.py`).
- **Isolated Repair Evaluator**: Outcome evaluator comparing pre-repair and post-repair execution reports, computing verdicts (`FIXED`, `PARTIALLY_FIXED`, `NOT_FIXED`, `REGRESSION_DETECTED`, `INCONCLUSIVE`), and detecting regressions (`backend/app/debugging/evaluation.py`).
- **Bounded Repair Controller & State Machine**: Controller enforcing attempt limits (default 3, hard ceiling 5), SHA-256 patch hashing loop detection, environment defect safeguards, and state transitions via explicit `RepairStateMachine` (`backend/app/debugging/repair_controller.py`, `backend/app/debugging/state_machine.py`).
- **Debugging Service & REST APIs**: Session management and query endpoints: `POST /api/v1/debugging/repair`, `GET /api/v1/debugging/repairs`, `GET /api/v1/debugging/repair/{repair_id}`, and `GET /api/v1/debugging/repair/{repair_id}/history` (`backend/app/debugging/service.py`, `backend/app/api/routes/debugging.py`).
- **Unit, Integration, & Pipeline Test Suites**: Comprehensive test suite with 331 total passing tests and immutability verification (`tests/unit/test_debugging_*.py`, `tests/unit/test_failure_classifier.py`, `tests/unit/test_evidence_collector.py`, `tests/unit/test_root_cause_analyzer.py`, `tests/unit/test_repair_strategy.py`, `tests/unit/test_repair_engine.py`, `tests/unit/test_repair_evaluation.py`, `tests/unit/test_repair_controller.py`, `tests/integration/test_debugging_pipeline.py`).
- **Documentation**: Comprehensive specification in `docs/debugging-and-repair.md`.

## [0.6.0] - 2026-11-21

### Added - Sprint 5: Execution & Verification Sandbox
- **Execution Domain Models**: Strongly typed Pydantic models for `ExecutionStatus`, `ExecutionPolicy`, `ExecutionRequest`, `CommandResult`, `TestSummary`, and `VerificationReport` (`backend/app/execution/models.py`).
- **Policy & Boundary Enforcement**: Execution policy validator enforcing command whitelisting (`pytest`, `python -m pytest`, `ruff check`), command argument injection rejection, strict resource upper bounds (CPU ≤ 2.0 cores, memory ≤ 1024MB, timeout ≤ 120s), and workspace quotas (`backend/app/execution/policy.py`).
- **Isolated Workspace Manager**: Ephemeral workspace manager with exclusion filtering for sensitive files (`.git`, `.venv`, `.env`, keys, tokens, bytecode), disk quota enforcement (≤ 50MB), and clean context manager teardown (`backend/app/execution/workspace.py`).
- **Secure Patch Applier**: Patch application engine supporting both `GeneratedFileChange` sequences and unified diffs with path traversal blocking, protected path defenses, and baseline SHA-256 hash checks (`backend/app/execution/patch_applier.py`).
- **Restricted Docker Runner**: Container runner enforcing unprivileged user execution (`10001:10001`), dropping all Linux capabilities (`--cap-drop=ALL`), read-only rootfs (`--read-only`), ephemeral tmpfs scratch space, disabled network (`--network=none`), output caps (`MAX_OUTPUT_BYTES = 500KB`), and strict No-Host-Fallback (`backend/app/execution/docker_runner.py`).
- **Test Executor & Verification Engine**: Pytest and Ruff output parsers with structured test metric synthesis, exit code tracking, and auditable verification report generation persisted to `agentforge-artifacts/execution/` (`backend/app/execution/test_executor.py`, `backend/app/execution/verification.py`).
- **Execution Service & REST APIs**: High-level execution orchestrator exposing `POST /api/v1/execution/create`, `GET /api/v1/execution/reports`, and `GET /api/v1/execution/reports/{execution_id}` (`backend/app/execution/service.py`, `backend/app/api/routes/execution.py`).
- **Unit, Integration, & Security Test Suites**: Comprehensive test coverage spanning 214 total repository tests, with dedicated security verification for no-host fallback, traversal defense, protected files, and repository immutability (`tests/unit/test_execution_*.py`, `tests/unit/test_workspace.py`, `tests/unit/test_patch_applier.py`, `tests/unit/test_docker_runner.py`, `tests/unit/test_test_executor.py`, `tests/unit/test_verification.py`, `tests/integration/test_execution_*.py`).
- **Documentation**: Execution sandbox technical specification in `docs/execution-sandbox.md`.

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
