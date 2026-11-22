# Debugging and Automatic Repair Engine

## 1. Overview

The **Debugging and Automatic Repair Engine** is Sprint 6 of AgentForge. It transforms sandboxed verification failures into bounded, classified, diagnosed, and validated code repairs without human intervention for standard defects, while strictly halting or pausing when encountering environment defects or high-risk architectural changes.

```text
Sprint 5 Verification Report (Failures)
         ↓
Failure Classifier (Deterministic Regex Taxonomy)
         ↓
Diagnostic Evidence Collector (Sanitized & Bounded)
         ↓
Root Cause Analyzer (Defect Attribution)
         ↓
Repair Strategy Engine (Risk & Bounds Assessment)
         ↓
Repair Proposal Engine (Sprint 4 Patch Generation & Validation)
         ↓
Docker Sandbox Execution (Ephemeral Workspace Verification)
         ↓
Repair Evaluator (Pre vs. Post Outcome Comparison)
         ↓
State Machine (SUCCEEDED / MAX_ATTEMPTS_REACHED / NEEDS_HUMAN_REVIEW)
```

---

## 2. Architectural Boundaries & Security

AgentForge adheres to five non-negotiable architectural constraints during automated repair:

1. **Target Repository Immutability**:
   The original target repository is never modified directly. All patch applications and test executions occur inside ephemeral workspace copies that are destroyed after verification.
2. **Strict No-Host-Fallback**:
   Repairs are executed exclusively inside isolated Docker containers. If the Docker CLI or daemon is unavailable, the engine immediately halts with `NEEDS_HUMAN_REVIEW` rather than falling back to host execution.
3. **Bounded Repair Loops**:
   The repair controller enforces a server-side hard ceiling of 5 iterations (default: 3). If repairs do not converge within this limit, execution transitions to `MAX_ATTEMPTS_REACHED`.
4. **Loop & Oscillation Detection**:
   The engine computes a SHA-256 fingerprint of every proposed patch diff. If a patch identical to an earlier attempt is proposed, an infinite loop is detected and execution transitions to `FAILED`.
5. **High-Risk Human Approval Gate**:
   Strategies impacting multiple modules or incurring `HIGH` or `CRITICAL` risk pause in `AWAITING_APPROVAL` when `require_approval_for_high_risk` is enabled.
6. **No Autonomous Commits/Pushes**:
   Sprint 6 strictly produces reviewable repair proposals and evaluation reports. Automated Git commits, pushes, and PR creation are reserved for subsequent shipping sprints.

---

## 3. Failure Classification Taxonomy

Failures captured from Docker sandbox test executions are deterministically classified into:

| Category | Description | Auto-Repair Permitted |
| :--- | :--- | :--- |
| `syntax_error` | Python indentation, syntax, or parse errors | Yes |
| `import_error` | Missing symbol or module imports | Yes |
| `type_error` | Incompatible argument, return, or variable types | Yes |
| `lint_error` | Style and quality rule violations (Ruff/Flake8) | Yes |
| `test_failure` | Unit test assertion failures | Yes |
| `dependency_error` | Missing third-party packages in environment | No (Halt) |
| `timeout` | Container execution exceeded timeout policy | No (Halt) |
| `resource_exhaustion` | Out of Memory (OOM) or CPU limit exceeded | No (Halt) |
| `patch_application_error` | Patch conflicts or corrupted diff | No (Halt) |
| `environment_error` | Docker daemon unavailable or missing | No (Halt) |
| `unknown` | Unrecognized non-zero exit code | No (Halt) |

---

## 4. State Machine Lifecycle

The repair lifecycle is governed by an explicit `RepairStateMachine`:

```text
                  ┌───────────────┐
                  │    PENDING    │
                  └───────┬───────┘
                          │
                          ▼
                  ┌───────────────┐
       ┌─────────►│   ANALYZING   ├──────────────┐
       │          └───────┬───────┘              │
       │                  │                      ▼
       │                  ▼              ┌───────────────┐
       │          ┌───────────────┐      │     FAILED    │
       │          │   DIAGNOSING  │      └───────────────┘
       │          └───────┬───────┘              ▲
       │                  │                      │
       │         ┌────────┴────────┐             │
       │         │                 ▼             │
       │         │       ┌──────────────────┐    │
       │         │       │AWAITING_APPROVAL │    │
       │         │       └────────┬─────────┘    │
       │         ▼                │              │
       │  ┌───────────────┐       │              │
       │  │GENERATING_REP.│◄──────┘              │
       │  └───────┬───────┘                      │
       │          │                              │
       │          ▼                              │
       │  ┌───────────────┐                      │
       │  │VALIDATING_REP.├──────────────────────┤
       │  └───────┬───────┘                      │
       │          │                              │
       │          ▼                              │
       │  ┌───────────────┐                      │
       │  │EXECUTING_REP. ├──────────────────────┘
       │  └───────┬───────┘
       │          │
       │          ▼
       │  ┌───────────────┐
       └──┤   EVALUATING  ├───────► ┌───────────┐
          └───────┬───────┘         │ SUCCEEDED │
                  │                 └───────────┘
                  ▼
          ┌──────────────────────┐
          │ MAX_ATTEMPTS_REACHED │
          └──────────────────────┘
```

---

## 5. API Reference

All debugging and automated repair routes are hosted under `/api/v1/debugging`:

### `POST /api/v1/debugging/repair`
Initiates a bounded automated repair session for a previous Sprint 5 execution run.

**Request Payload (`RepairRequest`):**
```json
{
  "repository_path": "/path/to/repo",
  "execution_id": "exec-1234567890ab",
  "generation_id": "GEN-ABCDEF12",
  "plan_id": "PLAN-ISSUE-1",
  "config": {
    "max_attempts": 3,
    "require_approval_for_high_risk": true,
    "auto_execute_repairs": true,
    "max_evidence_bytes": 100000,
    "max_traceback_lines": 50
  }
}
```

**Response (`RepairResult`):**
```json
{
  "repair_id": "repair-abcdef123456",
  "repository_path": "/path/to/repo",
  "initial_execution_id": "exec-1234567890ab",
  "current_state": "succeeded",
  "total_attempts": 1,
  "final_verdict": "fixed",
  "final_patch": "--- a/app.py\n+++ b/app.py\n...",
  "human_intervention_required": false,
  "attempts": [...]
}
```

### `GET /api/v1/debugging/repairs`
Lists all active and completed repair sessions.

### `GET /api/v1/debugging/repair/{repair_id}`
Retrieves complete details and audit state for a specific repair session.

### `GET /api/v1/debugging/repair/{repair_id}/history`
Retrieves chronological attempt history, including state transitions and evaluations.
