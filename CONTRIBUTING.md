# Contributing to AgentForge

Thank you for your interest in contributing to **AgentForge**! As an open-source project building an autonomous AI software-engineering agent, we maintain high standards for code quality, security, testing, and documentation.

---

## 1. Development Process & Agile Methodology

AgentForge follows an **Agile + Scrum-inspired iterative development** methodology. Development is organized into distinct sprints with clear acceptance criteria and mandatory verification.

### Definition of Done (DoD)
A feature or pull request is NOT considered done until:
- [ ] Implementation complete and typed (`mypy`).
- [ ] Code passes all linters (`ruff check .`, `ruff format --check .`).
- [ ] Unit & Integration tests complete and passing (`pytest`).
- [ ] Static security scan passes (`bandit`).
- [ ] Documentation updated in `docs/` and `README.md`.
- [ ] Three-Round Verification procedure satisfied.

---

## 2. Local Environment Setup

1. **Clone the repository**:
   ```bash
   git clone https://github.com/swikarb69/AgentForge.git
   cd AgentForge
   ```

2. **Set up virtual environment**:
   ```bash
   python -m venv .venv
   # Windows:
   .venv\Scripts\activate
   # Linux/macOS:
   source .venv/bin/activate
   ```

3. **Install dependencies**:
   ```bash
   pip install -e ".[dev]"
   ```

4. **Environment Variables**:
   Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```

---

## 3. Git Discipline & Branching Strategy

We follow Git Flow conventions:
- `main`: Production-ready releases.
- `develop`: Main development integration branch.
- `feature/<feature-name>`: Topic branches for new capabilities.
- `fix/<bug-name>`: Bug fixes.

### Conventional Commit Format
Commit messages must adhere to the Conventional Commits specification:
- `feat: add repository indexer`
- `fix: resolve issue parser regex bug`
- `docs: update architecture diagram`
- `test: add end-to-end sandbox execution test`
- `chore: update dependencies`

---

## 4. Code Quality & Testing Expectations

Before submitting a Pull Request, run the full local quality suite:

```bash
# Linting & Formatting
ruff check .
ruff format --check .

# Static Type Checking
mypy backend/app

# Security Scanning
bandit -r backend/app

# Unit & Integration Tests
pytest -q --cov=backend/app
```

---

## 5. Security Vulnerability Reporting

If you discover a potential security vulnerability within AgentForge or its sandbox execution environment, **do not open a public GitHub issue**. Please report it privately to the project author:

- **Author**: Swikar Bhattarai
- **Repository**: [https://github.com/swikarb69/AgentForge](https://github.com/swikarb69/AgentForge)
