"""Structured workflow event models and sensitive data redaction."""

import logging
import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from backend.app.orchestration.states import StageType

logger = logging.getLogger(__name__)

_SENSITIVE_KEY_PATTERN = re.compile(
    r"(?i)(api[_-]?key|secret|token|password|auth|bearer|credential|private[_-]?key)"
)


def redact_sensitive_data(val: Any) -> Any:
    """Recursively sanitize and redact sensitive key-value pairs."""
    if isinstance(val, dict):
        redacted = {}
        for k, v in val.items():
            if isinstance(k, str) and _SENSITIVE_KEY_PATTERN.search(k):
                redacted[k] = "[REDACTED]"
            else:
                redacted[k] = redact_sensitive_data(v)
        return redacted
    if isinstance(val, list):
        return [redact_sensitive_data(item) for item in val]
    if isinstance(val, tuple):
        return tuple(redact_sensitive_data(item) for item in val)
    return val


class WorkflowEventType(StrEnum):
    """Enumeration of lifecycle and telemetry events in workflow orchestration."""

    WORKFLOW_STARTED = "workflow.started"
    WORKFLOW_PAUSED = "workflow.paused"
    WORKFLOW_RESUMED = "workflow.resumed"
    WORKFLOW_CANCELLED = "workflow.cancelled"
    WORKFLOW_COMPLETED = "workflow.completed"
    WORKFLOW_FAILED = "workflow.failed"

    STAGE_STARTED = "stage.started"
    STAGE_COMPLETED = "stage.completed"
    STAGE_FAILED = "stage.failed"
    STAGE_SKIPPED = "stage.skipped"
    STAGE_RETRY = "stage.retry"

    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_GRANTED = "approval.granted"
    APPROVAL_REJECTED = "approval.rejected"

    RELEASE_STARTED = "release.started"
    RELEASE_COMPLETED = "release.completed"


class WorkflowEvent(BaseModel):
    """Structured, immutable audit and telemetry event."""

    event_id: str = Field(
        default_factory=lambda: f"evt-{uuid4().hex[:12]}",
        description="Globally unique identifier for this telemetry event",
    )
    event_type: WorkflowEventType = Field(
        description="Categorical workflow event type",
    )
    workflow_id: str = Field(
        description="Associated workflow run identifier",
    )
    stage_type: StageType | None = Field(
        default=None,
        description="Pipeline stage type if event is stage-scoped",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="ISO 8601 UTC timestamp of event generation",
    )
    duration_ms: float | None = Field(
        default=None,
        ge=0.0,
        description="Duration in milliseconds for span events",
    )
    correlation_id: str = Field(
        default="",
        description="Distributed tracing or correlation identifier",
    )
    actor: str | None = Field(
        default=None,
        description="User, agent, or component identity triggering the event",
    )
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Sanitized contextual attributes and event metadata",
    )
    error: str | None = Field(
        default=None,
        description="Error description if event represents a failure",
    )

    @model_validator(mode="before")
    @classmethod
    def sanitize_payload_and_correlation(cls, data: Any) -> Any:
        """Sanitize secrets in payload and ensure correlation ID defaults."""
        if isinstance(data, dict):
            if not data.get("correlation_id"):
                data["correlation_id"] = data.get("workflow_id", "")
            if "payload" in data and isinstance(data["payload"], dict):
                data["payload"] = redact_sensitive_data(data["payload"])
        return data
