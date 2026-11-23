# Stage 1: Build & Dependencies
FROM python:3.13-slim AS builder

WORKDIR /build

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY pyproject.toml README.md ./
COPY backend/ ./backend/

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

# Stage 2: Minimal Secure Runtime
FROM python:3.13-slim AS runtime

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Create dedicated unprivileged non-root user
RUN groupadd -g 10001 agentforge && \
    useradd -u 10001 -g agentforge -m -s /bin/bash agentforge

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Copy application backend with non-root ownership
COPY --chown=agentforge:agentforge backend/ ./backend/
COPY --chown=agentforge:agentforge pyproject.toml README.md ./

# Ensure runtime directories exist and have proper ownership
RUN mkdir -p /app/agentforge-storage /app/agentforge-artifacts /app/agentforge-workspaces && \
    chown -R agentforge:agentforge /app

USER agentforge

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/api/v1/health || exit 1

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
