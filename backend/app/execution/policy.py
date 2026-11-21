"""Security and execution policy enforcement for the execution sandbox."""

from backend.app.execution.models import ExecutionPolicy, VerificationProfile

# Forbidden shell metacharacters and operators that could indicate injection
FORBIDDEN_SHELL_METACHARACTERS = (
    ";",
    "&&",
    "||",
    "|",
    "`",
    "$(",
    "${",
    ">",
    "<",
    "\n",
    "\r",
)

# Predefined profile command mappings
PROFILE_COMMAND_MAP: dict[VerificationProfile, list[str]] = {
    VerificationProfile.PYTHON_UNIT_TESTS: ["pytest", "-q"],
    VerificationProfile.PYTHON_QUALITY_CHECKS: ["ruff", "check", "."],
}

# Sensitive environment key substrings
SENSITIVE_ENV_SUBSTRINGS = (
    "SECRET",
    "TOKEN",
    "KEY",
    "PASSWORD",
    "PASSWD",
    "CREDENTIAL",
    "AUTH",
    "PRIVATE",
)


class ExecutionPolicyEnforcer:
    """Validates and enforces security constraints for sandbox execution."""

    @classmethod
    def get_profile_command(
        cls,
        profile: VerificationProfile | str,
        policy: ExecutionPolicy,
    ) -> list[str]:
        """Resolve a predefined verification profile to a fixed argument list.

        Raises:
            ValueError: If profile is unknown, not allowlisted, or arbitrary.
        """
        if isinstance(profile, str):
            try:
                profile_enum = VerificationProfile(profile)
            except ValueError as err:
                raise ValueError(
                    f"Unknown verification profile: '{profile}'. "
                    f"Allowed profiles: {[p.value for p in VerificationProfile]}"
                ) from err
        else:
            profile_enum = profile

        if profile_enum.value not in policy.allowed_verification_profiles:
            raise ValueError(
                f"Verification profile '{profile_enum.value}' "
                "is not permitted by policy."
            )

        cmd = PROFILE_COMMAND_MAP.get(profile_enum)
        if not cmd:
            raise ValueError(
                f"No command mapping configured for profile '{profile_enum}'."
            )

        # Validate resulting command against policy
        cls.validate_command(cmd, policy)
        return list(cmd)

    @classmethod
    def validate_command(
        cls,
        command: list[str],
        policy: ExecutionPolicy,
    ) -> None:
        """Validate an executable command and arguments against security policies.

        Raises:
            ValueError: If the command violates allowlist, contains forbidden patterns,
                        or contains shell injection operators.
        """
        if not command:
            raise ValueError("Command list cannot be empty.")

        executable = command[0].strip()
        base_executable = executable.split("/")[-1].split("\\")[-1]

        if base_executable not in policy.allowed_commands:
            raise ValueError(
                f"Command executable '{executable}' is not in allowed commands: "
                f"{policy.allowed_commands}"
            )

        full_command_str = " ".join(command)

        # Check for denied command patterns
        for pattern in policy.denied_command_patterns:
            if pattern.lower() in full_command_str.lower():
                raise ValueError(
                    f"Command matches denied security pattern: '{pattern}'"
                )

        # Check for forbidden shell metacharacters in individual arguments
        for arg in command:
            for meta in FORBIDDEN_SHELL_METACHARACTERS:
                if meta in arg:
                    raise ValueError(
                        "Command contains forbidden shell metacharacter "
                        f"'{meta}': {arg}"
                    )

    @classmethod
    def sanitize_environment(
        cls,
        env: dict[str, str] | None,
        policy: ExecutionPolicy,
    ) -> dict[str, str]:
        """Filter env vars strictly against allowlist and strip secrets."""
        if not env:
            return {
                "PYTHONUNBUFFERED": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            }

        sanitized: dict[str, str] = {}
        for key, value in env.items():
            key_upper = key.upper()

            # Must be in permitted list
            if (
                key not in policy.permitted_environment_variables
                and key_upper not in policy.permitted_environment_variables
            ):
                continue

            # Extra defense-in-depth: check if key looks like a secret
            if any(s in key_upper for s in SENSITIVE_ENV_SUBSTRINGS):
                continue

            sanitized[key] = value

        sanitized.setdefault("PYTHONUNBUFFERED", "1")
        sanitized.setdefault("PYTHONDONTWRITEBYTECODE", "1")
        return sanitized

    @classmethod
    def validate_policy_bounds(cls, policy: ExecutionPolicy) -> list[str]:
        """Validate resource policy bounds and return any configuration errors."""
        errors: list[str] = []
        if policy.timeout_seconds <= 0 or policy.timeout_seconds > 300:
            errors.append(
                "Timeout must be between 1 and 300 seconds, "
                f"got {policy.timeout_seconds}"
            )
        if policy.cpu_limit <= 0 or policy.cpu_limit > 4.0:
            errors.append(
                f"CPU limit must be between 0.1 and 4.0 cores, got {policy.cpu_limit}"
            )
        if policy.process_limit <= 0 or policy.process_limit > 256:
            errors.append(
                f"Process limit must be between 1 and 256, got {policy.process_limit}"
            )
        if (
            policy.maximum_output_bytes < 1024
            or policy.maximum_output_bytes > 1_000_000
        ):
            errors.append(
                "Output limit must be between 1KB and 1MB, "
                f"got {policy.maximum_output_bytes}"
            )
        return errors
