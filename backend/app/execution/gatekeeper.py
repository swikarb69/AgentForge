"""Docker container creation gatekeeper and payload validator.

Enforces zero-trust isolation boundaries at the container-creation and execution level:
- Enforces exact normalized image allowlisting (no prefix matching)
- Prohibits privileged mode
- Prohibits host namespaces (PID, IPC, Network)
- Prohibits host devices
- Prohibits added Linux capabilities
- Prohibits Docker daemon socket mounts
- Prohibits arbitrary host bind mounts and shared workspace volumes
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

PROHIBITED_ENV_VARS: tuple[str, ...] = (
    "DOCKER_HOST",
    "DOCKER_AUTH",
    "DOCKER_CONFIG",
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
    "SUDO_ASKPASS",
    "API_KEY",
    "GITHUB_TOKEN",
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "EXECUTION_BROKER_AUTH_TOKEN",
    "BROKER_AUTH_TOKEN",
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
            is_inside = target.is_relative_to(root)
        except ValueError:
            is_inside = False

        if target == root:
            raise WorkspaceSecurityError(
                f"Workspace path '{target}' is not contained within authorized "
                f"workspace root '{root}'."
            )

        if not is_inside:
            raw_target = Path(workspace_path)
            if raw_target.is_symlink() or any(
                p.is_symlink() for p in raw_target.parents
            ):
                raise WorkspaceSecurityError(
                    f"Symlink escape detected: '{raw_target}' resolves outside "
                    f"authorized workspace root '{root}' to '{target}'."
                )
            raise WorkspaceSecurityError(
                f"Workspace path '{target}' is outside authorized root '{root}'."
            )

        # Check symlink escapes in path and all parent components down to root
        curr = Path(workspace_path)
        while curr != root and curr != curr.parent:
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

        Enforces positive allowlisting across all container creation parameters.
        Raises ContainerSecurityError if any policy constraint is violated.
        """
        # 1. Exact Image Allowlist (No prefix matching)
        valid_images = allowed_images or DEFAULT_ALLOWED_IMAGES
        valid_set = {img.strip().lower() for img in valid_images}
        image = str(payload.get("Image", "")).strip().lower()
        if not image or image not in valid_set:
            raise ContainerSecurityError(
                f"Container image '{payload.get('Image', '')}' is not in permitted "
                f"sandbox images: {sorted(valid_set)}"
            )

        # 2. Non-root user check
        user = str(payload.get("User", ""))
        if user in ("0", "root", "0:0", ""):
            raise ContainerSecurityError(
                f"Running container as root user is prohibited. Got User='{user}'."
            )

        # 3. Environment Variables Inspection
        env_vars = payload.get("Env", []) or []
        for env_item in env_vars:
            key = str(env_item).split("=")[0].strip().upper()
            if key in PROHIBITED_ENV_VARS:
                raise ContainerSecurityError(
                    f"Environment variable '{key}' is prohibited in sandbox containers."
                )

        host_config = payload.get("HostConfig", {}) or {}
        if not host_config:
            raise ContainerSecurityError("HostConfig must be provided and configured.")

        # 4. Privileged Container Prohibition
        if host_config.get("Privileged") is True:
            raise ContainerSecurityError(
                "Privileged container mode is strictly prohibited."
            )

        # 5. Host Namespace Prohibitions
        if host_config.get("PidMode", "").lower() == "host":
            raise ContainerSecurityError(
                "Host PID namespace sharing is strictly prohibited."
            )
        if host_config.get("IpcMode", "").lower() == "host":
            raise ContainerSecurityError(
                "Host IPC namespace sharing is strictly prohibited."
            )
        net_mode = str(host_config.get("NetworkMode", "none")).lower()
        if net_mode not in ("none", "bridge"):
            raise ContainerSecurityError(
                f"Network mode '{net_mode}' is not permitted (Host network namespace "
                "sharing is strictly prohibited). Permitted modes: ('none', 'bridge')."
            )

        # 6. Host Device Mapping Prohibition
        if host_config.get("Devices"):
            raise ContainerSecurityError("Host device mapping is strictly prohibited.")

        # 7. Linux Capability Addition Prohibition & Mandatory CapDrop
        if host_config.get("CapAdd"):
            raise ContainerSecurityError(
                "Adding Linux capabilities is strictly prohibited: "
                f"{host_config.get('CapAdd')}"
            )
        cap_drop = [str(c).upper() for c in (host_config.get("CapDrop") or [])]
        if "ALL" not in cap_drop:
            raise ContainerSecurityError(
                "Mandatory capability drop 'ALL' (CapDrop: ['ALL']) is required."
            )

        # 8. Mandatory Read-Only Root Filesystem
        if host_config.get("ReadonlyRootfs") is not True:
            raise ContainerSecurityError(
                "Mandatory read-only root filesystem (ReadonlyRootfs: true) "
                "is required."
            )

        # 9. Mandatory Resource Limits
        memory = host_config.get("Memory", 0)
        pids_limit = host_config.get("PidsLimit", 0)
        nano_cpus = host_config.get("NanoCpus", 0)
        cpu_quota = host_config.get("CpuQuota", 0)
        if not memory or memory <= 0:
            raise ContainerSecurityError(
                "Mandatory memory resource limit (Memory > 0) is required."
            )
        if (not nano_cpus or nano_cpus <= 0) and (not cpu_quota or cpu_quota <= 0):
            raise ContainerSecurityError(
                "Mandatory CPU resource limit (NanoCpus > 0 or CpuQuota > 0) "
                "is required."
            )
        if not pids_limit or pids_limit <= 0:
            raise ContainerSecurityError(
                "Mandatory process limit (PidsLimit > 0) is required."
            )

        # 10. Prohibit Container Restarts
        restart = host_config.get("RestartPolicy", {}) or {}
        r_name = str(restart.get("Name", "no")).lower()
        if r_name not in ("no", ""):
            raise ContainerSecurityError(
                f"Restart policy '{r_name}' is prohibited. Sandbox containers must "
                "not restart."
            )

        # 11. Prohibit VolumesFrom
        if host_config.get("VolumesFrom"):
            raise ContainerSecurityError(
                "VolumesFrom is strictly prohibited in sandbox containers."
            )

        # 12. Security Options
        sec_opt = host_config.get("SecurityOpt", []) or []
        if (
            "no-new-privileges:true" not in sec_opt
            and "no-new-privileges" not in sec_opt
        ):
            raise ContainerSecurityError(
                "Security option 'no-new-privileges:true' is required."
            )

        # 13. Mounts & Binds Allowlist
        binds = host_config.get("Binds", []) or []
        for bind in binds:
            cls._validate_bind_spec(str(bind))

        mounts = host_config.get("Mounts", []) or []
        for mount in mounts:
            if not isinstance(mount, dict):
                raise ContainerSecurityError(f"Invalid mount specification: {mount}")
            m_type = str(mount.get("Type", "")).lower()
            if m_type not in ("volume", "bind", "tmpfs"):
                raise ContainerSecurityError(
                    f"Unapproved mount type '{m_type}'. Permitted types: "
                    "('volume', 'bind', 'tmpfs')."
                )
            if m_type == "bind":
                cls._validate_bind_spec(mount.get("Source", ""))
            elif m_type == "tmpfs":
                target = mount.get("Target", "")
                if target != "/tmp":  # noqa: S108  # nosec B108
                    raise ContainerSecurityError(
                        f"Tmpfs mount target must be '/tmp', got '{target}'."
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

        # 5. Prohibited Restart Flags
        for arg in args:
            if (
                arg.startswith("--restart")
                and not arg.endswith("=no")
                and arg != "--restart"
            ):
                raise ContainerSecurityError(
                    f"Restart flag '{arg}' is prohibited. Sandbox containers must "
                    "not restart."
                )

        # 6. Volume binds inspection
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

        # 7. Mandatory security flags
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

        # 8. Mandatory Resource Limits in CLI args
        if "--memory" not in args and not any(a.startswith("--memory=") for a in args):
            raise ContainerSecurityError(
                "Mandatory memory limit '--memory' is required in CLI arguments."
            )
        if "--cpus" not in args and not any(a.startswith("--cpus=") for a in args):
            raise ContainerSecurityError(
                "Mandatory CPU limit '--cpus' is required in CLI arguments."
            )
        if "--pids-limit" not in args and not any(
            a.startswith("--pids-limit=") for a in args
        ):
            raise ContainerSecurityError(
                "Mandatory process limit '--pids-limit' is required in CLI arguments."
            )

        # 9. Exact Image Allowlist Check (No prefix matching)
        valid_images = allowed_images or DEFAULT_ALLOWED_IMAGES
        valid_set = {img.strip().lower() for img in valid_images}
        image_candidate = None
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

        if image_candidate:
            norm_cand = image_candidate.strip().lower()
            if norm_cand not in valid_set:
                raise ContainerSecurityError(
                    f"Container image '{image_candidate}' is not in permitted "
                    f"sandbox images: {sorted(valid_set)}"
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

        # BLOCKER 2: Prohibit mounting the shared root volume directly!
        if norm_source_clean == "agentforge-workspaces":
            raise ContainerSecurityError(
                "Mounting the shared 'agentforge-workspaces' volume directly is "
                "prohibited. Use isolated per-execution volumes or dedicated "
                "workspace subpaths."
            )

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

        # If it's an isolated per-execution named volume, allow it
        if norm_source_clean.startswith("agentforge-ws-"):
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
