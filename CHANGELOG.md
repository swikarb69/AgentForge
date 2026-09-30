# Changelog

All notable changes to AgentForge will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-09-25

### Added - Sprint 0: Foundation
- **Project Structure**: Standardized directory hierarchy (`backend/`, `sandbox/`, `tests/`, `docs/`, `.github/`).
- **FastAPI Core**: Base API foundation exposing `/api/v1/health` and `/api/v1/version`.
- **Configuration Engine**: Pydantic Settings implementation (`backend/app/core/config.py`).
- **Sandbox Baseline**: `sandbox/Dockerfile` and `sandbox/policies/security_policy.json` foundation.
- **Testing Architecture**: Pytest unit & integration test framework with coverage measurement.
- **Quality & CI/CD**: GitHub Actions workflow (`.github/workflows/ci.yml`) enforcing Ruff, MyPy, Pytest, and Bandit security scans.
- **Licensing**: Standard MIT License (Swikar Bhattarai).
- **Documentation**: Comprehensive suite (`docs/development-methodology.md`, `architecture.md`, `agent-system.md`, `security.md`, `testing.md`, `deployment.md`, `roadmap.md`, `README.md`).
