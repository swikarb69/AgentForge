# AgentForge Deployment & Local Setup Guide

This document provides setup, configuration, containerization, and local deployment instructions for **AgentForge**.

---

## 1. System Requirements

- **Python**: Python 3.13 or higher
- **Docker**: Docker Engine 24.0+ / Docker Desktop (required for future sandbox container execution)
- **Git**: Git 2.40+
- **OS**: Linux, macOS, or Windows 11 (PowerShell / WSL2)

---

## 2. Local Environment Setup

1. **Clone Repository**:
   ```bash
   git clone https://github.com/swikarb69/AgentForge.git
   cd AgentForge
   ```

2. **Create Virtual Environment**:
   ```bash
   python -m venv .venv
   # Windows (PowerShell):
   .venv\Scripts\Activate.ps1
   # Linux / macOS:
   source .venv/bin/activate
   ```

3. **Install Dependencies**:
   ```bash
   pip install --upgrade pip
   pip install -e ".[dev]"
   ```

4. **Environment Configuration**:
   Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```

5. **Start Application Locally**:
   ```bash
   uvicorn backend.app.main:app --reload --port 8000
   ```
   Access API documentation at `http://localhost:8000/docs`.

---

## 3. Containerized Deployment (Docker & Docker Compose)

AgentForge backend services can be built and deployed locally via Docker Compose.

1. **Build Container Image**:
   ```bash
   docker compose build
   ```

2. **Run Backend Service**:
   ```bash
   docker compose up -d
   ```

3. **Verify Service Health**:
   ```bash
   curl http://localhost:8000/api/v1/health
   # Expected response: {"status":"healthy"}
   ```

4. **Stop Container Services**:
   ```bash
   docker compose down
   ```
