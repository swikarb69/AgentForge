"""Unit tests for authentication service."""

from app.auth import AuthService, login


def test_verify_token() -> None:
    auth = AuthService()
    assert auth.verify_token("secret-token") is True


def test_login() -> None:
    assert login("user1") == "secret-token"
