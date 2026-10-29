# AgentForge — Planning Engine (Sprint 3)

The **Planning Engine** is the third major cognitive layer of AgentForge. It takes the structured `IssueContext` produced by Sprint 2 (Issue Intelligence) and transforms it into a validated, machine-readable, deterministic `ImplementationPlan` that downstream coding agents can consume without ambiguity.

---

## 1. High-Level Planning Pipeline

```text
GitHub / Local Software Issue
             ↓
Issue Intelligence (Sprint 2)
  ├── Parser (`IssueParser`)
  ├── Requirement Extractor (`RequirementExtractor`)
  ├── Reference Extractor (`ReferenceExtractor`)
  └── Matcher (`IssueMatcher`)
             ↓
        IssueContext
             ↓
   Planning Engine Subsystem (Sprint 3)
  ├── 1. Impact Analysis (`ImpactAnalyzer`)
  │      └── Direct/Indirect Files, Symbols, Test Discovery
  ├── 2. Task Generation & Traceability (`TaskGenerator`)
  │      └── Requirement → Task, Acceptance Criteria → Verification
  ├── 3. Dependency Analysis & Cycle Detection (`DependencyAnalyzer`)
  │      └── Deterministic Prerequisites, Graph Topological Ordering
  ├── 4. Risk & Assumption Extraction (`RiskAnalyzer`)
  │      └── Security, API Compatibility, Blast Radius, DB Persistence
  └── 5. Deterministic Plan Validation (`PlanValidator`)
         └── Structural, Dependency, Traceability, and Repo Bounds
             ↓
     ImplementationPlan (Ready / Needs Clarification / Blocked)
             ↓
     Future Coder Agent (Sprint 4)
```

---

## 2. Core Abstractions & Cognitive Stages

AgentForge maintains strict boundaries between three distinct conceptual artifacts:

| Artifact | Responsibility & Scope |
| :--- | :--- |
| **`Issue`** | Raw external problem statement (headline, description, source) without codebase awareness. |
| **`IssueContext`** | Issue semantics mapped to codebase intelligence (AST symbols, file matches, relevance scores, and unresolved references). |
| **`ImplementationPlan`** | Actionable engineering blueprint specifying *what* to change, *where* to change, *task sequence*, *test verification*, *risks*, and *assumptions*. |

---

## 3. Key Components & Implementation

### 1. Data Models (`backend/app/planning/models.py`)
- **`ImplementationPlan`**: Complete plan containing objective, tasks, file changes, test plan, dependencies, risks, assumptions, and validation report.
- **`PlanTask`**: Actionable engineering task specifying target files, target symbols, change type, dependencies, verification, and requirement traceability.
- **`FileChange` & `SymbolChange`**: Granular file- and symbol-level modifications (`modify`, `create`, `delete`, `test`).
- **`TestPlan` & `VerificationCase`**: Test modifications, additions, and concrete expected behavior assertions linked to acceptance criteria.
- **`Dependency`**: Explicit directed edges between tasks with topological ordering and cycle detection.
- **`Risk`**: Grounded engineering risks with severity ratings (`low`, `medium`, `high`, `critical`) and actionable mitigations.
- **`Assumption`**: Grounded engineering assumptions.
- **`PlanValidation`**: Comprehensive validation results detailing `valid: bool`, `status: PlanStatus`, blocking errors, and non-blocking warnings.

### 2. Impact Analyzer (`backend/app/planning/impact.py`)
Evaluates `IssueContext` against repository files and symbols:
- Differentiates direct impact (explicitly referenced or top-ranked implementation files/symbols) vs. indirect impact (test suites, dependent modules).
- Automatically discovers existing test suites for modified files or plans new test files (`tests/test_<module>.py`).
- Detects requests to create or delete files.

### 3. Task Generator & Traceability (`backend/app/planning/tasks.py`)
Generates concrete, actionable implementation steps:
- Maps each functional/non-functional requirement to implementation tasks (`addressed_requirements`).
- Derives verification cases from explicit acceptance criteria (`addressed_acceptance_criteria`).
- Orders tasks deterministically: foundation creation → core implementation → test suite extension.

### 4. Dependency Analyzer (`backend/app/planning/dependencies.py`)
- Establishes deterministic prerequisite dependencies between tasks.
- Detects circular dependencies using depth-first cycle search.
- Computes topological task order.

### 5. Risk & Assumption Analyzer (`backend/app/planning/risks.py`)
Detects real engineering risks supported by codebase evidence:
- **Security / Authentication**: Token, JWT, password, or credential modifications.
- **API Compatibility**: Breaking changes to HTTP API routes or response schemas.
- **Database / Schema**: Persistence or migration modifications.
- **Missing Baseline Tests**: Components lacking pre-existing unit test coverage.
- **Unresolved References**: Issue mentions non-existent symbols or paths.
- **Ambiguity**: Underspecified requirements.
- **Multi-File Blast Radius**: Changes spanning multiple repository files.

### 6. Plan Validator (`backend/app/planning/validator.py`)
Enforces strict quality and safety invariants:
- **Structural**: Non-empty tasks, unique task IDs, sequential 1-based order, valid targets.
- **Traceability**: Every requirement addressed by a task, every acceptance criterion mapped to verification.
- **Dependencies**: No circular dependencies, non-existent dependency IDs, or self-referential edges.
- **Repository Bounds**: Targeted files exist in the repository (unless marked `create`).
- **Readiness Classification**:
  - `ready`: Full information available to proceed with implementation.
  - `needs_clarification`: Ambiguity exists but partial planning is possible.
  - `blocked`: Critical dependencies or entity references are unresolved, or cycle detected.

### 7. Service & API (`backend/app/planning/service.py`, `backend/app/api/routes/planning.py`)
- `POST /api/v1/planning/create`: Generates an `ImplementationPlan` from issue text and repository path.
- `POST /api/v1/planning/validate`: Validates an existing `ImplementationPlan` payload.

---

## 4. Current Constraints & Non-Goals

> [!IMPORTANT]
> **Sprint 3 is strictly deterministic and read-only.**
> - **No LLMs**: No external OpenAI/Gemini APIs, vector databases, or generative heuristics.
> - **No Repository Writes**: The planner analyzes the codebase in read-only mode and never modifies source files, commits, or branches.
> - **No Code Generation**: Generating patches or executing test code is deferred to Sprint 4 (Coder Agent) and Sprint 5 (Sandbox Execution).
