"""Debugging service providing API orchestration and session management."""

from pathlib import Path

from backend.app.debugging.models import (
    RepairAttempt,
    RepairRequest,
    RepairResult,
)
from backend.app.debugging.repair_controller import BoundedRepairController
from backend.app.execution.service import ExecutionService
from backend.app.generation.service import GenerationService
from backend.app.planning.models import ImplementationPlan


class DebuggingService:
    """Coordinates automated debugging, repair lifecycle, and state query API."""

    def __init__(
        self,
        controller: BoundedRepairController | None = None,
        execution_service: ExecutionService | None = None,
        generation_service: GenerationService | None = None,
    ) -> None:
        self.execution_service = execution_service or ExecutionService()
        self.generation_service = generation_service
        self.controller = controller or BoundedRepairController(
            execution_service=self.execution_service
        )
        self._repairs: dict[str, RepairResult] = {}

    def get_repair(self, repair_id: str) -> RepairResult | None:
        """Retrieve stored repair result by ID."""
        return self._repairs.get(repair_id)

    def list_repairs(self) -> list[RepairResult]:
        """List all stored repair run results."""
        return list(self._repairs.values())

    def get_repair_history(self, repair_id: str) -> list[RepairAttempt] | None:
        """Retrieve attempt history and audit transitions for a repair run."""
        repair = self._repairs.get(repair_id)
        if not repair:
            return None
        return repair.attempts

    async def start_repair(
        self,
        request: RepairRequest,
        plan: ImplementationPlan | None = None,
    ) -> RepairResult:
        """Initiate bounded automated repair session for a failed execution."""
        repo_path = Path(request.repository_path).resolve()
        if not repo_path.exists() or not repo_path.is_dir():
            raise ValueError(f"Repository directory does not exist: {repo_path}")

        initial_report = self.execution_service.get_report(request.execution_id)
        if not initial_report:
            raise ValueError(
                f"Execution report '{request.execution_id}' not found in "
                "execution service."
            )

        result = await self.controller.run_repair(
            request=request,
            initial_report=initial_report,
            plan=plan,
        )

        self._repairs[result.repair_id] = result
        return result
