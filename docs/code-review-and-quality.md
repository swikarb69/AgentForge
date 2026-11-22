# Code Review and Quality Assurance Engine

## 1. Overview

The **Code Review & Quality Assurance Engine** is Sprint 7 of AgentForge. It provides deterministic, evidence-grounded static code review, security vulnerability scanning, architectural boundary enforcement, and regression detection for AI-generated and repaired code before shipping.

```text
Generated Patch / Repository Workspace
                 ↓
      Review Request & Scope
                 ↓
      Deterministic Static Analyzer (AST & Quality Rules)
                 ↓
      Security Analyzer (Vulnerabilities & Secret Detection)
                 ↓
      Architecture Analyzer (Circular Imports, Layers, Coupling)
                 ↓
      Regression Analyzer (Plan Compliance, ACs, Test Quality)
                 ↓
      Evidence Collector (Bounded Snippets, Secret Redaction, SHA-256)
                 ↓
      Review Aggregator & Scorer (0-100 Quality Score, Severity Deductions)
                 ↓
      Gating State Machine (APPROVED / REQUIRES_CHANGES / BLOCKED)
                 ↓
      Auditable Review Report (JSON & Markdown)
```

---

## 2. Core Architectural Principles & Security

1. **Deterministic-First**:
   The engine operates entirely offline without requiring external LLM API keys or services. All 24 rules execute deterministic AST traversals, regular expressions, and static graph evaluations.
2. **Strict Repository Read-Only Immutability**:
   Review operations never modify, delete, or write files in the examined repository. Code changes from patches or diffs are applied in-memory.
3. **Evidence-Grounded Findings**:
   Every reported finding links directly to a bounded code snippet ($\le 15$ lines), exact 1-indexed line numbers, a SHA-256 fingerprint, and actionable remediation guidance.
4. **Active Secret Sanitization**:
   All collected code snippets pass through active redaction filters ensuring API keys (AWS, GitHub, Slack, Generic Bearer tokens) are never leaked in review reports or audit trails.
5. **Multi-Artifact Traceability**:
   The review engine seamlessly links upstream artifacts: Sprint 2 Issue Context, Sprint 3 Implementation Plans, Sprint 4 Generations, Sprint 5 Execution Reports, and Sprint 6 Repair Histories.

---

## 3. Built-In Rule Catalog

The rule catalog comprises 24 rules across 7 categories:

| Rule ID | Category | Default Severity | Description | Requires Human Review |
| :--- | :--- | :--- | :--- | :--- |
| `CORR-001` | Correctness | `high` | Undefined variable, function, or symbol usage | No |
| `CORR-002` | Correctness | `high` | Unreachable code detected following return/raise/break | No |
| `CORR-003` | Correctness | `medium` | Mutable default argument (list/dict/set) in function definition | No |
| `CORR-004` | Correctness | `medium` | Suspicious identity comparison (`is` / `is not`) on literal | No |
| `SEC-001` | Security | `critical` | SQL injection via unparameterized string formatting | Yes |
| `SEC-002` | Security | `critical` | Command/shell injection via `os.system` or `subprocess(shell=True)` | Yes |
| `SEC-003` | Security | `critical` | Dynamic code execution via `eval()` or `exec()` | Yes |
| `SEC-004` | Security | `high` | Hardcoded API keys, bearer tokens, or private secrets | Yes |
| `SEC-005` | Security | `high` | Path traversal vulnerability via unsanitized file paths | Yes |
| `SEC-006` | Security | `high` | Insecure deserialization via `pickle.loads` or `yaml.load` | Yes |
| `SEC-007` | Security | `medium` | Insecure temporary file creation in shared directories | No |
| `ARCH-001` | Architecture | `high` | Circular import dependency cycle detected between modules | No |
| `ARCH-002` | Architecture | `high` | Layer boundary violation (lower-layer importing upper-layer) | No |
| `ARCH-003` | Architecture | `medium` | Unapproved external third-party package dependency | No |
| `ARCH-004` | Architecture | `low` | Excessive module coupling (> 15 internal imports) | No |
| `REG-001` | Regression | `high` | Out-of-scope file modified relative to Implementation Plan | Yes |
| `REG-002` | Regression | `high` | Missing planned target file or unmet acceptance criteria | Yes |
| `REG-003` | Regression | `critical` | Unresolved test or container failures from Verification Report | Yes |
| `REG-004` | Regression | `high` | Regression detected from unsuccessful automated repair run | Yes |
| `TEST-001` | Test Compliance | `medium` | Test function contains no assertion or expectation checks | No |
| `TEST-002` | Test Compliance | `low` | Test marked skipped or disabled without justification | No |
| `MAINT-001`| Maintainability | `low` | Missing module, class, or public function docstring | No |
| `MAINT-002`| Maintainability | `medium` | Deeply nested control flow (depth > 4) | No |
| `MAINT-003`| Maintainability | `medium` | High cyclomatic complexity (McCabe complexity > 10) | No |
| `MAINT-004`| Maintainability | `low` | Function signature has excessive parameters (> 6) | No |
| `MAINT-005`| Maintainability | `medium` | Global variable reassignment or modification inside function | No |
| `REL-001` | Reliability | `medium` | Bare `except:` or empty `except Exception: pass` handler | No |
| `REL-002` | Reliability | `medium` | Unhandled file/socket resource cleanup without `with` context | No |
| `PERF-001` | Performance | `medium` | Inefficient quadratic string concatenation loop | No |
| `PERF-002` | Performance | `medium` | Repeated regex compilation (`re.compile`) inside loop body | No |

---

## 4. The Four Analyzers

### 4.1 Deterministic Static Analyzer
- Uses Python `ast` parse trees to inspect syntax, semantics, and code structure.
- Emits findings for correctness (`CORR-001`–`004`), maintainability (`MAINT-001`–`005`), reliability (`REL-001`–`002`), and performance (`PERF-001`–`002`).
- Gracefully handles syntax errors by reporting them cleanly under `CORR-001` rather than crashing the review pipeline.

### 4.2 Security Analyzer
- Tracks tainted user-input variables across call hierarchies to detect SQL injection (`SEC-001`).
- Identifies unsafe shell execution (`SEC-002`), arbitrary code evaluation (`SEC-003`), and unvalidated path concatenation (`SEC-005`).
- Combines regex scanners and AST string literal checks to detect hardcoded secrets (`SEC-004`) while redacting them in memory.
- Flags insecure deserialization (`SEC-006`) and shared temporary directory usage (`SEC-007`).

### 4.3 Architecture Analyzer
- Builds directed module dependency graphs to discover circular imports (`ARCH-001`) via Depth-First Search (DFS) cycle detection.
- Enforces strict unidirectional layer boundaries (`ARCH-002`):
  `core` $\rightarrow$ `models` $\rightarrow$ `engine` $\rightarrow$ `services` $\rightarrow$ `api`.
- Validates external package imports against `pyproject.toml` dependencies and standard library modules (`ARCH-003`).
- Detects tight coupling (`ARCH-004`) when a module imports more than 15 unique internal modules.

### 4.4 Regression Analyzer
- Compares changed files against authorized paths in the Sprint 3 `ImplementationPlan` (`REG-001`).
- Confirms all planned target files and acceptance criteria are implemented (`REG-002`).
- Verifies that sandbox test failures from the Sprint 5 `VerificationReport` are addressed (`REG-003`).
- Audits test suite quality to ensure tests include genuine assertions (`TEST-001`) and are not silently skipped (`TEST-002`).

---

## 5. Quality Scoring & Gating State Machine

### 5.1 Explainable Quality Score Formula
The engine computes a deterministic 0–100 quality score:

$$\text{Score} = \max\left(0.0, 100.0 - (25.0 \cdot C + 10.0 \cdot H + 3.0 \cdot M + 1.0 \cdot L)\right)$$

Where:
- $C$ = Critical severity findings count ($-25$ pts each)
- $H$ = High severity findings count ($-10$ pts each)
- $M$ = Medium severity findings count ($-3$ pts each)
- $L$ = Low severity findings count ($-1$ pt each)

### 5.2 Gating State Machine
The overall review verdict is evaluated against deterministic safety thresholds:

```text
[Findings Evaluation]
       │
       ├─ If Critical > 0 (or Score < 60) ──────────→ BLOCKED
       │
       ├─ Else If High > 0 (or Score < 80) ─────────→ REQUIRES_CHANGES
       │
       ├─ Else If Medium > 0 or Low > 0 (Score < 95) → APPROVED_WITH_COMMENTS
       │
       └─ Else (0 findings, Score ≥ 95) ────────────→ APPROVED
```

Human review is flagged (`requires_human_review = True`) whenever any Critical finding, High finding with `requires_human_review=True`, or plan scope violation occurs.

---

## 6. REST API Reference

Prefix: `/api/v1/reviews`

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `POST` | `/api/v1/reviews/create` | Execute full code review and return structured `ReviewReport` |
| `GET` | `/api/v1/reviews` | List all stored review session reports |
| `GET` | `/api/v1/reviews/{review_id}` | Retrieve review report by ID |
| `GET` | `/api/v1/reviews/{review_id}/findings` | Retrieve findings with optional `?severity=` and `?category=` filters |
| `GET` | `/api/v1/reviews/{review_id}/report` | Retrieve formatted report (`?format=json` or `?format=markdown`) |
| `GET` | `/api/v1/reviews/rules` | List registered rules with optional `?category=` and `?severity=` filters |

---

## 7. Example Usage

### Executing a Review via REST API
```bash
curl -X POST "http://localhost:8000/api/v1/reviews/create" \
  -H "Content-Type: application/json" \
  -d '{
    "review_id": "REV-2026-001",
    "repository_path": "backend/app",
    "review_scope": "patch_only",
    "patch_content": "diff --git a/app/main.py b/app/main.py\n..."
  }'
```

### Retrieving Markdown Review Report
```bash
curl "http://localhost:8000/api/v1/reviews/REV-2026-001/report?format=markdown"
```
