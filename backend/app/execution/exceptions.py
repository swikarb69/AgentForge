"""Security and runtime exceptions for the isolated execution engine."""


class ExecutionSecurityError(Exception):
    """Raised when an execution operation violates sandbox security boundaries."""


class WorkspaceSecurityError(ExecutionSecurityError):
    """Raised when workspace path escapes authorized root or fails validation."""


class ContainerSecurityError(ExecutionSecurityError):
    """Raised when container configuration violates sandbox isolation rules."""
