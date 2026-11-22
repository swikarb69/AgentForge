# AgentForge — Human Approval & Controlled Release Engine (Sprint 8)

The **Human Approval & Controlled Release Engine** establishes an explicit, auditable authorization boundary between the autonomous engineering pipeline (Sprints 0–7) and production repositories.

---

## 1. Safety Principles & Core Philosophy

1. **Human Authorization Boundary**: AgentForge **NEVER** independently decides that a change is safe to release merely because tests passed or code review reports recommended approval. An authorized human must explicitly approve the release candidate.
2. **Zero Automatic Merging**: AgentForge **NEVER** merges pull requests into default or protected branches (`main`, `master`). Release operations strictly create release candidate branches (e.g. `agentforge/rc-...`) and pull requests for human review.
3. **Separation of Duties**: An actor who created or requested an implementation cannot approve or release their own candidate.
4. **Cryptographic Binding**: Approval decisions and release candidates are cryptographically bound to the SHA-256 hashes of the patch, implementation plan, and review report. If any artifact is modified, approvals are invalidated.
5. **Tamper-Evident Audit Trail**: Every request, decision, revocation, and release operation is appended to an immutable SHA-256 hash-chained audit log with automatic secret redaction.
6. **Zero External Credentials Required**: Offline testing and local execution are 100% deterministic using `MockReleaseProvider` and `LocalTestIdentityProvider`.

---

## 2. Pipeline Integration Architecture

```text
[Sprint 0: Foundation]
       │
[Sprint 1: Repo Intelligence] ───┐
       │                         │
[Sprint 2: Issue Intelligence] ──┼──> [Sprint 8: Approval Request Creation]
       │                         │       │ (Binds patch, plan, review hashes)
[Sprint 3: Planning Engine] ────┼───────┤
       │                         │       ▼
[Sprint 4: Code Generation] ─────┤   [Human Decision Evaluation]
       │                         │       │ (Separation of duties, roles, expiry)
[Sprint 5: Execution Sandbox] ───┤       ▼
       │                         ├──> [13-Point Release Readiness Engine]
[Sprint 6: Automatic Repair] ────┤       │ (Validates all upstream artifacts)
       │                         │       ▼
[Sprint 7: Code Review & QA] ────┘   [Controlled Release Execution]
                                         │ (Mock or GitHub Release Provider)
                                         ▼
                                     [Pull Request Created for Human Review]
```

---

## 3. Core Subsystems

### 3.1 Domain Models & State Machine
- **`ApprovalRequest`**: Encapsulates release candidate metadata, bound artifact hashes, risk level, expiration deadlines, decision history, and active blockers.
- **`ApprovalDecision`**: Immutable record of reviewer verdict (`approved`, `rejected`, `changes_requested`, `revoked`), reviewer role, comments, and observed patch/review hashes.
- **`ApprovalStateMachine`**: Deterministic state machine enforcing legal transitions (`PENDING` -> `APPROVED` / `REJECTED` / `CHANGES_REQUESTED` / `EXPIRED` / `REVOKED` / `BLOCKED`).
- **`ArtifactBinding`**: Cryptographic SHA-256 bindings securing release candidates against out-of-band artifact tampering.

### 3.2 Identity & Authorization
- **`UserIdentity`**: Authenticated human actor or service identity with role classifications and repository permissions (`read`, `triage`, `write`, `approve`, `release`, `admin`).
- **`AuthorizationEngine`**: Enforces separation of duties, role requirements, repository-level permissions, and duplicate decision prevention.
- **`LocalTestIdentityProvider`**: In-memory deterministic provider with seeded identities for offline testing and local workflows.
- **`ProductionIdentityProvider`**: Production enterprise identity stub explicitly requiring configured enterprise directory services.

### 3.3 Policies & Risk-Based Tiers
- **`ApprovalPolicy`**: Configurable rules governing minimum approvers, required roles, expiration deadlines, and allowed target branches.
- **Risk Presets**:
  - `LOW`: 1 approver, 72h expiration.
  - `MEDIUM`: 1 approver with write/approve permissions, 48h expiration.
  - `HIGH`: 2 approvers (including peer reviewer), 24h expiration.
  - `CRITICAL`: 2 approvers (including Administrator), 12h expiration, strict branch controls.

### 3.4 Tamper-Evident Audit Trail
- **`AuditTrailStore`**: Append-only log where each `AuditEventRecord` incorporates `previous_event_hash` to form a continuous SHA-256 hash chain starting from `GENESIS_HASH`.
- **Secret Redaction**: Passwords, API tokens, bearer keys, and AWS/GitHub credentials are automatically sanitized before audit persistence.
- **Integrity Verification**: `verify_integrity(approval_id)` computes sequence continuity and verifies cryptographic hashes, detecting any modified, injected, or deleted events.

### 3.5 13-Point Release Readiness Engine
Before any release operation can proceed, `ReleaseReadinessEngine` verifies:
1. Target repository reference and path existence.
2. Linked issue context and requirements traceability.
3. Implementation plan validity and hash match.
4. Code generation validation and patch applicability.
5. Sandbox execution verification status and test results.
6. Zero unresolved or failed automatic repair attempts.
7. Sprint 7 code review report presence.
8. Zero critical security defects or review blockers.
9. Required human approval threshold satisfied.
10. Current request status is `APPROVED`.
11. Bound patch and review artifact hashes match observed candidate.
12. Target branch allowed by policy.
13. Request deadline has not expired.

### 3.6 Controlled Release Providers
- **`MockReleaseProvider`**: In-memory branch creation, commit synthesis, and PR generation with configurable failure injection modes for testing.
- **`GitHubReleaseProvider`**: Production REST client interacting with GitHub's Git Data and Pulls APIs. Strictly requires `GITHUB_TOKEN` and never initiates automatic branch merges.
- **`ControlledReleaseService`**: Orchestrates release readiness validation, actor authorization, idempotency checks, release branch synthesis (`agentforge/rc-...`), commit creation, PR generation, and audit logging.

---

## 4. REST API Reference

All approval endpoints are prefixed with `/api/v1/approval`:

| Method | Endpoint | Description | Status Code |
| :--- | :--- | :--- | :--- |
| `POST` | `/api/v1/approval/requests` | Create approval request | `201 Created` |
| `GET` | `/api/v1/approval/requests` | List requests (filter by repo/status) | `200 OK` |
| `GET` | `/api/v1/approval/requests/{id}` | Get request details (on-read expiry) | `200 OK` |
| `POST` | `/api/v1/approval/requests/{id}/decide` | Submit human approval or rejection | `200 OK` |
| `POST` | `/api/v1/approval/requests/{id}/revoke` | Revoke granted approval | `200 OK` |
| `GET` | `/api/v1/approval/requests/{id}/readiness` | Evaluate 13-point readiness criteria | `200 OK` |
| `POST` | `/api/v1/approval/requests/{id}/release` | Execute controlled release & create PR | `201 Created` |
| `GET` | `/api/v1/approval/requests/{id}/audit` | Retrieve chronological audit history | `200 OK` |
| `GET` | `/api/v1/approval/requests/{id}/audit/verify` | Verify cryptographic hash chain | `200 OK` |
| `GET` | `/api/v1/approval/releases/{id}` | Query release operation status | `200 OK` |
| `GET` | `/api/v1/approval/releases` | List release operations | `200 OK` |

---

## 5. Verification & Testing

- **Unit Test Coverage**: >90% branch/statement coverage across `backend/app/approval/`.
- **Integration Test Suite**: Complete end-to-end lifecycle verification via FastAPI `TestClient` (`tests/integration/test_approval_release_pipeline.py`).
- **Static Analysis & Linting**: 100% compliant with Ruff line length <=88 chars (`ruff check`, `ruff format`).
- **Type Safety**: Fully typed with MyPy in strict mode (`python -m mypy`).
- **Security Audit**: 0 security issues found across 3000+ lines scanned with Bandit (`python -m bandit`).
