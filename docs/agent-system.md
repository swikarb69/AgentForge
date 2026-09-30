# AgentForge Multi-Agent Architecture

> [!IMPORTANT]
> **CURRENT STATUS: PLANNED — NOT IMPLEMENTED IN SPRINT 0**
>
> Sprint 0 establishes the foundational architecture and infrastructure. The specialized AI agents described below will be implemented incrementally across Sprints 1 through 8.

---

## Overview of the 9 Specialized Agents

AgentForge breaks down complex software engineering tasks across nine specialized AI agents, each possessing a single responsibility, specific tool bindings, and constrained context scope.

```text
┌────────────────────────────────────────────────────────────────────────┐
│                        Orchestrator Agent                              │
└───────┬────────────────────────────────────────────────────────┬───────┘
        │                                                        │
        ▼                                                        ▼
┌──────────────────┐                                   ┌──────────────────┐
│ 1. Repo Analyst  │                                   │ 2. Issue Analyst │
└───────┬──────────┘                                   └─────────┬────────┘
        │                                                        │
        └──────────────────────────┬─────────────────────────────┘
                                   │
                                   ▼
                         ┌──────────────────┐
                         │    3. Planner    │
                         └─────────┬────────┘
                                   │
                                   ▼
                         ┌──────────────────┐
                         │ 4. Coder Agent   │
                         └─────────┬────────┘
                                   │
                                   ▼
                         ┌──────────────────┐
                         │  5. Test Agent   │
                         └─────────┬────────┘
                                   │
                     ┌─────────────┴─────────────┐
                     ▼                           ▼
            [Tests Pass]                   [Tests Fail]
                     │                           │
                     │                           ▼
                     │                 ┌──────────────────┐
                     │                 │   6. Debugger    │
                     │                 └─────────┬────────┘
                     │                           │
                     │                           └──────► (Loop to Coder)
                     ▼
          ┌──────────────────┐
          │ 7. Security Agent│
          └──────────┬───────┘
                     │
                     ▼
          ┌──────────────────┐
          │ 8. Code Reviewer │
          └──────────┬───────┘
                     │
                     ▼
          ┌──────────────────┐
          │   9. PR Agent    │
          └──────────────────┘
```

---

## Detailed Agent Specifications

### 1. Repository Analyst Agent
- **Sprint**: Sprint 1
- **Responsibility**: Inspect target codebase tree, discover directory structure, build AST index, identify entry points, frameworks, and existing test setups.
- **Inputs**: Local git checkout / repository clone.
- **Outputs**: Structural topology report, AST symbol graph, file dependency map.

### 2. Issue Analyst Agent
- **Sprint**: Sprint 2
- **Responsibility**: Parse GitHub issue title, body, comments, labels, and issue metadata. Resolve ambiguities, extract implicit requirements, and write clear acceptance criteria.
- **Inputs**: GitHub Issue payload.
- **Outputs**: Structured engineering specification + Acceptance Criteria.

### 3. Implementation Planning Agent
- **Sprint**: Sprint 3
- **Responsibility**: Formulate step-by-step modification plan, isolate precise minimal file sets to modify/create, assess regression risks, and set boundary constraints.
- **Inputs**: Issue specification + Repository topology.
- **Outputs**: `plan.json` (Structured Implementation Plan).

### 4. Coding Agent
- **Sprint**: Sprint 4
- **Responsibility**: Synthesize context-aware code edits following repository conventions, minimizing diff bloat, and preserving existing API contracts.
- **Inputs**: Implementation plan + Retrieved RAG code context chunks.
- **Outputs**: Patch file / source file modifications.

### 5. Test Agent
- **Sprint**: Sprint 6
- **Responsibility**: Inspect existing test suites, generate missing unit/integration tests for new features/fixes, validate edge cases against acceptance criteria.
- **Inputs**: Modified code + Existing test files.
- **Outputs**: Test file diffs + Execution requests.

### 6. Debugger Agent
- **Sprint**: Sprint 6
- **Responsibility**: Parse test failure output, analyze tracebacks, isolate root cause, formulate minimal corrective patches, and trigger re-verification.
- **Inputs**: Failed test logs + Stack traces + Code diffs.
- **Outputs**: Root cause diagnosis + Corrective patch proposal.

### 7. Security Review Agent
- **Sprint**: Sprint 7
- **Responsibility**: Conduct deterministic and LLM security audits. Flag secret leakage, command injection, path traversal, unsafe dependencies, and prompt injection attacks.
- **Inputs**: Cumulative Git diff + Modified files.
- **Outputs**: Security audit report (PASS / FAIL + findings).

### 8. Code Review Agent
- **Sprint**: Sprint 7
- **Responsibility**: Perform senior peer code review on final diffs. Evaluate maintainability, code cleanliness, test coverage, potential edge case bugs, and requirement compliance.
- **Inputs**: Complete task diff + Test results + Issue specs.
- **Outputs**: Code review report (Approved / Changes Requested).

### 9. Pull Request Agent
- **Sprint**: Sprint 8
- **Responsibility**: Create feature branch, synthesize commit messages adhering to Conventional Commits, construct pull request title/description with test evidence and auditable run logs.
- **Inputs**: Verified git workspace + Run audit logs.
- **Outputs**: GitHub Pull Request created.
