# AgentForge Development Methodology

This document outlines the software engineering principles, development process, quality standards, and verification workflow governing the **AgentForge** project.

---

## 1. Core Engineering Philosophy

AgentForge is built following **Agile + Scrum-inspired iterative development**. We strictly reject building software via massive, monolithic, unverified code dumps. Instead, AgentForge advances incrementally through well-defined, short sprints where every milestone is designed, implemented, tested, verified, and documented before being pushed to production.

The overarching engineering pattern governing every agent action and sprint output is:

$$\text{Understand} \longrightarrow \text{Plan} \longrightarrow \text{Implement} \longrightarrow \text{Verify} \longrightarrow \text{Repair} \longrightarrow \text{Review} \longrightarrow \text{Ship}$$

---

## 2. Sprint Lifecycle & Structure

Each sprint within AgentForge is executed as an autonomous cycle containing:

1. **Goal Statement**: A clear objective defining the value delivered by the sprint.
2. **User Stories**: Functional & non-functional requirements written from the end-user / developer perspective.
3. **Technical Tasks**: Granular modular components and engineering specifications.
4. **Implementation**: Code development adhering strictly to clean code guidelines, typing, and single responsibility.
5. **Tests**: Comprehensive unit, integration, and domain-specific tests validating all acceptance criteria.
6. **Sprint Review**: Self-assessment and code diff inspection validating against potential regressions.
7. **Acceptance Criteria Verification**: Explicit validation checklist determining feature completion.
8. **Retrospective**: Documentation of lessons learned, performance benchmarks, and limitations.
9. **Git Commit & Push**: Atomic commit adhering to Conventional Commits and pushed to remote `origin`.

---

## 3. Definition of Done (DoD)

A feature or sprint milestone is strictly **NOT DONE** until all the following criteria are satisfied:

- [x] **Implementation Complete**: All code files created/updated with strict Python 3.13 type annotations (`mypy`).
- [x] **Unit & Integration Tests Complete**: Full test suite passing with high branch/line coverage (`pytest`).
- [x] **Static Code Analysis**: Zero errors or warnings reported by `ruff check` and `ruff format`.
- [x] **Security Audit**: Static security scanner (`bandit`) clean with zero critical issues.
- [x] **Error Handling**: Graceful failure modes, structured exceptions, and zero silent exception masking.
- [x] **Logging & Observability**: Structured logs for key state transitions without secret leakage.
- [x] **Documentation Updated**: Technical specifications, API docs, and `README.md` updated.
- [x] **Three-Round Verification Complete**: Passed Pass 1 (Automated), Pass 2 (Functional), and Pass 3 (Clean Environment).
- [x] **Git Security Audit**: Checked for uncommitted credentials, `.env` leakage, or build junk.
- [x] **Remote Push Verified**: Commit pushed to GitHub remote (`swikarb69/AgentForge.git`) and verified via `git ls-remote`.

---

## 4. Three-Round Verification Requirement

To guarantee production quality before any code reaches the `main` or `develop` branches on GitHub, developers and agents MUST complete **Three Distinct Verification Passes**:

### Verification Pass 1 — Static & Automated Quality
Automated execution of all static linters, type checkers, security scanners, unit tests, and coverage engines:
```bash
ruff check .
ruff format --check .
mypy backend/app
bandit -r backend/app
pytest --cov=backend/app --cov-report=term-missing
```

### Verification Pass 2 — Functional & End-to-End Runtime
Execution of the actual application instance (e.g., via `uvicorn` or ASGI test client) to test real HTTP requests, configuration loading, security policy JSON validation, and OpenAPI documentation endpoint generation (`/api/v1/health`, `/api/v1/version`, `/docs`).

### Verification Pass 3 — Clean Environment Verification
Validation from a completely clean, isolated environment (e.g., fresh virtual environment or container) starting from a clean git checkout. Guarantees that no hidden local dependencies, cached files, or uncommitted `.env` variables break clean deployments.

---

## 5. Planned 11-Sprint Roadmap

| Sprint | Name | Primary Objective & Deliverables | Status |
|---|---|---|---|
| **Sprint 0** | **Foundation** | Architecture, directory layout, FastAPI baseline, config, CI/CD, testing, MIT License, docs | **CURRENT** |
| **Sprint 1** | **Repository Intelligence** | Repo scanner, file tree builder, AST symbol extractor, dependency mapper | PLANNED |
| **Sprint 2** | **Issue Understanding** | GitHub issue parser, requirement extractor, acceptance criteria generator | PLANNED |
| **Sprint 3** | **Planning Agent** | Implementation planner, file touch set optimizer, diff risk scorer | PLANNED |
| **Sprint 4** | **Coding Agent** | Code generation engine, patch generator, context-aware code synthesizer | PLANNED |
| **Sprint 5** | **Execution Sandbox** | Docker sandbox runner, resource limits, command filtering, network isolation | PLANNED |
| **Sprint 6** | **Testing & Debugging** | Test generator, automated test executor, stack trace analyzer, auto-repair loop | PLANNED |
| **Sprint 7** | **Security & Code Review** | Deterministic security scanner, secret scanner, LLM code reviewer, approval gate | PLANNED |
| **Sprint 8** | **GitHub Shipping** | Git branch manager, commit synthesizer, pull request creator | PLANNED |
| **Sprint 9** | **Evaluation & Benchmarks** | Benchmark task suite, latency/cost trackers, token usage analytics | PLANNED |
| **Sprint 10** | **Production Polish** | Developer UI dashboard, final documentation, end-to-end audit | PLANNED |

---

## 6. Git Discipline & Commit Guidelines

- **Atomic Commits**: Each commit represents one logical feature, fix, or sprint milestone.
- **Conventional Commit Format**:
  - `feat: <description>` (new features)
  - `fix: <description>` (bug fixes)
  - `docs: <description>` (documentation)
  - `test: <description>` (tests)
  - `chore: <description>` (maintenance/foundation)
- **Branching Strategy**: `main` (stable production), `develop` (staging integration), `feature/<name>` (working feature branches).
