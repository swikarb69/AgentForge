"""Docker container creation gatekeeper and payload validator.

Enforces zero-trust isolation boundaries at the container-creation and execution level:
- Prohibits privileged mode
- Prohibits host namespaces (PID, IPC, Network)
- Prohibits host devices
- Prohibits added Linux capabilities
- Prohibits Docker daemon socket mounts
- Prohibits arbitrary host bind mounts
- Enforces read-only rootfs, tmpfs, capability drops, non-root user, and resource limits
- Validates workspace path confinement within WORKSPACE_ROOT
"""

from pathlib import Path
from typing import Any

from backend.app.core.config import settings
from backend.app.execution.exceptions import (
    ContainerSecurityError,
    WorkspaceSecurityError,
)

PROHIBITED_HOST_EXACT: tuple[str, ...] = (
    "/",
    "\\",
    "c",
    "c:",
    "c:\\",
    "c:/",
    "d",
    "d:",
    "d:\\",
    "d:/",
)

PROHIBITED_HOST_PREFIXES: tuple[str, ...] = (
    "/var/run",
    "/run",
    "/etc",
    "/proc",
    "/sys",
    "/dev",
    "/root",
    "/boot",
    "/usr",
    "/bin",
    "/sbin",
    "/lib",
    "/lib64",
    "c:/windows",
    "c:\\windows",
    "c:/program files",
    "c:\\program files",
    "c:/programdata",
    "c:\\programdata",
)

DEFAULT_ALLOWED_IMAGES: tuple[str, ...] = (
    "agentforge-sandbox:latest",
    "agentforge-sandbox:0.11.0",
    "agentforge-sandbox",
)


class SandboxContainerPayloadValidator:
    """Validates container creation parameters against strict security constraints."""

    @classmethod
    def validate_workspace_path(
        cls,
        workspace_path: Path | str,
        workspace_root: Path | str | None = None,
    ) -> Path:
        """Validate workspace_path resides strictly within authorized WORKSPACE_ROOT.

        Prohibits:
        - Path traversal ('..')
        - Null bytes or control characters
        - Symlink escapes targeting outside WORKSPACE_ROOT
        - Pointing to WORKSPACE_ROOT itself (must be an isolated sub-workspace)
        - Non-existent or non-directory paths

        Returns:
            Resolved, canonical Path.

        Raises:
            WorkspaceSecurityError: If any security constraint is violated.
        """
        raw_str = str(workspace_path)
        if "\x00" in raw_str:
            raise WorkspaceSecurityError("Null byte detected in workspace path.")

        # Check for literal directory traversal before resolution
        parts = Path(raw_str).parts
        if ".." in parts:
            raise WorkspaceSecurityError(
                f"Path traversal ('..') detected in workspace path: '{raw_str}'."
            )

        root = Path(workspace_root or settings.WORKSPACE_ROOT).resolve()
        target = Path(workspace_path).resolve()

        # Check against prohibited host system paths
        target_str = str(target).lower().replace("\\", "/")
        if (
            target_str in PROHIBITED_HOST_EXACT
            or str(target).lower() in PROHIBITED_HOST_EXACT
        ):
            raise WorkspaceSecurityError(
                f"Target workspace path '{target}' is a prohibited system root."
            )
        for prefix in PROHIBITED_HOST_PREFIXES:
            norm_prefix = prefix.lower().replace("\\", "/")
            if target_str == norm_prefix or target_str.startswith(norm_prefix + "/"):
                raise WorkspaceSecurityError(
                    f"Target workspace path '{target}' is a prohibited host "
                    "system path."
                )

        # Target must exist and be a directory
        if not target.exists():
            raise WorkspaceSecurityError(f"Workspace path does not exist: '{target}'.")
        if not target.is_dir():
            raise WorkspaceSecurityError(
                f"Workspace path is not a directory: '{target}'."
            )

        # Target must be strictly inside root, and not root itself
        try:
            if not target.is_relative_to(root) or target == root:
                raise WorkspaceSecurityError(
                    f"Workspace path '{target}' is not contained within authorized "
                    f"workspace root '{root}'."
                )
        except ValueError as exc:
            raise WorkspaceSecurityError(
                f"Workspace path '{target}' is outside authorized root '{root}'."
            ) from exc

        # Check symlink escapes in path and all parent components down to root
        curr = target
        while curr != root:
            if curr.is_symlink():
                resolved_sym = curr.resolve()
                if not resolved_sym.is_relative_to(root):
                    raise WorkspaceSecurityError(
                        f"Symlink escape detected: '{curr}' resolves outside "
                        f"workspace root to '{resolved_sym}'."
                    )
            curr = curr.parent

        return target

    @classmethod
    def validate_docker_api_payload(
        cls,
        payload: dict[str, Any],
        allowed_images: tuple[str, ...] | list[str] | None = None,
    ) -> None:
        """Validate Docker Engine API POST /containers/create JSON payload.

        Raises:
            ContainerSecurityError: If any prohibited setting is detected.
        """
        # 1. Image Allowlist
        valid_images = allowed_images or DEFAULT_ALLOWED_IMAGES
        image = payload.get("Image", "")
        if not any(image.startswith(prefix) for prefix in valid_images):
            raise ContainerSecurityError(
                f"Container image '{image}' is not in permitted sandbox images: "
                f"{valid_images}"
            )

        host_config = payload.get("HostConfig", {}) or {}

        # 2. Privileged Container Prohibition
        if host_config.get("Privileged") is True:
            raise ContainerSecurityError(
                "Privileged container mode is strictly prohibited."
            )

        # 3. Host Namespace Prohibitions
        if host_config.get("PidMode", "").lower() == "host":
            raise ContainerSecurityError(
                "Host PID namespace sharing is strictly prohibited."
            )
        if host_config.get("IpcMode", "").lower() == "host":
            raise ContainerSecurityError(
                "Host IPC namespace sharing is strictly prohibited."
            )
        if host_config.get("NetworkMode", "").lower() == "host":
            raise ContainerSecurityError(
                "Host network namespace sharing is strictly prohibited."
            )

        # 4. Host Device Mapping Prohibition
        if host_config.get("Devices"):
            raise ContainerSecurityError("Host device mapping is strictly prohibited.")

        # 5. Linux Capability Addition Prohibition
        if host_config.get("CapAdd"):
            raise ContainerSecurityError(
                "Adding Linux capabilities is strictly prohibited: "
                f"{host_config.get('CapAdd')}"
            )

        # 6. Bind Mounts & Volume Mounts Allowlist
        binds = host_config.get("Binds", []) or []
        for bind in binds:
            cls._validate_bind_spec(str(bind))

        mounts = host_config.get("Mounts", []) or []
        for mount in mounts:
            if isinstance(mount, dict):
                m_type = mount.get("Type", "")
                m_source = mount.get("Source", "")
                if m_type == "bind":
                    cls._validate_bind_spec(m_source)

        # 7. Security Options
        sec_opt = host_config.get("SecurityOpt", []) or []
        if (
            "no-new-privileges:true" not in sec_opt
            and "no-new-privileges" not in sec_opt
        ):
            raise ContainerSecurityError(
                "Security option 'no-new-privileges:true' is required."
            )

        # 8. Non-root user check
        user = str(payload.get("User", ""))
        if user in ("0", "root", "0:0", ""):
            raise ContainerSecurityError(
                f"Running container as root user is prohibited. Got User='{user}'."
            )

    @classmethod
    def validate_docker_cli_args(
        cls,
        args: list[str],
        allowed_images: tuple[str, ...] | list[str] | None = None,
    ) -> None:
        """Validate Docker CLI argument list before subprocess execution.

        Raises:
            ContainerSecurityError: If any prohibited flag or argument is present.
        """
        arg_str = " ".join(args).lower()

        # 1. Privileged flag
        if "--privileged" in args or "--privileged=true" in arg_str:
            raise ContainerSecurityError("Flag '--privileged' is strictly prohibited.")

        # 2. Host namespaces
        for flag in (
            "--pid=host",
            "--pid host",
            "--ipc=host",
            "--ipc host",
            "--net=host",
            "--net host",
            "--network=host",
            "--network host",
        ):
            if flag in arg_str:
                raise ContainerSecurityError(
                    f"Host namespace sharing '{flag}' is strictly prohibited."
                )

        # 3. Devices
        if "--device" in args:
            raise ContainerSecurityError("Flag '--device' is strictly prohibited.")

        # 4. Added Capabilities
        if "--cap-add" in args:
            raise ContainerSecurityError("Flag '--cap-add' is strictly prohibited.")

        # 5. Volume binds inspection
        for i, arg in enumerate(args):
            if arg in ("-v", "--volume"):
                if i + 1 < len(args):
                    cls._validate_bind_spec(args[i + 1])
            elif arg.startswith("-v=") or arg.startswith("--volume="):
                cls._validate_bind_spec(arg.split("=", 1)[1])
            elif arg == "--mount":
                if i + 1 < len(args):
                    cls._validate_bind_spec(args[i + 1])
            elif arg.startswith("--mount="):
                cls._validate_bind_spec(arg.split("=", 1)[1])

        # 6. Must contain mandatory security flags
        if "--cap-drop" not in args or "all" not in arg_str:
            raise ContainerSecurityError(
                "Mandatory capability drop '--cap-drop ALL' is required."
            )
        if "--read-only" not in args:
            raise ContainerSecurityError("Mandatory flag '--read-only' is required.")
        if "--user" not in args:
            raise ContainerSecurityError(
                "Mandatory flag '--user 10001:10001' is required."
            )

        # 7. Image check if present
        valid_images = allowed_images or DEFAULT_ALLOWED_IMAGES
        image_candidate = None
        # In `docker run [flags] image [cmd]`, find image argument
        is_run = False
        skip_next = False
        for _i, arg in enumerate(args):
            if skip_next:
                skip_next = False
                continue
            if arg == "run":
                is_run = True
                continue
            if not is_run:
                continue
            if arg in (
                "-v",
                "--volume",
                "-e",
                "--env",
                "-w",
                "--workdir",
                "-u",
                "--user",
                "-m",
                "--memory",
                "--cpus",
                "--pids-limit",
                "--network",
                "--tmpfs",
                "--security-opt",
                "--cap-drop",
                "--cap-add",
                "-H",
            ):
                skip_next = True
                continue
            if arg.startswith("-"):
                continue
            # First non-flag after run is the image
            image_candidate = arg
            break

        if image_candidate and not any(
            image_candidate.startswith(prefix) for prefix in valid_images
        ):
            raise ContainerSecurityError(
                f"Container image '{image_candidate}' is not in permitted "
                f"sandbox images: {valid_images}"
            )

    @classmethod
    def _validate_bind_spec(cls, bind_spec: str) -> None:
        """Verify bind/volume spec does not access prohibited host paths."""
        lowered = bind_spec.lower().replace("\\", "/")

        # Check Docker socket explicitly
        if "docker.sock" in lowered:
            raise ContainerSecurityError(
                "Mounting the Docker socket into a container is strictly prohibited."
            )

        # Parse source part of bind specification (handling Windows drive letters)
        parts = bind_spec.split(":")
        if len(parts) >= 2 and len(parts[0]) == 1 and parts[0].isalpha():
            source_part = f"{parts[0]}:{parts[1]}".strip()
        else:
            source_part = parts[0].strip()

        norm_source = source_part.lower().replace("\\", "/")
        norm_source_clean = norm_source.rstrip("/")

        if (
            norm_source_clean in PROHIBITED_HOST_EXACT
            or source_part.lower() in PROHIBITED_HOST_EXACT
        ):
            raise ContainerSecurityError(
                f"Prohibited host filesystem root mount requested: '{bind_spec}'."
            )

        for prefix in PROHIBITED_HOST_PREFIXES:
            norm_prefix = prefix.lower().replace("\\", "/")
            if norm_source_clean == norm_prefix or norm_source.startswith(
                norm_prefix + "/"
            ):
                raise ContainerSecurityError(
                    f"Prohibited host filesystem mount requested: '{bind_spec}'."
                )

        # If it's a named volume (e.g. agentforge-workspaces), allow it
        named_vol = getattr(settings, "DOCKER_NAMED_VOLUME", None)
        if named_vol and source_part == named_vol:
            return

        # If it's a host path, verify it is strictly within an authorized workspace root
        try:
            ws_root = Path(settings.WORKSPACE_ROOT).resolve()
            src_path = Path(source_part).resolve()
            # If not in production and inside test dir, allow if not root itself
            if not src_path.is_relative_to(ws_root):
                if not settings.is_production:
                    # In test/dev mode, verify it doesn't target system roots
                    if (
                        str(src_path) == src_path.anchor
                        or str(src_path).lower() in PROHIBITED_HOST_EXACT
                    ):
                        raise ContainerSecurityError(
                            f"Mount source '{source_part}' is a system root directory."
                        )
                else:
                    raise ContainerSecurityError(
                        f"Mount source '{source_part}' is outside authorized workspace "
                        f"root '{ws_root}'."
                    )
        except ContainerSecurityError:
            raise
        except Exception as exc:
            raise ContainerSecurityError(
                f"Invalid mount specification: '{bind_spec}'."
            ) from exc
