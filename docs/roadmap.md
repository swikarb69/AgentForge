# AgentForge Product & Engineering Roadmap

This roadmap details the planned engineering sprints and milestones for **AgentForge**.

---

## Sprint Overview

```text
[Sprint 0] Foundation (COMPLETED)
    │
    ▼
[Sprint 1] Repository Intelligence (COMPLETED)
    │
    ▼
[Sprint 2] Issue Intelligence (COMPLETED)
    │
    ▼
[Sprint 3] Implementation Planning Agent (COMPLETED)
    │
    ▼
[Sprint 4] Code Generation Engine (COMPLETED)
    │
    ▼
[Sprint 5] Execution Sandbox Engine (COMPLETED)
    │
    ▼
[Sprint 6] Debugging and Automatic Repair Engine (COMPLETED)
    │
    ▼
[Sprint 7] Security & Senior Peer Code Review Engine (NEXT)
    │
    ▼
[Sprint 8] GitHub Integration & PR Shipping Agent
    │
    ▼
[Sprint 9] Benchmark Evaluation Suite & Token/Cost Analytics
    │
    ▼
[Sprint 10] Developer UI Dashboard & Final Polish
```

---

## Detailed Sprint Specifications

### Sprint 0 — Foundation
- **Status**: COMPLETED
- **Deliverables**: Repository structure, FastAPI application entrypoint, Pydantic settings config, Dockerfile baseline, baseline security policy JSON, Pytest unit/integration test baseline, GitHub Actions CI workflow (`ci.yml`), MIT License, documentation suite.

### Sprint 1 — Repository Intelligence
- **Status**: **CURRENT / COMPLETE**
- **Deliverables**: Repository Scanner, Ignore Rule Filters, Language Detection, Test File Classifier, Python AST Parser, Symbol Extractor, Structural Code Chunker, In-Memory Repository Index, Deterministic Search Engine, Repositories API (`/api/v1/repositories/analyze`, `/api/v1/repositories/search`), integration test suite.

### Sprint 2 — Issue Intelligence
- **Status**: **COMPLETED**
- **Deliverables**: Issue Parser, Requirement Extractor, Acceptance Criteria Extractor, Reference & Endpoint Extractor, Repository-Aware Matcher, Issue Context Builder Service, Issues REST API (`/api/v1/issues/analyze`, `/api/v1/issues/context`), Sample Fixture Issues, Integration Test Suite.

### Sprint 3 — Planning Engine
- **Status**: **COMPLETED**
- **Deliverables**: Domain models (`ImplementationPlan`, `PlanTask`, `FileChange`, `SymbolChange`, `TestPlan`, `Dependency`, `Risk`, `Assumption`, `PlanValidation`), Change Impact Analyzer, Task Generator with requirement traceability, Dependency Analyzer with cycle detection, Risk Analyzer, Deterministic Plan Validator, Planning Service Pipeline, REST APIs (`/api/v1/planning/create`, `/api/v1/planning/validate`), Planning Fixtures, Integration Test Suite, Comprehensive Documentation.

### Sprint 4 — Code Generation Engine
- **Status**: **COMPLETED**
- **Deliverables**: Domain models (`GeneratedFileChange`, `PatchValidation`, `GenerationRequest`, `GenerationResult`, `CreateGenerationRequest`), Bounded Source Context Builder (`SourceContextBuilder`), Pluggable Provider Interface (`CodeGenerationProvider`, `MockCodeGenerationProvider`, `GeminiCodeGenerationProvider`), Versioned Prompt Engineering (`PROMPT_VERSION = v1.0`), Structured Code Generator (`CodeGenerator`), Patch Validator (`PatchValidator`), Patch & Diff Builder (`PatchBuilder`), Generation Service (`GenerationService`), REST APIs (`/api/v1/generation/create`, `/api/v1/generation/{id}`, `/api/v1/generation/{id}/patch`), Integration Test Suite with Repository Immutability, Comprehensive Documentation.

### Sprint 5 — Execution Sandbox Engine
- **Status**: **COMPLETED**
- **Deliverables**: Execution Domain Models (`ExecutionStatus`, `ExecutionPolicy`, `ExecutionRequest`, `CommandResult`, `TestSummary`, `VerificationReport`), Security Policy Validator (`backend/app/execution/policy.py`), Isolated Ephemeral Workspace Manager with sensitive file filtering and quotas (`backend/app/execution/workspace.py`), Secure Patch Applier with traversal/protected path defenses and hash checks (`backend/app/execution/patch_applier.py`), Restricted Docker Runner with unprivileged non-root user `10001:10001`, dropped capabilities `ALL`, read-only rootfs, tmpfs, network isolation `none`, and strict No-Host-Fallback (`backend/app/execution/docker_runner.py`), Pytest and Ruff Test Executor (`backend/app/execution/test_executor.py`), Verification Engine & Report Persistence (`backend/app/execution/verification.py`), Execution Service Pipeline (`backend/app/execution/service.py`), REST APIs (`/api/v1/execution/create`, `/api/v1/execution/reports`, `/api/v1/execution/reports/{id}`), Unit, Integration & Security Test Suites, and Comprehensive Documentation (`docs/execution-sandbox.md`).

### Sprint 6 — Debugging and Automatic Repair Engine
- **Status**: **COMPLETED**
- **Deliverables**: Failure classification taxonomy (`FailureClassifier`), diagnostic evidence collector with secret sanitization and byte limits (`DiagnosticEvidenceCollector`), deterministic root cause analyzer (`RootCauseAnalyzer`), repair strategy engine with risk assessment (`RepairStrategyEngine`), bounded repair proposal engine reusing Sprint 4 code generation and patch validation (`RepairProposalEngine`), isolated repair evaluator (`RepairEvaluator`), bounded repair controller with loop detection and attempt caps (`BoundedRepairController`), explicit state machine (`RepairStateMachine`), debugging service (`DebuggingService`), REST APIs (`/api/v1/debugging/repair`, `/api/v1/debugging/repairs`, `/api/v1/debugging/repair/{id}`, `/api/v1/debugging/repair/{id}/history`), unit and integration test suites with repository immutability verification, comprehensive documentation (`docs/debugging-and-repair.md`).

### Sprint 7 — Security & Senior Peer Code Review Engine
- **Status**: PLANNED
- **Deliverables**: Deterministic secret scanner, prompt injection sanitizer, LLM senior code reviewer agent, human approval checkpoints.

### Sprint 8 — GitHub Integration & PR Shipping Agent
- **Status**: PLANNED
- **Deliverables**: Git branch creator, commit synthesizer, pull request creator with auditable run reports and test evidence.

### Sprint 9 — Benchmark Evaluation Suite & Token/Cost Analytics
- **Status**: PLANNED
- **Deliverables**: Benchmark task suite, latency metric tracker, token counter, financial cost estimation engine.

### Sprint 10 — Developer UI Dashboard & Final Polish
- **Status**: PLANNED
- **Deliverables**: Developer UI dashboard, real-time agent state timeline, live logs view, end-to-end user testing, v1.0.0 release.
