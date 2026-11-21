"""Execution service orchestrating isolated workspace, patch, and test verification."""

import uuid
from pathlib import Path

from backend.app.execution.docker_runner import DockerRunner
from backend.app.execution.models import (
    CreateExecutionRequest,
    ExecutionPolicy,
    ExecutionResult,
    ExecutionStatus,
    PatchApplicationResult,
    VerificationReport,
)
from backend.app.execution.patch_applier import PatchApplier
from backend.app.execution.policy import ExecutionPolicyEnforcer
from backend.app.execution.test_executor import TestExecutor
from backend.app.execution.verification import VerificationEngine
from backend.app.execution.workspace import WorkspaceManager
from backend.app.generation.service import GenerationService


class ExecutionService:
    """Orchestrates end-to-end sandbox execution and verification."""

    def __init__(
        self,
        workspace_manager: WorkspaceManager | None = None,
        patch_applier: PatchApplier | None = None,
        docker_runner: DockerRunner | None = None,
        test_executor: TestExecutor | None = None,
        generation_service: GenerationService | None = None,
    ) -> None:
        self.workspace_manager = workspace_manager or WorkspaceManager()
        self.patch_applier = patch_applier or PatchApplier()
        self.docker_runner = docker_runner or DockerRunner()
        self.test_executor = test_executor or TestExecutor(
            docker_runner=self.docker_runner
        )
        self.generation_service = generation_service
        self._reports: dict[str, VerificationReport] = {}

    def get_report(self, execution_id: str) -> VerificationReport | None:
        """Retrieve stored verification report by ID."""
        return self._reports.get(execution_id)

    def list_reports(self) -> list[VerificationReport]:
        """List all stored verification reports."""
        return list(self._reports.values())

    async def execute_and_verify(
        self,
        request: CreateExecutionRequest,
    ) -> VerificationReport:
        """Execute end-to-end sandboxed patch application and verification.

        1. Creates isolated workspace snapshot of repository
        2. Applies proposed changes or patch cleanly
        3. Executes allowed verification profile inside restricted container
        4. Synthesizes outcomes into structured VerificationReport
        5. Cleans up ephemeral workspace
        """
        execution_id = f"exec-{uuid.uuid4().hex[:12]}"
        policy = request.policy or ExecutionPolicy()

        # Validate policy bounds
        policy_errors = ExecutionPolicyEnforcer.validate_policy_bounds(policy)
        if policy_errors:
            exec_res = ExecutionResult(
                execution_id=execution_id,
                status=ExecutionStatus.REJECTED,
                exit_code=126,
                errors=policy_errors,
            )
            report = VerificationEngine.evaluate(
                PatchApplicationResult(success=False, errors=policy_errors),
                exec_res,
            )
            self._reports[execution_id] = report
            return report

        # Validate target repository exists
        repo_path = Path(request.repository_path).resolve()
        if not repo_path.exists() or not repo_path.is_dir():
            exec_res = ExecutionResult(
                execution_id=execution_id,
                status=ExecutionStatus.FAILED,
                exit_code=1,
                errors=[f"Repository path does not exist: {repo_path}"],
            )
            report = VerificationEngine.evaluate(
                PatchApplicationResult(
                    success=False,
                    errors=[f"Invalid repository directory: {repo_path}"],
                ),
                exec_res,
            )
            self._reports[execution_id] = report
            return report

        # Resolve generation result if generation_id was supplied
        gen_result = request.generation_result
        if not gen_result and request.generation_id and self.generation_service:
            gen_result = self.generation_service.get_generation(request.generation_id)

        # Create ephemeral workspace
        try:
            ws_meta = self.workspace_manager.create_workspace(repo_path)
        except Exception as ws_err:
            exec_res = ExecutionResult(
                execution_id=execution_id,
                status=ExecutionStatus.FAILED,
                exit_code=1,
                errors=[f"Workspace creation failed: {ws_err}"],
            )
            report = VerificationEngine.evaluate(
                PatchApplicationResult(
                    success=False,
                    errors=[f"Workspace isolation failed: {ws_err}"],
                ),
                exec_res,
            )
            self._reports[execution_id] = report
            return report

        try:
            # Apply patch to workspace
            patch_result = self.patch_applier.apply(
                workspace_path=ws_meta.workspace_path,
                patch_content=request.patch_content,
                generation_result=gen_result,
            )

            if not patch_result.success:
                # Patch application failed - skip test execution
                exec_res = ExecutionResult(
                    execution_id=execution_id,
                    status=ExecutionStatus.FAILED,
                    exit_code=1,
                    workspace_metadata=ws_meta,
                    errors=patch_result.errors,
                )
                report = VerificationEngine.evaluate(patch_result, exec_res)
                self._reports[execution_id] = report
                return report

            # Execute tests inside container sandbox
            exec_res = await self.test_executor.run_tests_async(
                workspace_path=ws_meta.workspace_path,
                profile=request.verification_profile,
                policy=policy,
                execution_id=execution_id,
            )
            exec_res.workspace_metadata = ws_meta

            report = VerificationEngine.evaluate(patch_result, exec_res)
            self._reports[execution_id] = report
            return report

        finally:
            # Ensure workspace is always cleaned up
            self.workspace_manager.cleanup(ws_meta)
