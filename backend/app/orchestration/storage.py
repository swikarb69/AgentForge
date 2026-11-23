"""Storage interfaces, SQLite, and file persistence for orchestration."""

import json
import logging
import sqlite3
import threading
import time
import uuid
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio

from backend.app.core.config import settings
from backend.app.orchestration.context import WorkflowContext
from backend.app.orchestration.exceptions import (
    ConcurrencyConflictError,
    IdempotencyConflictError,
)
from backend.app.orchestration.models import WorkflowRun
from backend.app.orchestration.states import WorkflowStatus

logger = logging.getLogger(__name__)


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
    async def get(self, workflow_id: str) -> tuple[WorkflowRun, WorkflowContext] | None:
        """Retrieve workflow run and associated context by workflow identifier."""

    @abstractmethod
    async def get_run(self, workflow_id: str) -> WorkflowRun | None:
        """Retrieve only the workflow run record by identifier."""

    @abstractmethod
    async def get_context(self, workflow_id: str) -> WorkflowContext | None:
        """Retrieve only the workflow context record by identifier."""

    @abstractmethod
    async def get_by_idempotency_key(self, idempotency_key: str) -> WorkflowRun | None:
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

    async def get(self, workflow_id: str) -> tuple[WorkflowRun, WorkflowContext] | None:
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

    async def get_by_idempotency_key(self, idempotency_key: str) -> WorkflowRun | None:
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


class SqliteWorkflowStore(WorkflowStore):
    """Production durable SQLite-backed store for workflow persistence."""

    def __init__(
        self,
        db_path: str | Path | None = None,
        database_url: str | None = None,
    ) -> None:
        self._master_lock = anyio.Lock()
        self._locks: dict[str, anyio.Lock] = {}

        if db_path is not None:
            self.db_path = str(db_path)
        else:
            url = database_url or settings.DATABASE_URL
            if url.startswith("sqlite:///"):
                self.db_path = url[len("sqlite:///") :]
            elif url.startswith("sqlite://"):
                self.db_path = url[len("sqlite://") :]
            else:
                self.db_path = url

        if self.db_path != ":memory:":
            db_file = Path(self.db_path)
            db_file.parent.mkdir(parents=True, exist_ok=True)

        self._thread_lock = threading.Lock()
        self._conn = sqlite3.connect(
            self.db_path,
            check_same_thread=False,
            timeout=10.0,
        )
        self._conn.row_factory = sqlite3.Row
        with self._thread_lock:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA foreign_keys=ON;")
            self._init_db()

    def _init_db(self) -> None:
        """Create database tables and indices if not present."""
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS workflows (
                workflow_id TEXT PRIMARY KEY,
                version INTEGER NOT NULL,
                status TEXT NOT NULL,
                idempotency_key TEXT UNIQUE,
                run_json TEXT NOT NULL,
                context_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_wf_status
                ON workflows(status);
            CREATE INDEX IF NOT EXISTS idx_wf_idemp
                ON workflows(idempotency_key);
            CREATE INDEX IF NOT EXISTS idx_wf_created
                ON workflows(created_at DESC);

            CREATE TABLE IF NOT EXISTS stages (
                workflow_id TEXT NOT NULL,
                stage_type TEXT NOT NULL,
                status TEXT NOT NULL,
                stage_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (workflow_id, stage_type)
            );

            CREATE TABLE IF NOT EXISTS idempotency_keys (
                idempotency_key TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at REAL
            );

            CREATE TABLE IF NOT EXISTS workflow_locks (
                workflow_id TEXT PRIMARY KEY,
                owner TEXT NOT NULL,
                acquired_at REAL NOT NULL,
                expires_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                payload TEXT
            );
            """
        )
        self._conn.commit()

    def close(self) -> None:
        """Close underlying SQLite connection."""
        with self._thread_lock:
            self._conn.close()

    def _save_sync(
        self,
        run: WorkflowRun,
        context: WorkflowContext,
        expected_version: int | None = None,
    ) -> None:
        with self._thread_lock:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT version, status, idempotency_key FROM workflows "
                "WHERE workflow_id = ?",
                (run.workflow_id,),
            )
            existing_row = cur.fetchone()

            if existing_row is not None:
                existing_version = existing_row["version"]
                if (
                    expected_version is not None
                    and existing_version != expected_version
                ):
                    raise ConcurrencyConflictError(
                        f"Concurrency conflict for workflow '{run.workflow_id}': "
                        f"expected version {expected_version}, but found "
                        f"{existing_version}"
                    )
                if run.version < existing_version:
                    raise ConcurrencyConflictError(
                        f"Stale update rejected for workflow '{run.workflow_id}': "
                        f"current version {existing_version}, payload version "
                        f"{run.version}"
                    )
                run.version = existing_version + 1
            else:
                if expected_version is not None and expected_version > 1:
                    raise ConcurrencyConflictError(
                        f"Expected version {expected_version} for non-existent "
                        f"workflow '{run.workflow_id}'"
                    )

            if run.idempotency_key:
                cur.execute(
                    "SELECT workflow_id FROM idempotency_keys "
                    "WHERE idempotency_key = ?",
                    (run.idempotency_key,),
                )
                idemp_row = cur.fetchone()
                if (
                    idemp_row is not None
                    and idemp_row["workflow_id"] != run.workflow_id
                ):
                    raise IdempotencyConflictError(
                        f"Idempotency key '{run.idempotency_key}' is already "
                        f"associated with workflow '{idemp_row['workflow_id']}'"
                    )
                cur.execute(
                    "INSERT OR REPLACE INTO idempotency_keys "
                    "(idempotency_key, workflow_id, created_at) VALUES (?, ?, ?)",
                    (
                        run.idempotency_key,
                        run.workflow_id,
                        run.created_at.isoformat(),
                    ),
                )

            run_json = run.model_dump_json()
            context_json = context.model_dump_json()
            now_iso = datetime.now(UTC).isoformat()
            created_iso = run.created_at.isoformat()

            run_status_str = (
                run.status.value if hasattr(run.status, "value") else str(run.status)
            )
            cur.execute(
                "INSERT OR REPLACE INTO workflows (workflow_id, version, status, "
                "idempotency_key, run_json, context_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run.workflow_id,
                    run.version,
                    run_status_str,
                    run.idempotency_key,
                    run_json,
                    context_json,
                    created_iso,
                    now_iso,
                ),
            )

            for stype, stage_obj in run.stages.items():
                stype_str = stype.value if hasattr(stype, "value") else str(stype)
                stage_status_str = (
                    stage_obj.status.value
                    if hasattr(stage_obj.status, "value")
                    else str(stage_obj.status)
                )
                cur.execute(
                    "INSERT OR REPLACE INTO stages (workflow_id, stage_type, status, "
                    "stage_json, created_at) VALUES (?, ?, ?, ?, ?)",
                    (
                        run.workflow_id,
                        stype_str,
                        stage_status_str,
                        stage_obj.model_dump_json(),
                        now_iso,
                    ),
                )

            self._conn.commit()

    async def save(
        self,
        run: WorkflowRun,
        context: WorkflowContext,
        expected_version: int | None = None,
    ) -> None:
        async with self._master_lock:
            await anyio.to_thread.run_sync(
                self._save_sync, run, context, expected_version
            )

    def _get_sync(self, workflow_id: str) -> tuple[WorkflowRun, WorkflowContext] | None:
        with self._thread_lock:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT run_json, context_json FROM workflows WHERE workflow_id = ?",
                (workflow_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            run = WorkflowRun.model_validate_json(row["run_json"])
            ctx = WorkflowContext.model_validate_json(row["context_json"])
            return run, ctx

    async def get(self, workflow_id: str) -> tuple[WorkflowRun, WorkflowContext] | None:
        async with self._master_lock:
            return await anyio.to_thread.run_sync(self._get_sync, workflow_id)

    async def get_run(self, workflow_id: str) -> WorkflowRun | None:
        res = await self.get(workflow_id)
        return res[0] if res else None

    async def get_context(self, workflow_id: str) -> WorkflowContext | None:
        res = await self.get(workflow_id)
        return res[1] if res else None

    def _get_by_idempotency_key_sync(self, idempotency_key: str) -> WorkflowRun | None:
        with self._thread_lock:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT run_json FROM workflows WHERE idempotency_key = ?",
                (idempotency_key,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return WorkflowRun.model_validate_json(row["run_json"])

    async def get_by_idempotency_key(self, idempotency_key: str) -> WorkflowRun | None:
        async with self._master_lock:
            return await anyio.to_thread.run_sync(
                self._get_by_idempotency_key_sync, idempotency_key
            )

    def _list_sync(
        self,
        limit: int,
        offset: int,
        status: WorkflowStatus | None,
    ) -> Sequence[WorkflowRun]:
        with self._thread_lock:
            cur = self._conn.cursor()
            if status is not None:
                cur.execute(
                    "SELECT run_json FROM workflows WHERE status = ? "
                    "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (status.value, limit, offset),
                )
            else:
                cur.execute(
                    "SELECT run_json FROM workflows "
                    "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                )
            rows = cur.fetchall()
            return [WorkflowRun.model_validate_json(r["run_json"]) for r in rows]

    async def list(
        self,
        limit: int = 50,
        offset: int = 0,
        status: WorkflowStatus | None = None,
    ) -> Sequence[WorkflowRun]:
        async with self._master_lock:
            return await anyio.to_thread.run_sync(
                self._list_sync, limit, offset, status
            )

    def _count_sync(self, status: WorkflowStatus | None) -> int:
        with self._thread_lock:
            cur = self._conn.cursor()
            if status is not None:
                cur.execute(
                    "SELECT COUNT(*) FROM workflows WHERE status = ?",
                    (status.value,),
                )
            else:
                cur.execute("SELECT COUNT(*) FROM workflows")
            row = cur.fetchone()
            return int(row[0]) if row else 0

    async def count(self, status: WorkflowStatus | None = None) -> int:
        async with self._master_lock:
            return await anyio.to_thread.run_sync(self._count_sync, status)

    def _exists_sync(self, workflow_id: str) -> bool:
        with self._thread_lock:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT 1 FROM workflows WHERE workflow_id = ?",
                (workflow_id,),
            )
            return cur.fetchone() is not None

    async def exists(self, workflow_id: str) -> bool:
        async with self._master_lock:
            return await anyio.to_thread.run_sync(self._exists_sync, workflow_id)

    def _delete_sync(self, workflow_id: str) -> bool:
        with self._thread_lock:
            cur = self._conn.cursor()
            cur.execute("DELETE FROM workflows WHERE workflow_id = ?", (workflow_id,))
            deleted = cur.rowcount > 0
            cur.execute(
                "DELETE FROM idempotency_keys WHERE workflow_id = ?", (workflow_id,)
            )
            cur.execute("DELETE FROM stages WHERE workflow_id = ?", (workflow_id,))
            self._conn.commit()
            return deleted

    async def delete(self, workflow_id: str) -> bool:
        async with self._master_lock:
            return await anyio.to_thread.run_sync(self._delete_sync, workflow_id)

    def _try_acquire_db_lock(
        self, workflow_id: str, owner: str, timeout_seconds: float
    ) -> bool:
        with self._thread_lock:
            cur = self._conn.cursor()
            now = time.time()
            cur.execute(
                "SELECT owner, expires_at FROM workflow_locks WHERE workflow_id = ?",
                (workflow_id,),
            )
            row = cur.fetchone()
            if row is not None:
                if row["owner"] == owner:
                    return True
                if row["expires_at"] > now:
                    return False

            expires = now + timeout_seconds
            cur.execute(
                "INSERT OR REPLACE INTO workflow_locks (workflow_id, owner, "
                "acquired_at, expires_at) VALUES (?, ?, ?, ?)",
                (workflow_id, owner, now, expires),
            )
            self._conn.commit()
            return True

    def _release_db_lock(self, workflow_id: str, owner: str) -> None:
        with self._thread_lock:
            cur = self._conn.cursor()
            cur.execute(
                "DELETE FROM workflow_locks WHERE workflow_id = ? AND owner = ?",
                (workflow_id, owner),
            )
            self._conn.commit()

    @asynccontextmanager
    async def lock(
        self, workflow_id: str, timeout_seconds: float = 5.0
    ) -> AsyncIterator[None]:
        """Acquire distributed/cross-instance exclusive lock on workflow."""
        async with self._master_lock:
            if workflow_id not in self._locks:
                self._locks[workflow_id] = anyio.Lock()
            in_proc_lock = self._locks[workflow_id]

        owner = str(uuid.uuid4())
        deadline = time.time() + timeout_seconds

        try:
            with anyio.fail_after(timeout_seconds):
                await in_proc_lock.acquire()
        except TimeoutError as err:
            raise ConcurrencyConflictError(
                f"Timed out waiting for lock on workflow '{workflow_id}' "
                f"after {timeout_seconds}s"
            ) from err

        try:
            acquired = False
            while time.time() < deadline:
                acquired = await anyio.to_thread.run_sync(
                    self._try_acquire_db_lock, workflow_id, owner, timeout_seconds
                )
                if acquired:
                    break
                await anyio.sleep(0.02)

            if not acquired:
                raise ConcurrencyConflictError(
                    f"Timed out acquiring database lock on workflow '{workflow_id}' "
                    f"after {timeout_seconds}s"
                )

            yield
        finally:
            await anyio.to_thread.run_sync(self._release_db_lock, workflow_id, owner)
            in_proc_lock.release()


class FileWorkflowStore(WorkflowStore):
    """Durable filesystem-backed store for workflow persistence."""

    def __init__(self, directory: str | Path | None = None) -> None:
        self.base_dir = Path(directory or "./data/workflows")
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.idemp_dir = self.base_dir / "idempotency"
        self.idemp_dir.mkdir(parents=True, exist_ok=True)
        self._master_lock = anyio.Lock()
        self._locks: dict[str, anyio.Lock] = {}

    def _get_workflow_file(self, workflow_id: str) -> Path:
        return self.base_dir / f"{workflow_id}.json"

    def _get_idemp_file(self, idempotency_key: str) -> Path:
        safe_key = "".join(
            c if c.isalnum() or c in "-_" else "_" for c in idempotency_key
        )
        return self.idemp_dir / f"{safe_key}.json"

    def _save_sync(
        self,
        run: WorkflowRun,
        context: WorkflowContext,
        expected_version: int | None = None,
    ) -> None:
        target_file = self._get_workflow_file(run.workflow_id)
        if target_file.is_file():
            try:
                raw = json.loads(target_file.read_text(encoding="utf-8"))
                existing_version = raw.get("version", 1)
            except Exception:
                existing_version = 1

            if expected_version is not None and existing_version != expected_version:
                raise ConcurrencyConflictError(
                    f"Concurrency conflict for workflow '{run.workflow_id}': "
                    f"expected version {expected_version}, but found "
                    f"{existing_version}"
                )
            if run.version < existing_version:
                raise ConcurrencyConflictError(
                    f"Stale update rejected for workflow '{run.workflow_id}': "
                    f"current version {existing_version}, payload version "
                    f"{run.version}"
                )
            run.version = existing_version + 1
        else:
            if expected_version is not None and expected_version > 1:
                raise ConcurrencyConflictError(
                    f"Expected version {expected_version} for non-existent "
                    f"workflow '{run.workflow_id}'"
                )

        if run.idempotency_key:
            idemp_file = self._get_idemp_file(run.idempotency_key)
            if idemp_file.is_file():
                try:
                    mapped = json.loads(idemp_file.read_text(encoding="utf-8")).get(
                        "workflow_id"
                    )
                    if mapped and mapped != run.workflow_id:
                        raise IdempotencyConflictError(
                            f"Idempotency key '{run.idempotency_key}' is already "
                            f"associated with workflow '{mapped}'"
                        )
                except IdempotencyConflictError:
                    raise
                except (json.JSONDecodeError, OSError) as err:
                    logger.debug("Ignoring invalid idempotency record: %s", err)

            idemp_data = {
                "workflow_id": run.workflow_id,
                "created_at": run.created_at.isoformat(),
            }
            tmp_idemp = idemp_file.with_suffix(".tmp")
            tmp_idemp.write_text(json.dumps(idemp_data), encoding="utf-8")
            tmp_idemp.replace(idemp_file)

        run_status_str = (
            run.status.value if hasattr(run.status, "value") else str(run.status)
        )
        payload = {
            "version": run.version,
            "status": run_status_str,
            "idempotency_key": run.idempotency_key,
            "run_json": run.model_dump_json(),
            "context_json": context.model_dump_json(),
            "created_at": run.created_at.isoformat(),
        }
        tmp_file = target_file.with_suffix(".tmp")
        tmp_file.write_text(json.dumps(payload), encoding="utf-8")
        tmp_file.replace(target_file)

    async def save(
        self,
        run: WorkflowRun,
        context: WorkflowContext,
        expected_version: int | None = None,
    ) -> None:
        async with self._master_lock:
            await anyio.to_thread.run_sync(
                self._save_sync, run, context, expected_version
            )

    def _get_sync(self, workflow_id: str) -> tuple[WorkflowRun, WorkflowContext] | None:
        target_file = self._get_workflow_file(workflow_id)
        if not target_file.is_file():
            return None
        try:
            raw = json.loads(target_file.read_text(encoding="utf-8"))
            run = WorkflowRun.model_validate_json(raw["run_json"])
            ctx = WorkflowContext.model_validate_json(raw["context_json"])
            return run, ctx
        except Exception as err:
            raise ValueError(
                f"Failed to load corrupted workflow file '{target_file}': {err}"
            ) from err

    async def get(self, workflow_id: str) -> tuple[WorkflowRun, WorkflowContext] | None:
        async with self._master_lock:
            return await anyio.to_thread.run_sync(self._get_sync, workflow_id)

    async def get_run(self, workflow_id: str) -> WorkflowRun | None:
        res = await self.get(workflow_id)
        return res[0] if res else None

    async def get_context(self, workflow_id: str) -> WorkflowContext | None:
        res = await self.get(workflow_id)
        return res[1] if res else None

    def _get_by_idempotency_key_sync(self, idempotency_key: str) -> WorkflowRun | None:
        idemp_file = self._get_idemp_file(idempotency_key)
        if not idemp_file.is_file():
            return None
        try:
            wf_id = json.loads(idemp_file.read_text(encoding="utf-8")).get(
                "workflow_id"
            )
            if not wf_id:
                return None
            res = self._get_sync(wf_id)
            return res[0] if res else None
        except Exception:
            return None

    async def get_by_idempotency_key(self, idempotency_key: str) -> WorkflowRun | None:
        async with self._master_lock:
            return await anyio.to_thread.run_sync(
                self._get_by_idempotency_key_sync, idempotency_key
            )

    def _list_sync(
        self,
        limit: int,
        offset: int,
        status: WorkflowStatus | None,
    ) -> Sequence[WorkflowRun]:
        runs: list[WorkflowRun] = []
        for file in self.base_dir.glob("*.json"):
            if file.name.endswith(".tmp"):
                continue
            try:
                raw = json.loads(file.read_text(encoding="utf-8"))
                if "run_json" in raw:
                    run = WorkflowRun.model_validate_json(raw["run_json"])
                    if status is None or run.status == status:
                        runs.append(run)
            except (json.JSONDecodeError, OSError, ValueError) as err:
                logger.debug("Skipping unreadable workflow file %s: %s", file, err)
                continue
        runs.sort(key=lambda r: r.created_at, reverse=True)
        return runs[offset : offset + limit]

    async def list(
        self,
        limit: int = 50,
        offset: int = 0,
        status: WorkflowStatus | None = None,
    ) -> Sequence[WorkflowRun]:
        async with self._master_lock:
            return await anyio.to_thread.run_sync(
                self._list_sync, limit, offset, status
            )

    async def count(self, status: WorkflowStatus | None = None) -> int:
        runs = await self.list(limit=1000000, status=status)
        return len(runs)

    async def exists(self, workflow_id: str) -> bool:
        target_file = self._get_workflow_file(workflow_id)
        return target_file.is_file()

    def _delete_sync(self, workflow_id: str) -> bool:
        target_file = self._get_workflow_file(workflow_id)
        if target_file.is_file():
            try:
                raw = json.loads(target_file.read_text(encoding="utf-8"))
                idemp_key = raw.get("idempotency_key")
                if idemp_key:
                    idemp_file = self._get_idemp_file(idemp_key)
                    if idemp_file.is_file():
                        idemp_file.unlink()
            except (json.JSONDecodeError, OSError) as err:
                logger.debug("Error reading idempotency key on delete: %s", err)
            target_file.unlink()
            return True
        return False

    async def delete(self, workflow_id: str) -> bool:
        async with self._master_lock:
            return await anyio.to_thread.run_sync(self._delete_sync, workflow_id)

    @asynccontextmanager
    async def lock(
        self, workflow_id: str, timeout_seconds: float = 5.0
    ) -> AsyncIterator[None]:
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


def create_workflow_store(
    backend: str | None = None,
    **kwargs: Any,
) -> WorkflowStore:
    """Factory creating configured WorkflowStore based on backend setting."""
    selected = (backend or settings.STORAGE_BACKEND).lower().strip()
    if selected == "memory":
        return InMemoryWorkflowStore()
    if selected == "sqlite":
        return SqliteWorkflowStore(**kwargs)
    if selected == "file":
        return FileWorkflowStore(**kwargs)
    raise ValueError(
        f"Unsupported storage backend: '{selected}'. "
        "Supported: 'memory', 'sqlite', 'file'."
    )
