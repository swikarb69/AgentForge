"""Append-only audit trail and tamper-evident event verification."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from backend.app.review.models import redact_secrets

GENESIS_HASH = "0" * 64


class AuditEventRecord(BaseModel):
    """Immutable audit record within a tamper-evident cryptographic hash chain."""

    event_id: str = Field(description="Unique audit record identifier")
    approval_id: str = Field(description="Associated approval request identifier")
    sequence: int = Field(ge=1, description="Monotonically increasing sequence number")
    actor_id: str = Field(
        description="Identifier of the actor or system initiating event"
    )
    action: str = Field(description="Action name (e.g. approval_requested, approved)")
    timestamp: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
        description="ISO 8601 UTC timestamp",
    )
    previous_state: str | None = Field(
        default=None, description="Status prior to this event"
    )
    new_state: str | None = Field(default=None, description="Status after this event")
    reason: str | None = Field(
        default=None, description="Justification or explanation for action"
    )
    artifact_hashes: dict[str, str | None] = Field(
        default_factory=dict, description="Snapshot of artifact hashes at event time"
    )
    correlation_id: str | None = Field(
        default=None, description="Upstream or tracing correlation identifier"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Supplementary contextual metadata"
    )
    previous_event_hash: str = Field(
        default=GENESIS_HASH,
        description="SHA-256 hash of the preceding event record",
    )
    event_hash: str = Field(
        default="", description="Cryptographic SHA-256 checksum of this record"
    )


def compute_event_hash(event: AuditEventRecord) -> str:
    """Compute deterministic SHA-256 hash over canonical representation of record."""
    payload = {
        "event_id": event.event_id,
        "approval_id": event.approval_id,
        "sequence": event.sequence,
        "actor_id": event.actor_id,
        "action": event.action,
        "timestamp": event.timestamp,
        "previous_state": event.previous_state,
        "new_state": event.new_state,
        "reason": event.reason,
        "artifact_hashes": dict(sorted(event.artifact_hashes.items())),
        "correlation_id": event.correlation_id,
        "previous_event_hash": event.previous_event_hash,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class AuditTrailStore:
    """In-memory append-only tamper-evident audit store.

    Durability Notice:
    This in-memory store persists audit records in RAM for the lifetime of
    the process. It provides cryptographic hash-chaining for tamper evidence,
    but does not provide hardware WORM or external distributed ledger durability.
    """

    def __init__(self) -> None:
        self._events_by_approval: dict[str, list[AuditEventRecord]] = {}
        self._all_events: list[AuditEventRecord] = []

    def append_event(
        self,
        approval_id: str,
        actor_id: str,
        action: str,
        previous_state: str | None = None,
        new_state: str | None = None,
        reason: str | None = None,
        artifact_hashes: dict[str, str | None] | None = None,
        correlation_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AuditEventRecord:
        """Create and append an audit event into the tamper-evident hash chain."""
        history = self._events_by_approval.setdefault(approval_id, [])
        seq = len(history) + 1
        prev_hash = history[-1].event_hash if history else GENESIS_HASH

        # Sanitize metadata to prevent secret leakage
        clean_metadata: dict[str, Any] = {}
        if metadata:
            for k, v in metadata.items():
                if isinstance(v, str):
                    clean_metadata[k] = redact_secrets(v)
                else:
                    clean_metadata[k] = v

        record = AuditEventRecord(
            event_id=f"evt_{uuid4().hex[:16]}",
            approval_id=approval_id,
            sequence=seq,
            actor_id=actor_id,
            action=action,
            previous_state=previous_state,
            new_state=new_state,
            reason=redact_secrets(reason) if reason else None,
            artifact_hashes=artifact_hashes or {},
            correlation_id=correlation_id,
            metadata=clean_metadata,
            previous_event_hash=prev_hash,
        )
        record.event_hash = compute_event_hash(record)

        history.append(record)
        self._all_events.append(record)
        return record

    def get_events(self, approval_id: str) -> list[AuditEventRecord]:
        """Retrieve chronological audit records for an approval request."""
        return list(self._events_by_approval.get(approval_id, []))

    def get_all_events(self) -> list[AuditEventRecord]:
        """Retrieve complete chronological log of all audit events."""
        return list(self._all_events)

    def verify_integrity(self, approval_id: str) -> tuple[bool, list[str]]:
        """Verify the cryptographic hash-chain integrity of the audit log.

        Detects tampering, missing records, altered fields, or broken chains.
        """
        records = self._events_by_approval.get(approval_id, [])
        if not records:
            return True, []

        violations: list[str] = []
        expected_prev_hash = GENESIS_HASH

        for i, rec in enumerate(records, start=1):
            # 1. Verify sequence order
            if rec.sequence != i:
                violations.append(
                    f"Sequence discontinuity at record {rec.event_id}: "
                    f"expected {i}, found {rec.sequence}."
                )

            # 2. Verify previous event link
            if rec.previous_event_hash != expected_prev_hash:
                violations.append(
                    f"Broken hash chain at record {rec.event_id} (seq {rec.sequence}): "
                    f"previous_event_hash '{rec.previous_event_hash}' does not "
                    f"match expected '{expected_prev_hash}'."
                )

            # 3. Verify record payload checksum
            recomputed = compute_event_hash(rec)
            if recomputed != rec.event_hash:
                violations.append(
                    f"Tampering detected at {rec.event_id} (seq {rec.sequence}): "
                    f"recorded hash '{rec.event_hash}' differs from "
                    f"recomputed '{recomputed}'."
                )

            expected_prev_hash = rec.event_hash

        is_valid = len(violations) == 0
        return is_valid, violations
