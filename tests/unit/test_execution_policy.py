"""Unit tests for execution sandbox policy enforcement."""

import pytest

from backend.app.execution.models import ExecutionPolicy, VerificationProfile
from backend.app.execution.policy import (
    FORBIDDEN_SHELL_METACHARACTERS,
    PROFILE_COMMAND_MAP,
    ExecutionPolicyEnforcer,
)


def test_profile_command_map_defined() -> None:
    """Verify standard profiles have valid mapped commands."""
    assert VerificationProfile.PYTHON_UNIT_TESTS in PROFILE_COMMAND_MAP
    assert VerificationProfile.PYTHON_QUALITY_CHECKS in PROFILE_COMMAND_MAP
    assert PROFILE_COMMAND_MAP[VerificationProfile.PYTHON_UNIT_TESTS] == [
        "pytest",
        "-q",
    ]
    assert PROFILE_COMMAND_MAP[VerificationProfile.PYTHON_QUALITY_CHECKS] == [
        "ruff",
        "check",
        ".",
    ]


def test_get_profile_command_success() -> None:
    """Verify profile command resolution for valid profiles."""
    policy = ExecutionPolicy()
    cmd1 = ExecutionPolicyEnforcer.get_profile_command(
        VerificationProfile.PYTHON_UNIT_TESTS, policy
    )
    assert cmd1 == ["pytest", "-q"]

    cmd2 = ExecutionPolicyEnforcer.get_profile_command("python-quality-checks", policy)
    assert cmd2 == ["ruff", "check", "."]


def test_get_profile_command_unknown() -> None:
    """Verify unknown profile names raise ValueError."""
    policy = ExecutionPolicy()
    with pytest.raises(ValueError, match="Unknown verification profile"):
        ExecutionPolicyEnforcer.get_profile_command("arbitrary-bash-script", policy)


def test_get_profile_command_disallowed_by_policy() -> None:
    """Verify profile disallowed by policy raises ValueError."""
    policy = ExecutionPolicy(allowed_verification_profiles=["python-unit-tests"])
    with pytest.raises(ValueError, match="not permitted by policy"):
        ExecutionPolicyEnforcer.get_profile_command(
            VerificationProfile.PYTHON_QUALITY_CHECKS, policy
        )


def test_validate_command_empty() -> None:
    """Verify empty command list is rejected."""
    policy = ExecutionPolicy()
    with pytest.raises(ValueError, match="cannot be empty"):
        ExecutionPolicyEnforcer.validate_command([], policy)


def test_validate_command_allowed() -> None:
    """Verify permitted executables pass validation."""
    policy = ExecutionPolicy()
    ExecutionPolicyEnforcer.validate_command(["pytest", "-q"], policy)
    ExecutionPolicyEnforcer.validate_command(["python", "-m", "pytest"], policy)
    ExecutionPolicyEnforcer.validate_command(
        ["/usr/local/bin/pytest", "-k", "unit"], policy
    )
    ExecutionPolicyEnforcer.validate_command(["ruff", "check", "backend/"], policy)


def test_validate_command_disallowed_executable() -> None:
    """Verify unapproved executables are rejected."""
    policy = ExecutionPolicy()
    with pytest.raises(ValueError, match="not in allowed commands"):
        ExecutionPolicyEnforcer.validate_command(["bash", "-c", "echo hello"], policy)

    with pytest.raises(ValueError, match="not in allowed commands"):
        ExecutionPolicyEnforcer.validate_command(
            ["curl", "https://example.com"], policy
        )

    with pytest.raises(ValueError, match="not in allowed commands"):
        ExecutionPolicyEnforcer.validate_command(["sh", "run.sh"], policy)


@pytest.mark.parametrize("meta", FORBIDDEN_SHELL_METACHARACTERS)
def test_validate_command_forbidden_shell_characters(meta: str) -> None:
    """Verify shell injection metacharacters in arguments are blocked."""
    policy = ExecutionPolicy()
    with pytest.raises(ValueError, match="forbidden shell metacharacter"):
        ExecutionPolicyEnforcer.validate_command(
            ["pytest", f"arg{meta}injection"], policy
        )


def test_validate_command_denied_patterns() -> None:
    """Verify dangerous command patterns are blocked even inside arguments."""
    policy = ExecutionPolicy()
    with pytest.raises(ValueError, match="denied security pattern"):
        ExecutionPolicyEnforcer.validate_command(
            ["python", "-c", "import os; os.system('sudo ls')"], policy
        )

    with pytest.raises(ValueError, match="denied security pattern"):
        ExecutionPolicyEnforcer.validate_command(["python", "-c", "rm -rf /"], policy)


def test_sanitize_environment_default() -> None:
    """Verify empty/none environment returns safe python defaults."""
    policy = ExecutionPolicy()
    env = ExecutionPolicyEnforcer.sanitize_environment(None, policy)
    assert env == {
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def test_sanitize_environment_filters() -> None:
    """Verify allowed variables are retained and disallowed/secrets stripped."""
    policy = ExecutionPolicy()
    raw_env = {
        "PYTHONPATH": "/sandbox/workspace",
        "PATH": "/usr/local/bin:/usr/bin",
        "RANDOM_UNAPPROVED_VAR": "forbidden",
        "GITHUB_TOKEN": "ghp_secrettoken123",
        "DATABASE_PASSWORD": "supersecretpassword",
        "AWS_SECRET_ACCESS_KEY": "akiahidden",
    }
    sanitized = ExecutionPolicyEnforcer.sanitize_environment(raw_env, policy)

    assert "PYTHONPATH" in sanitized
    assert "PATH" in sanitized
    assert sanitized["PYTHONPATH"] == "/sandbox/workspace"
    assert "RANDOM_UNAPPROVED_VAR" not in sanitized
    assert "GITHUB_TOKEN" not in sanitized
    assert "DATABASE_PASSWORD" not in sanitized
    assert "AWS_SECRET_ACCESS_KEY" not in sanitized
    assert sanitized["PYTHONUNBUFFERED"] == "1"


def test_validate_policy_bounds() -> None:
    """Verify policy bounds validation detects invalid thresholds."""
    policy = ExecutionPolicy()
    assert ExecutionPolicyEnforcer.validate_policy_bounds(policy) == []

    # Construct object bypassing pydantic constructor validation to test method logic
    class MockPolicy:
        timeout_seconds = 0
        cpu_limit = 0.0
        process_limit = 500
        maximum_output_bytes = 100

    errors = ExecutionPolicyEnforcer.validate_policy_bounds(MockPolicy())  # type: ignore[arg-type]
    assert len(errors) == 4
    assert any("Timeout" in e for e in errors)
    assert any("CPU limit" in e for e in errors)
    assert any("Process limit" in e for e in errors)
    assert any("Output limit" in e for e in errors)
