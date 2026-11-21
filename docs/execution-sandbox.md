# AgentForge — Execution & Verification Sandbox (Sprint 5)

The **Execution & Verification Sandbox** is the fifth core layer of AgentForge. It takes a validated code change or patch proposal from Sprint 4, prepares an isolated ephemeral workspace, securely applies the patch, executes permitted verification commands inside a locked-down Docker container, and produces an auditable `VerificationReport`.

---

## 1. High-Level Architecture & Workflow

```text
Generated Patch / ExecutionRequest (from Sprint 4)
         ↓
Policy & Boundary Validation (`backend/app/execution/policy.py`)
  ├── Validates command against strict whitelist (`pytest`, `python -m pytest`, `ruff check`)
  ├── Enforces resource bounds (CPU ≤ 2.0, Memory ≤ 1024MB, Timeout ≤ 120s)
  └── Validates workspace size limit (≤ 50MB) and target paths
         ↓
Workspace Manager (`backend/app/execution/workspace.py`)
  ├── Clones target repository snapshot into isolated ephemeral workspace
  ├── Excludes sensitive directories (`.git`, `.venv`, `__pycache__`, `.env`, keys, tokens)
  ├── Enforces workspace disk quotas
  └── Guarantees full deterministic cleanup (sync & async context managers)
         ↓
Secure Patch Applier (`backend/app/execution/patch_applier.py`)
  ├── Validates target paths against traversal (`..`, `:`, leading slashes)
  ├── Blocks mutations to protected files (`.env`, `.git`, `Dockerfile`, CI configs)
  ├── Verifies original file baseline SHA-256 hashes
  └── Applies file changes and unified diffs in-memory / locally to workspace only
         ↓
Restricted Docker Runner (`backend/app/execution/docker_runner.py`)
  ├── Hard non-root user execution (`10001:10001`)
  ├── Drops ALL Linux capabilities (`--cap-drop=ALL`)
  ├── Mounts container root filesystem as strictly read-only (`--read-only`)
  ├── Mounts isolated workspace as read-write (`/workspace`)
  ├── Disables container networking entirely (`--network=none`)
  ├── Allocates constrained ephemeral `/tmp` tmpfs with `noexec,nosuid,nodev`
  ├── Enforces CPU (`--cpus`) and memory (`--memory`) hard limits
  ├── Enforces subprocess timeout with process termination
  ├── Caps log output (`MAX_OUTPUT_BYTES = 500KB`) to prevent memory exhaustion
  └── STRICT NO-HOST-FALLBACK: Never falls back to host execution if Docker is absent
         ↓
Test Executor (`backend/app/execution/test_executor.py`)
  ├── Executes permitted verification commands sequentially
  ├── Parses structured test output (pytest summary counts, failures, errors, skipped)
  ├── Parses linter output (ruff syntax/formatting/rule errors)
  └── Computes execution metrics (duration, exit codes, output truncation)
         ↓
Verification Engine (`backend/app/execution/verification.py`)
  ├── Evaluates overall verification verdict (`PASSED`, `FAILED`, `ERROR`, `TIMED_OUT`)
  ├── Verifies patch application status and original baseline hash provenance
  └── Synthesizes immutable `VerificationReport` stored in `agentforge-artifacts/execution/`
         ↓
Execution Service & REST API (`backend/app/execution/service.py`, `backend/app/api/routes/execution.py`)
```

---

## 2. Key Architectural Guarantees & Safeguards

### Strict No-Host-Fallback Guarantee
Under no circumstances will generated code or verification tests execute on the host machine.
- If the Docker daemon is unavailable, offline, or inaccessible, the sandbox runner fails fast and deterministically.
- Returns `ExecutionStatus.FAILED` with exit code `127` and a clear error explanation: `Docker CLI is not available in host environment. No host execution fallback is permitted for security.`
- Host isolation is non-negotiable.

### Repository Immutability Guarantee
- The target repository is treated as strictly read-only.
- All patches are applied solely to a temporary isolated workspace (`agentforge-workspaces/{execution_id}`).
- Original repository files are never modified, created, or deleted.
- Ephemeral workspaces are automatically purged upon execution completion.

### Non-Root & Dropped Capabilities
- Containers execute strictly as unprivileged user `agentforge` with UID/GID `10001:10001`.
- All Linux kernel capabilities are explicitly dropped via `cap_drop=["ALL"]`.
- The root filesystem (`/`) is mounted read-only (`read_only_rootfs=True`).
- Temporary scratch space `/tmp` is mounted as an in-memory `tmpfs` sized at 64MB with `noexec,nosuid,nodev`.

### Network Isolation
- Container network mode is set to `none` (`--network=none`).
- Sandboxed processes cannot communicate over the network, preventing data exfiltration, phone-home callbacks, or outbound attacks.

### Path Traversal & Protected File Defenses
- Target paths are sanitized and resolved strictly relative to the workspace root.
- Paths with directory traversal tokens (`..`), drive colons (`:`), or pointing outside the workspace are blocked.
- Protected configuration files (e.g. `.env`, `.git`, `.github`, `Dockerfile`, `docker-compose.yml`, credentials, certificates) cannot be modified or replaced by patches.

---

## 3. Execution Domain Models

The execution subsystem is strongly typed using Pydantic V2 models (`backend/app/execution/models.py`):

- **`ExecutionStatus`**: Enum representing `PENDING`, `RUNNING`, `PASSED`, `FAILED`, `TIMED_OUT`, `ERROR`.
- **`VerificationProfile`**: Predefined verification profiles (`python-unit-tests`, `python-quality-checks`).
- **`ExecutionPolicy`**: Configurable security policy defining `timeout_seconds` (default 60s, max 120s), `memory_limit` (default "512m", max "1024m"), `cpu_limit` (default 1.0, max 2.0), `process_limit` (default 64), `network_enabled` (always `False`), `read_only_root_filesystem` (always `True`), `drop_capabilities` (`["ALL"]`), `allowed_commands` (`pytest`, `python`, `python3`, `git`, `ruff`), `denied_command_patterns`, and `permitted_environment_variables`.
- **`ExecutionRequest`**: Internal pipeline specification containing `execution_id`, `repository_path`, `generation_id`, `generation_result`, `patch_content`, `patch_file_path`, `requested_verification_profile`, and `policy`.
- **`CommandResult`**: Individual command execution outcome with `command`, `exit_code`, `stdout`, `stderr`, `duration_seconds`, `status`, and `truncated`.
- **`TestSummary`**: Aggregated test statistics (`total_tests`, `passed_tests`, `failed_tests`, `error_tests`, `skipped_tests`, `duration_seconds`, `failure_details`).
- **`ExecutionResult`**: Low-level execution run result (`execution_id`, `status`, `exit_code`, `started_at`, `completed_at`, `duration_seconds`, `commands_executed`, `test_summary`, `error_message`).
- **`VerificationReport`**: High-level verification artifact synthesizing sandbox outcomes (`execution_id`, `execution_success`, `patch_applied`, `tests_executed`, `verification_passed`, `failures`, `warnings`, `execution_result`, `created_at`).
- **`CreateExecutionRequest`**: Public API request payload (`repository_path`, `generation_id`, `generation_result`, `verification_profile`, `policy`, `patch_content`).

---

## 4. REST API Endpoints

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `POST` | `/api/v1/execution/create` | Runs execution and verification for a patch against an isolated workspace in Docker. |
| `GET` | `/api/v1/execution/reports` | Lists all verification reports generated during the runtime session. |
| `GET` | `/api/v1/execution/reports/{execution_id}` | Retrieves a specific verification report by its unique execution ID. |

### Request Example (`POST /api/v1/execution/create`)
```json
{
  "repository_path": "agentforge-fixtures/sample-project",
  "generation_id": "GEN-C5A88EA2",
  "verification_profile": "python-unit-tests"
}
```

### Response Example
```json
{
  "execution_id": "EXEC-7C246294",
  "execution_success": true,
  "patch_applied": true,
  "tests_executed": true,
  "verification_passed": true,
  "failures": [],
  "warnings": [],
  "execution_result": {
    "execution_id": "EXEC-7C246294",
    "status": "passed",
    "exit_code": 0,
    "started_at": "2026-11-21T18:55:00Z",
    "completed_at": "2026-11-21T18:55:01Z",
    "duration_seconds": 0.45,
    "commands_executed": [
      {
        "command": "pytest -q",
        "exit_code": 0,
        "stdout": "3 passed in 0.12s\n",
        "stderr": "",
        "duration_seconds": 0.25,
        "status": "passed",
        "truncated": false
      }
    ],
    "test_summary": {
      "total_tests": 3,
      "passed_tests": 3,
      "failed_tests": 0,
      "error_tests": 0,
      "skipped_tests": 0,
      "duration_seconds": 0.25,
      "failure_details": []
    },
    "error_message": null
  },
  "created_at": "2026-11-21T18:55:01Z"
}
```

---

## 5. Verification & Testing

The execution subsystem is tested with comprehensive unit, integration, and security suites:

- **Unit Tests**:
  - `tests/unit/test_execution_models.py`: Model creation, validation bounds, status transitions, JSON serialization.
  - `tests/unit/test_execution_policy.py`: Command whitelisting, command argument injection checks, resource caps, blocked patterns.
  - `tests/unit/test_workspace.py`: Ephemeral directory lifecycle, exclusion of sensitive files (`.git`, `.venv`, `.env`), size limit enforcement, context manager cleanup.
  - `tests/unit/test_patch_applier.py`: Patch application from structured changes and unified diffs, traversal rejection, protected path enforcement, baseline hash verification.
  - `tests/unit/test_docker_runner.py`: Docker command assembly, user/group flags, capability dropping, read-only rootfs, tmpfs args, timeout handling, output truncation, no-host-fallback behavior.
  - `tests/unit/test_test_executor.py`: Command dispatching, Pytest output parsing, Ruff linter output parsing, execution metrics.
  - `tests/unit/test_verification.py`: Verification report synthesis, status aggregation, report persistence to disk.

- **Integration & Security Tests**:
  - `tests/integration/test_execution_pipeline.py`: Full end-to-end execution pipeline, target repository immutability, failed test diagnosis, and REST API routes (`/create`, `/reports`, `/reports/{id}`).
  - `tests/integration/test_execution_security.py`: Verifies zero host fallback when Docker is missing, path traversal blocking, protected configuration blocking, policy upper bounds enforcement, and workspace quota enforcement.
