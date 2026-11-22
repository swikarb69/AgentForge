"""Identity abstraction and authentication providers for the approval subsystem."""

from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from backend.app.approval.exceptions import (
    ApprovalValidationError,
    IdentityVerificationError,
)
from backend.app.approval.models import ApprovalRole


class RepositoryPermission(StrEnum):
    """Standard repository-level permission privileges."""

    READ = "read"
    TRIAGE = "triage"
    WRITE = "write"
    APPROVE = "approve"
    RELEASE = "release"
    ADMIN = "admin"


# Permission hierarchy mapping: higher privileges encompass lower ones
PERMISSION_HIERARCHY: dict[str, set[str]] = {
    RepositoryPermission.ADMIN.value: {
        RepositoryPermission.ADMIN.value,
        RepositoryPermission.RELEASE.value,
        RepositoryPermission.APPROVE.value,
        RepositoryPermission.WRITE.value,
        RepositoryPermission.TRIAGE.value,
        RepositoryPermission.READ.value,
    },
    RepositoryPermission.RELEASE.value: {
        RepositoryPermission.RELEASE.value,
        RepositoryPermission.READ.value,
    },
    RepositoryPermission.APPROVE.value: {
        RepositoryPermission.APPROVE.value,
        RepositoryPermission.READ.value,
    },
    RepositoryPermission.WRITE.value: {
        RepositoryPermission.WRITE.value,
        RepositoryPermission.TRIAGE.value,
        RepositoryPermission.READ.value,
    },
    RepositoryPermission.TRIAGE.value: {
        RepositoryPermission.TRIAGE.value,
        RepositoryPermission.READ.value,
    },
    RepositoryPermission.READ.value: {
        RepositoryPermission.READ.value,
    },
}


class UserIdentity(BaseModel):
    """Authenticated human or service identity interacting with approvals."""

    actor_id: str = Field(description="Unique actor identifier")
    username: str = Field(description="System login or display handle")
    display_name: str = Field(description="Full human name or service title")
    email: str | None = Field(default=None, description="Contact email address")
    roles: list[ApprovalRole] = Field(
        default_factory=lambda: [ApprovalRole.REQUESTER],
        description="Assigned role classifications",
    )
    repository_permissions: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Repository-specific permission mappings",
    )
    is_authenticated: bool = Field(
        default=True, description="True if identity verification succeeded"
    )
    auth_provider: str = Field(
        default="local", description="Underlying authentication provider"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Supplementary identity metadata"
    )

    @field_validator("actor_id", "username")
    @classmethod
    def validate_non_empty(cls, value: str) -> str:
        """Ensure actor identifier and username are non-blank."""
        cleaned = value.strip()
        if not cleaned:
            raise ApprovalValidationError("Identifier must not be empty or blank")
        return cleaned

    def has_role(self, role: ApprovalRole) -> bool:
        """Check if user has specified role (ADMIN satisfies all roles)."""
        if ApprovalRole.ADMIN in self.roles:
            return True
        return role in self.roles

    def has_repo_permission(
        self, repository_id: str, required_permission: str | RepositoryPermission
    ) -> bool:
        """Verify repository-level permission with hierarchy support."""
        if ApprovalRole.ADMIN in self.roles:
            return True

        req_perm = (
            required_permission.value
            if isinstance(required_permission, RepositoryPermission)
            else str(required_permission).lower().strip()
        )

        # Combine specific repo permissions with wildcard '*' permissions
        assigned = list(self.repository_permissions.get(repository_id, []))
        assigned.extend(self.repository_permissions.get("*", []))

        # Check direct or hierarchical inclusion
        for p in assigned:
            norm_p = p.lower().strip()
            if norm_p == req_perm:
                return True
            effective_perms = PERMISSION_HIERARCHY.get(norm_p, set())
            if req_perm in effective_perms:
                return True

        return False


class IdentityProvider(ABC):
    """Abstract interface for validating and resolving actor identities."""

    @abstractmethod
    async def authenticate(self, credentials_or_token: str) -> UserIdentity:
        """Authenticate supplied token or credentials and return identity."""

    @abstractmethod
    async def get_identity(self, actor_id: str) -> UserIdentity | None:
        """Look up user identity by unique actor ID."""


class LocalTestIdentityProvider(IdentityProvider):
    """Deterministic local and test identity provider.

    Used for local development, automated testing, and developer CLI workflows.
    Maintains an in-memory directory of mock and custom registered users.
    """

    def __init__(self, populate_defaults: bool = True) -> None:
        self._users: dict[str, UserIdentity] = {}
        self._tokens: dict[str, str] = {}
        if populate_defaults:
            self._seed_default_users()

    def _seed_default_users(self) -> None:
        """Seed default identities for testing and development."""
        defaults = [
            UserIdentity(
                actor_id="admin_user",
                username="admin",
                display_name="System Administrator",
                email="admin@agentforge.local",
                roles=[ApprovalRole.ADMIN],
                repository_permissions={"*": [RepositoryPermission.ADMIN.value]},
                auth_provider="local_test",
            ),
            UserIdentity(
                actor_id="lead_approver",
                username="approver_lead",
                display_name="Lead Approver",
                email="lead.approver@agentforge.local",
                roles=[ApprovalRole.APPROVER],
                repository_permissions={
                    "*": [
                        RepositoryPermission.APPROVE.value,
                        RepositoryPermission.READ.value,
                    ]
                },
                auth_provider="local_test",
            ),
            UserIdentity(
                actor_id="secondary_approver",
                username="approver_sec",
                display_name="Secondary Approver",
                email="secondary.approver@agentforge.local",
                roles=[ApprovalRole.APPROVER],
                repository_permissions={
                    "*": [
                        RepositoryPermission.APPROVE.value,
                        RepositoryPermission.READ.value,
                    ]
                },
                auth_provider="local_test",
            ),
            UserIdentity(
                actor_id="peer_reviewer",
                username="reviewer_1",
                display_name="Code Reviewer",
                email="reviewer@agentforge.local",
                roles=[ApprovalRole.REVIEWER],
                repository_permissions={
                    "*": [
                        RepositoryPermission.WRITE.value,
                        RepositoryPermission.READ.value,
                    ]
                },
                auth_provider="local_test",
            ),
            UserIdentity(
                actor_id="release_engineer",
                username="rel_ops",
                display_name="Release Operations Engineer",
                email="release.ops@agentforge.local",
                roles=[ApprovalRole.RELEASE_OPERATOR],
                repository_permissions={
                    "*": [
                        RepositoryPermission.RELEASE.value,
                        RepositoryPermission.READ.value,
                    ]
                },
                auth_provider="local_test",
            ),
            UserIdentity(
                actor_id="agent_coder",
                username="bot_coder",
                display_name="AgentForge Coding Agent",
                email="agent.coder@agentforge.internal",
                roles=[ApprovalRole.REQUESTER],
                repository_permissions={
                    "*": [
                        RepositoryPermission.WRITE.value,
                        RepositoryPermission.READ.value,
                    ]
                },
                auth_provider="local_test",
            ),
        ]
        for u in defaults:
            self.register_user(u, token=f"mock_token_{u.username}")

    def register_user(self, identity: UserIdentity, token: str | None = None) -> None:
        """Register a user identity and optional mock authentication token."""
        self._users[identity.actor_id] = identity
        if token:
            self._tokens[token] = identity.actor_id

    async def authenticate(self, credentials_or_token: str) -> UserIdentity:
        """Validate a mock token or user ID and return identity."""
        cleaned = credentials_or_token.strip()
        if not cleaned:
            raise IdentityVerificationError("Empty token or credentials supplied")

        # Check token mapping
        if cleaned in self._tokens:
            actor_id = self._tokens[cleaned]
            return self._users[actor_id]

        # Check direct actor_id lookup in local test environment
        if cleaned in self._users:
            return self._users[cleaned]

        raise IdentityVerificationError(
            "Invalid credentials or unknown actor in local identity "
            f"directory: '{cleaned}'"
        )

    async def get_identity(self, actor_id: str) -> UserIdentity | None:
        """Retrieve user identity by actor ID."""
        return self._users.get(actor_id)


class ProductionIdentityProvider(IdentityProvider):
    """Explicitly unconfigured production identity provider.

    Adheres to safety requirements by refusing to verify production identities
    until enterprise SSO, OAuth 2.0 / OIDC, or GitHub App auth is configured.
    """

    async def authenticate(self, credentials_or_token: str) -> UserIdentity:
        """Raise error indicating production authentication is not configured."""
        raise IdentityVerificationError(
            "Production identity provider is explicitly unconfigured. "
            "Deployments must configure an authentic identity boundary "
            "(such as GitHub OAuth/App or enterprise OIDC) before running."
        )

    async def get_identity(self, actor_id: str) -> UserIdentity | None:
        """Raise error indicating production authentication lookup is unconfigured."""
        raise IdentityVerificationError(
            "Production identity directory is unconfigured."
        )
