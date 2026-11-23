"""Storage interfaces and in-memory persistence for orchestration engine."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

import anyio

from backend.app.orchestration.context import WorkflowContext
from backend.app.orchestration.exceptions import (
    ConcurrencyConflictError,
    IdempotencyConflictError,
)
from backend.app.orchestration.models import WorkflowRun
from backend.app.orchestration.states import WorkflowStatus


class WorkflowStore(ABC):
    """Abstract persistence interface for workflow runs and runtime contexts."""

    @abstractmethod
    async def save(
        self,
        run: WorkflowRun,
        context: WorkflowContext,
        expected_version: int | None = None,
    ) -> None:
        """Persist or update workflow run and context with optimistic locking."""

    @abstractmethod
    async def get(
        self, workflow_id: str
    ) -> tuple[WorkflowRun, WorkflowContext] | None:
        """Retrieve workflow run and associated context by workflow identifier."""

    @abstractmethod
    async def get_run(self, workflow_id: str) -> WorkflowRun | None:
        """Retrieve only the workflow run record by identifier."""

    @abstractmethod
    async def get_context(self, workflow_id: str) -> WorkflowContext | None:
        """Retrieve only the workflow context record by identifier."""

    @abstractmethod
    async def get_by_idempotency_key(
        self, idempotency_key: str
    ) -> WorkflowRun | None:
        """Retrieve workflow run associated with an idempotency key."""

    @abstractmethod
    async def list(
        self,
        limit: int = 50,
        offset: int = 0,
        status: WorkflowStatus | None = None,
    ) -> Sequence[WorkflowRun]:
        """List workflow runs with optional pagination and status filter."""

    @abstractmethod
    async def count(self, status: WorkflowStatus | None = None) -> int:
        """Count total workflow runs with optional status filter."""

    @abstractmethod
    async def exists(self, workflow_id: str) -> bool:
        """Check if a workflow exists by identifier."""

    @abstractmethod
    async def delete(self, workflow_id: str) -> bool:
        """Delete workflow run and context by identifier."""

    @abstractmethod
    @asynccontextmanager
    async def lock(
        self, workflow_id: str, timeout_seconds: float = 5.0
    ) -> AsyncIterator[None]:
        """Acquire an exclusive lock for modifying a workflow run."""
        yield


class InMemoryWorkflowStore(WorkflowStore):
    """Thread-safe and AnyIO-compatible in-memory store for workflow persistence."""

    def __init__(self) -> None:
        self._runs: dict[str, WorkflowRun] = {}
        self._contexts: dict[str, WorkflowContext] = {}
        self._idempotency_index: dict[str, str] = {}
        self._locks: dict[str, anyio.Lock] = {}
        self._master_lock = anyio.Lock()

    async def save(
        self,
        run: WorkflowRun,
        context: WorkflowContext,
        expected_version: int | None = None,
    ) -> None:
        """Persist workflow run and context with optimistic locking and idempotency."""
        async with self._master_lock:
            existing = self._runs.get(run.workflow_id)

            if existing is not None:
                # Concurrency check
                if (
                    expected_version is not None
                    and existing.version != expected_version
                ):
                    raise ConcurrencyConflictError(
                        f"Concurrency conflict for workflow '{run.workflow_id}': "
                        f"expected version {expected_version}, but found "
                        f"{existing.version}"
                    )
                if run.version < existing.version:
                    raise ConcurrencyConflictError(
                        f"Stale update rejected for workflow '{run.workflow_id}': "
                        f"current version {existing.version}, payload version "
                        f"{run.version}"
                    )
                run.version = existing.version + 1
            else:
                if expected_version is not None and expected_version > 1:
                    raise ConcurrencyConflictError(
                        f"Expected version {expected_version} for non-existent "
                        f"workflow '{run.workflow_id}'"
                    )

            # Idempotency validation
            if run.idempotency_key:
                mapped_wf = self._idempotency_index.get(run.idempotency_key)
                if mapped_wf is not None and mapped_wf != run.workflow_id:
                    raise IdempotencyConflictError(
                        f"Idempotency key '{run.idempotency_key}' is already "
                        f"associated with workflow '{mapped_wf}'"
                    )
                self._idempotency_index[run.idempotency_key] = run.workflow_id

            self._runs[run.workflow_id] = run.model_copy(deep=True)
            self._contexts[run.workflow_id] = context.model_copy(deep=True)

    async def get(
        self, workflow_id: str
    ) -> tuple[WorkflowRun, WorkflowContext] | None:
        """Retrieve deep-copied workflow run and context by workflow identifier."""
        async with self._master_lock:
            run = self._runs.get(workflow_id)
            context = self._contexts.get(workflow_id)
            if run is None or context is None:
                return None
            return run.model_copy(deep=True), context.model_copy(deep=True)

    async def get_run(self, workflow_id: str) -> WorkflowRun | None:
        """Retrieve deep-copied workflow run by identifier."""
        async with self._master_lock:
            run = self._runs.get(workflow_id)
            return run.model_copy(deep=True) if run else None

    async def get_context(self, workflow_id: str) -> WorkflowContext | None:
        """Retrieve deep-copied workflow context by identifier."""
        async with self._master_lock:
            context = self._contexts.get(workflow_id)
            return context.model_copy(deep=True) if context else None

    async def get_by_idempotency_key(
        self, idempotency_key: str
    ) -> WorkflowRun | None:
        """Retrieve workflow run associated with idempotency key."""
        async with self._master_lock:
            wf_id = self._idempotency_index.get(idempotency_key)
            if wf_id is None:
                return None
            run = self._runs.get(wf_id)
            return run.model_copy(deep=True) if run else None

    async def list(
        self,
        limit: int = 50,
        offset: int = 0,
        status: WorkflowStatus | None = None,
    ) -> Sequence[WorkflowRun]:
        """List workflow runs with optional pagination and status filter."""
        async with self._master_lock:
            runs = list(self._runs.values())
            if status is not None:
                runs = [r for r in runs if r.status == status]
            # Sort newest first by created_at
            runs.sort(key=lambda r: r.created_at, reverse=True)
            paged = runs[offset : offset + limit]
            return [r.model_copy(deep=True) for r in paged]

    async def count(self, status: WorkflowStatus | None = None) -> int:
        """Count workflow runs matching optional status filter."""
        async with self._master_lock:
            if status is None:
                return len(self._runs)
            return sum(1 for r in self._runs.values() if r.status == status)

    async def exists(self, workflow_id: str) -> bool:
        """Check if workflow run exists."""
        async with self._master_lock:
            return workflow_id in self._runs

    async def delete(self, workflow_id: str) -> bool:
        """Delete workflow run and associated state."""
        async with self._master_lock:
            run = self._runs.pop(workflow_id, None)
            self._contexts.pop(workflow_id, None)
            self._locks.pop(workflow_id, None)
            if run and run.idempotency_key:
                self._idempotency_index.pop(run.idempotency_key, None)
            return run is not None

    @asynccontextmanager
    async def lock(
        self, workflow_id: str, timeout_seconds: float = 5.0
    ) -> AsyncIterator[None]:
        """Acquire an exclusive lock for modifying a workflow run."""
        async with self._master_lock:
            if workflow_id not in self._locks:
                self._locks[workflow_id] = anyio.Lock()
            wlock = self._locks[workflow_id]

        try:
            with anyio.fail_after(timeout_seconds):
                await wlock.acquire()
        except TimeoutError as err:
            raise ConcurrencyConflictError(
                f"Timed out waiting for lock on workflow '{workflow_id}' "
                f"after {timeout_seconds}s"
            ) from err

        try:
            yield
        finally:
            wlock.release()
