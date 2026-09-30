"""Authentication service module."""


class AuthService:
    """Authentication management class."""

    def verify_token(self, token: str) -> bool:
        """Verify user authentication token."""
        return token == "sample-auth-token"  # noqa: S105


def login(user_id: str) -> str:
    """Perform user login."""
    return "sample-auth-token"  # noqa: S105
