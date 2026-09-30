# AgentForge Testing & Quality Strategy

This document describes the testing architecture, test organization, coverage requirements, quality tooling, and verification protocols for **AgentForge**.

---

## 1. Testing Philosophy

Testing in AgentForge is a first-class engineering requirement, not an afterthought. Autonomous AI software agents must be subjected to rigorous, deterministic verification before generated code is allowed to enter production codebases.

Every component within AgentForge must be testable without relying on external non-deterministic services. Unit tests utilize mocks/fakes for external LLM API and GitHub API calls.

---

## 2. Test Suite Organization

```text
tests/
├── unit/
│   ├── test_health.py       # API health & version endpoint unit tests
│   ├── test_config.py       # Pydantic configuration & env override tests
│   └── ...
│
├── integration/
│   ├── test_app_init.py     # FastAPI ASGI initialization & OpenAPI schema tests
│   └── ...
│
├── security/ [Planned - Sprint 7]
│   └── test_security_policy.py  # Command injection & path traversal filter tests
│
├── agent/ [Planned - Sprint 4-8]
│   └── test_state_machine.py    # Agent state machine transition tests
│
├── execution/ [Planned - Sprint 5]
│   └── test_sandbox.py         # Docker sandbox runner isolation tests
│
└── e2e/ [Planned - Sprint 9]
    └── test_end_to_end_flow.py # Complete Issue-to-PR workflow end-to-end tests
```

---

## 3. Mandatory Quality Tooling

1. **Pytest (`pytest`)**: Test runner and assertion framework.
2. **Pytest Coverage (`pytest-cov`)**: Code coverage tracking (Sprint 0 baseline threshold: $\ge 80\%$).
3. **Ruff (`ruff check .`, `ruff format --check .`)**: Ultra-fast linting and code formatting check.
4. **MyPy (`mypy backend/app`)**: Static type checking enforcing Python 3.13 strict type safety.
5. **Bandit (`bandit -r backend/app`)**: Static security analysis identifying security vulnerabilities.

---

## 4. The Three-Round Verification Protocol

Prior to pushing any major milestone or sprint code to GitHub, developers and agents MUST complete **Three Verification Passes**:

```text
┌──────────────────────────────────────────────────────────┐
│        Verification Pass 1: Static & Automated           │
│  (Ruff Lint + Format + MyPy + Bandit + Pytest + Cov)     │
└────────────────────────────┬─────────────────────────────┘
                             │ PASS
                             ▼
┌──────────────────────────────────────────────────────────┐
│        Verification Pass 2: Functional & Runtime         │
│  (Uvicorn ASGI test execution + Endpoint Verification)   │
└────────────────────────────┬─────────────────────────────┘
                             │ PASS
                             ▼
┌──────────────────────────────────────────────────────────┐
│     Verification Pass 3: Clean Environment Checkout      │
│  (Fresh virtualenv setup + Full test re-execution)       │
└──────────────────────────────────────────────────────────┘
```

---

## 5. Running Tests Locally

```bash
# Run unit tests only
pytest tests/unit

# Run integration tests only
pytest tests/integration

# Run full test suite with coverage
pytest --cov=backend/app --cov-report=term-missing
```
