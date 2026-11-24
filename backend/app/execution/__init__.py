"""AgentForge execution and verification subsystem."""

from backend.app.execution.docker_runner import (
    BrokerDockerClient,
    CLIDockerClient,
    DockerClientInterface,
    DockerExecutionOutput,
    DockerRunner,
    MockDockerClient,
)
from backend.app.execution.exceptions import (
    ContainerSecurityError,
    ExecutionSecurityError,
    WorkspaceSecurityError,
)
from backend.app.execution.gatekeeper import SandboxContainerPayloadValidator
from backend.app.execution.models import (
    CreateExecutionRequest,
    ExecutionPolicy,
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
    PatchApplicationResult,
    ResourceUsage,
    TestSummary,
    VerificationProfile,
    VerificationReport,
    WorkspaceMetadata,
)
from backend.app.execution.patch_applier import PatchApplier
from backend.app.execution.policy import (
    FORBIDDEN_SHELL_METACHARACTERS,
    PROFILE_COMMAND_MAP,
    ExecutionPolicyEnforcer,
)
from backend.app.execution.service import ExecutionService
from backend.app.execution.test_executor import TestExecutor
from backend.app.execution.verification import VerificationEngine
from backend.app.execution.workspace import WorkspaceManager

__all__ = [
    "BrokerDockerClient",
    "CLIDockerClient",
    "ContainerSecurityError",
    "CreateExecutionRequest",
    "DockerClientInterface",
    "DockerExecutionOutput",
    "DockerRunner",
    "ExecutionPolicy",
    "ExecutionPolicyEnforcer",
    "ExecutionRequest",
    "ExecutionResult",
    "ExecutionSecurityError",
    "ExecutionService",
    "ExecutionStatus",
    "FORBIDDEN_SHELL_METACHARACTERS",
    "MockDockerClient",
    "PROFILE_COMMAND_MAP",
    "PatchApplier",
    "PatchApplicationResult",
    "ResourceUsage",
    "SandboxContainerPayloadValidator",
    "TestExecutor",
    "TestSummary",
    "VerificationEngine",
    "VerificationProfile",
    "VerificationReport",
    "WorkspaceManager",
    "WorkspaceMetadata",
    "WorkspaceSecurityError",
]
