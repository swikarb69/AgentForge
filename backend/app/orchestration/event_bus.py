"""In-process asynchronous event bus for workflow telemetry and audit history."""

import logging
from collections.abc import Awaitable, Callable, Sequence

import anyio

from backend.app.orchestration.events import WorkflowEvent, WorkflowEventType

logger = logging.getLogger(__name__)

EventHandler = Callable[[WorkflowEvent], Awaitable[None]]


class WorkflowEventBus:
    """Asynchronous publish-subscribe event dispatcher with audit log buffer."""

    def __init__(self, max_history_size: int = 1000) -> None:
        self._subscribers: list[tuple[EventHandler, set[WorkflowEventType] | None]] = []
        self._history: list[WorkflowEvent] = []
        self._max_history_size = max_history_size
        self._lock = anyio.Lock()

    def subscribe(
        self,
        handler: EventHandler,
        event_types: set[WorkflowEventType] | None = None,
    ) -> None:
        """Register a callback handler for all or specific event types."""
        self._subscribers.append((handler, event_types))

    def unsubscribe(self, handler: EventHandler) -> bool:
        """Remove a previously registered handler callback."""
        initial_len = len(self._subscribers)
        self._subscribers = [
            (h, t) for (h, t) in self._subscribers if h != handler
        ]
        return len(self._subscribers) < initial_len

    async def publish(self, event: WorkflowEvent) -> None:
        """Record and broadcast an event to all matching registered subscribers."""
        async with self._lock:
            self._history.append(event)
            if len(self._history) > self._max_history_size:
                self._history.pop(0)

        for handler, types in list(self._subscribers):
            if types is None or event.event_type in types:
                try:
                    await handler(event)
                except Exception as exc:
                    logger.warning(
                        "Subscriber %s failed processing event %s: %s",
                        getattr(handler, "__name__", repr(handler)),
                        event.event_type.value,
                        exc,
                    )

    async def get_events(
        self,
        workflow_id: str | None = None,
        event_types: set[WorkflowEventType] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[WorkflowEvent]:
        """Query recorded event history with filtering and pagination."""
        async with self._lock:
            filtered = self._history
            if workflow_id is not None:
                filtered = [e for e in filtered if e.workflow_id == workflow_id]
            if event_types is not None:
                filtered = [e for e in filtered if e.event_type in event_types]
            # Deep copies for isolation
            paged = filtered[offset : offset + limit]
            return [e.model_copy() for e in paged]

    async def clear_history(self) -> None:
        """Clear the in-memory event audit buffer."""
        async with self._lock:
            self._history.clear()
