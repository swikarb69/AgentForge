# AgentForge — End-to-End Agent Orchestration Engine

## 1. Overview and Vision

The **AgentForge Orchestration Engine** serves as the central control plane connecting all autonomous software engineering capabilities developed across Sprints 0 through 8 into a single, observable, fault-tolerant workflow pipeline.

```
       ┌────────────────────────┐
       │   Client / Developer   │
       └───────────┬────────────┘
                   │  POST /api/v1/workflows
                   ▼
┌──────────────────────────────────────────────────────────────┐
│                    Orchestration Service                     │
│  ┌────────────────────┐            ┌──────────────────────┐  │
│  │   Workflow Store   │            │   WorkflowExecutor   │  │
│  │  (Idempotency &    │            │ (Sequential DAG loop,│  │
│  │   Optimistic Locks)│            │  retries, timeouts)  │  │
│  └─────────┬──────────┘            └──────────┬───────────┘  │
│            │                                  │              │
│  ┌─────────▼──────────┐            ┌──────────▼───────────┐  │
│  │  Recovery Manager  │            │    Event Bus & Pub   │  │
│  │  (Pause/Resume &   │            │  (Redacted Telemetry │  │
│  │ Artifact Integrity)│            │    & Audit Events)   │  │
│  └────────────────────┘            └──────────────────────┘  │
└──────────────────────────────┬───────────────────────────────┘
                               │
                ┌──────────────┴──────────────┐
                ▼                             ▼
   ┌───────────────────────────┐ ┌───────────────────────────┐
   │     Stages 1–7 (Auto)     │ │   Stages 8–9 (Gated)      │
   │ 1. Repo Intelligence      │ │ 8. Human Approval Gate    │
   │ 2. Issue Analysis         │ │    (Explicit human signoff│
   │ 3. Planning Engine        │ │     mandatory)            │
   │ 4. Code Generation        │ │ 9. Controlled Release     │
   │ 5. Execution Sandbox      │ │    (RC branch + PR only;  │
   │ 6. Automatic Repair (Opt) │ │     ZERO auto-merge)      │
   │ 7. Code Review Gate       │ │                           │
   └───────────────────────────┘ └───────────────────────────┘
```

The orchestration engine guarantees that AgentForge operates with deterministic execution boundaries, never bypassing safety checks, isolated sandbox execution, peer code review, or human authorization.

---

## 2. State Machine Architecture

### Workflow Lifecycle (`WorkflowStatus`)

The workflow level state machine enforces monotonic transitions between active and terminal states:

```
          ┌─────────┐
          │ CREATED │
          └────┬────┘
               │
               ▼
          ┌─────────┐
     ┌───►│ RUNNING │◄────────┐
     │    └────┬────┘         │
     │         ├──────────────┼──────────────┐
     │         ▼              ▼              ▼
     │   ┌───────────┐  ┌───────────┐  ┌───────────┐
     │   │ WAITING_  │  │  PAUSED   │  │ RETRYING  │
     │   │ APPROVAL  │  └─────┬─────┘  └─────┬─────┘
     │   └─────┬─────┘        │              │
     │         │              │              │
     └─────────┴──────────────┴──────────────┘
               │
               ├──────────────────┬──────────────────┐
               ▼                  ▼                  ▼
        ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
        │  COMPLETED  │    │   FAILED    │    │  CANCELLED  │
        │  (Terminal) │    │  (Terminal) │    │  (Terminal) │
        └─────────────┘    └─────────────┘    └─────────────┘
```

- **`CREATED`**: Initial workflow submission prior to queueing.
- **`QUEUED`**: Workflow accepted and scheduled for dispatch.
- **`RUNNING`**: Active pipeline loop executing stages.
- **`WAITING_FOR_APPROVAL`**: Halted at the human approval gate. Requires explicit operator authorization to resume.
- **`PAUSED`**: Explicitly paused by operator or policy.
- **`RETRYING`**: Transient failure backoff before re-attempting a stage.
- **`COMPLETED`**: All pipeline stages concluded successfully (Terminal).
- **`FAILED`**: Unrecoverable stage error, timeout, or policy violation (Terminal).
- **`CANCELLED`**: Execution cancelled by operator or administrator (Terminal).
- **`BLOCKED`**: Safety rule or policy constraint blocked progression (Terminal).

### Stage Lifecycle (`StageStatus`)

Individual stages advance through explicit deterministic transitions:

```
     ┌─────────┐
     │ PENDING │
     └────┬────┘
          ├─────────────────┐
          ▼                 ▼
     ┌─────────┐       ┌─────────┐
     │ RUNNING │       │ SKIPPED │ (Terminal)
     └────┬────┘       └─────────┘
          ├─────────────────┬─────────────────┬─────────────────┐
          ▼                 ▼                 ▼                 ▼
     ┌───────────┐     ┌─────────┐       ┌─────────┐       ┌───────────┐
     │ COMPLETED │     │ FAILED  │       │ WAITING │       │ CANCELLED │
     │ (Terminal)│     │(Terminal│       └────┬────┘       │ (Terminal)│
     └───────────┘     └─────────┘            │            └───────────┘
                            ▲                 │
                            └─────────────────┘ (On rejection)
```

---

## 3. Stage Coordination & Topological DAG Execution

Workflows execute nine standard stages topologically:

1. **`REPOSITORY_ANALYSIS`** (`StageType.REPOSITORY_ANALYSIS`):
   Scans repository AST, symbols, dependencies, and test topologies.
2. **`ISSUE_ANALYSIS`** (`StageType.ISSUE_ANALYSIS`):
   Extracts requirements, acceptance criteria, and candidate code locations.
3. **`IMPLEMENTATION_PLANNING`** (`StageType.IMPLEMENTATION_PLANNING`):
   Generates structured, validated task plans with dependency cycle checks.
4. **`CODE_GENERATION`** (`StageType.CODE_GENERATION`):
   Synthesizes unified diff patches conforming to strict path validation rules.
5. **`EXECUTION_VERIFICATION`** (`StageType.EXECUTION_VERIFICATION`):
   Applies patches in isolated, ephemeral sandboxes and runs test suites.
6. **`AUTOMATIC_REPAIR`** (`StageType.AUTOMATIC_REPAIR`):
   Conditionally executed only if execution verification detects failures. Skipped automatically if tests pass on the first attempt.
7. **`CODE_REVIEW`** (`StageType.CODE_REVIEW`):
   Runs 24 deterministic security, architecture, and quality rules. Gated: score must satisfy release policy threshold.
8. **`HUMAN_APPROVAL`** (`StageType.HUMAN_APPROVAL`):
   Mandatory human authorization gate. Pauses execution and awaits cryptographic or role-validated signoff.
9. **`CONTROLLED_RELEASE`** (`StageType.CONTROLLED_RELEASE`):
   Publishes release candidate branch and opens pull request. Strict zero-auto-merge enforcement.

---

## 4. Execution Context & Artifact Integrity

Inter-stage communication passes structured data and cryptographic hashes via `WorkflowContext` and `WorkflowArtifactReferences`:

- **Artifact Binding Tracking**: IDs and SHA-256 fingerprints recorded for repository analysis, issue analysis, plan, patch, verification report, review report, and approval record.
- **Tamper Detection (`verify_artifact_integrity`)**:
  Before resuming from pause or approval, the recovery manager re-verifies on-disk or bound artifacts against recorded cryptographic hashes. Any mismatch raises `WorkflowArtifactIntegrityError` and halts execution.

---

## 5. Storage, Concurrency, and Recovery Controls

### Storage Engine (`WorkflowStore`)
- **`InMemoryWorkflowStore`**: Production-ready reference store featuring deep-copy isolation, versioned optimistic concurrency control (`ConcurrencyConflictError`), and per-workflow granular locking (`anyio.Lock`).
- **Unique Idempotency Keys**: Submitting duplicate requests with identical idempotency keys returns existing workflow runs idempotently. Submitting conflicting parameters with an existing idempotency key yields `409 Conflict`.

### Recovery Manager (`WorkflowRecoveryManager`)
- **Pause & Resume**: Safe suspension at stage boundaries, checkpointing full in-flight context.
- **Cancellation**: Graceful aborting with immediate event dispatch.
- **Crashed Workflow Recovery**: Detects zombie workflows (`RUNNING` or `RETRYING` without an active executor), transitions them to `FAILED` or `PAUSED`, and alerts operators.

---

## 6. Event-Driven Telemetry & Secret Redaction

The `WorkflowEventBus` coordinates asynchronous broadcast of 16 structured lifecycle events:
`WORKFLOW_STARTED`, `STAGE_STARTED`, `STAGE_COMPLETED`, `STAGE_SKIPPED`, `STAGE_FAILED`, `APPROVAL_REQUESTED`, `APPROVAL_GRANTED`, `APPROVAL_REJECTED`, `WORKFLOW_COMPLETED`, `WORKFLOW_FAILED`, `WORKFLOW_PAUSED`, `WORKFLOW_RESUMED`, `WORKFLOW_CANCELLED`.

### Secret Redaction Policy
All event payloads are recursively sanitized via `redact_sensitive_data()`. Any key matching sensitive patterns (`api_key`, `token`, `secret`, `password`, `authorization`, `private_key`) or string matching known token formats (GitHub PAT, generic API keys) is irreversibly masked with `[REDACTED]`.

---

## 7. REST API Reference (`/api/v1/workflows`)

| Method | Endpoint | Description | Status Codes |
|---|---|---|---|
| `POST` | `/api/v1/workflows` | Start or idempotently retrieve workflow run | `201`, `400`, `409` |
| `GET` | `/api/v1/workflows` | List workflow runs (paginated, status filter) | `200` |
| `GET` | `/api/v1/workflows/{id}` | Get workflow run state and stages | `200`, `404` |
| `GET` | `/api/v1/workflows/{id}/context` | Get workflow execution context and artifacts | `200`, `404` |
| `POST` | `/api/v1/workflows/{id}/pause` | Pause active or queued workflow | `200`, `400`, `404`, `409` |
| `POST` | `/api/v1/workflows/{id}/resume` | Resume paused or waiting workflow | `200`, `400`, `404`, `409`, `422` |
| `POST` | `/api/v1/workflows/{id}/cancel` | Cancel in-flight workflow | `200`, `400`, `404`, `409` |
| `GET` | `/api/v1/workflows/{id}/events` | Query audit and telemetry event history | `200` |

---

## 8. Safety Guarantees

1. **Zero Auto-Merge**: The controlled release stage strictly creates release candidate branches (`release/rc-...`) and pull requests. Direct merging to protected default branches is rejected at the architectural level.
2. **Non-Bypassable Authorization Boundary**: A workflow cannot proceed to release without passing through the human approval adapter.
3. **No Unrestricted Execution**: All test execution occurs inside isolated, read-only rootfs sandboxes without host filesystem exposure.
4. **Deterministic Mock Execution**: Full end-to-end integration and verification suites run deterministically in local environments without requiring paid third-party API keys.
