# AgentForge Execution Sandbox Container Foundation
# NOTE: Full sandbox enforcement and secure code execution will be implemented in Sprint 5.
FROM python:3.13-slim

WORKDIR /sandbox

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Create non-root unprivileged sandbox user
RUN groupadd -g 10001 sandboxuser && \
    useradd -u 10001 -g sandboxuser -m -s /bin/bash sandboxuser

# Install base toolchain for isolated test execution
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

USER sandboxuser

CMD ["python3", "-c", "print('AgentForge Sandbox Foundation Ready')"]
