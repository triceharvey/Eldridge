from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from uuid import uuid4

from control_plane.domain import SandboxError
from control_plane.tools import ToolName, ToolRequest, validate_relative_path

PINNED_IMAGE = (
    "python:3.11.13-alpine3.22@"
    "sha256:801053a2c35d91edc335c4c478f682de905611fed131007715538c7552d943db"
)
IMAGE_PATTERN = re.compile(r"^[A-Za-z0-9._/:@-]+@sha256:[0-9a-f]{64}$")


WRITE_SCRIPT = """
from pathlib import Path
import sys
root = Path('/workspace').resolve()
target = (root / sys.argv[1]).resolve()
if root not in target.parents or '.git' in target.parts:
    raise SystemExit('unsafe write path')
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(sys.argv[2], encoding='utf-8')
""".strip()

LIST_SCRIPT = """
from pathlib import Path
root = Path('/workspace')
for path in sorted(root.rglob('*')):
    if path.is_file() and '.git' not in path.parts:
        print(path.relative_to(root))
""".strip()

COMPILE_SCRIPT = """
from pathlib import Path
import sys
root = Path('/workspace').resolve()
targets = sys.argv[1:] or ['.']
for value in targets:
    target = (root / value).resolve()
    if target != root and root not in target.parents:
        raise SystemExit('unsafe compile path')
    paths = [target] if target.is_file() else sorted(target.rglob('*.py'))
    for path in paths:
        compile(path.read_text(encoding='utf-8'), str(path.relative_to(root)), 'exec')
""".strip()


def _default_user() -> str:
    uid = os.getuid()
    gid = os.getgid()
    if uid == 0:
        return "65532:65532"
    return f"{uid}:{gid}"


@dataclass(frozen=True)
class DockerSandboxPolicy:
    image: str = PINNED_IMAGE
    user: str = field(default_factory=_default_user)
    network: str = "none"
    memory: str = "512m"
    cpus: str = "1.0"
    pids_limit: int = 128
    timeout_seconds: int = 30
    output_limit_bytes: int = 1_000_000
    writable_paths: tuple[str, ...] = ()

    def validate(self) -> None:
        if not IMAGE_PATTERN.fullmatch(self.image):
            raise SandboxError("sandbox image must be pinned by sha256 digest")
        if self.network != "none":
            raise SandboxError("Phase 2 sandbox network must be disabled")
        if self.user.split(":", 1)[0] == "0":
            raise SandboxError("sandbox must not run as root")
        if self.timeout_seconds < 1 or self.output_limit_bytes < 1024:
            raise SandboxError("sandbox time and output limits must be positive")
        for path in self.writable_paths:
            validate_relative_path(path)


@dataclass(frozen=True)
class SandboxResult:
    status: str
    tool: ToolName
    exit_code: int
    stdout: str
    stderr: str
    container_name: str
    network: str
    read_only_root: bool
    commands_executed: tuple[tuple[str, ...], ...]


class DockerSandboxExecutor:
    def __init__(self, policy: DockerSandboxPolicy | None = None) -> None:
        self.policy = policy or DockerSandboxPolicy()
        self.policy.validate()
        docker = shutil.which("docker")
        if docker is None:
            raise SandboxError("Docker CLI is not available")
        self.docker: str = docker

    def execute(self, *, workspace: Path, request: ToolRequest) -> SandboxResult:
        workspace = workspace.resolve()
        if not workspace.is_dir() or "," in str(workspace) or "\n" in str(workspace):
            raise SandboxError("workspace path is invalid")
        tool_command, workspace_writable = self._command(request)
        container_name = f"control-plane-{uuid4().hex}"
        mount = f"type=bind,source={workspace},target=/workspace"
        if not workspace_writable:
            mount += ",readonly"
        command = [
            self.docker,
            "run",
            "--rm",
            "--name",
            container_name,
            "--network",
            self.policy.network,
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--pids-limit",
            str(self.policy.pids_limit),
            "--memory",
            self.policy.memory,
            "--cpus",
            self.policy.cpus,
            "--user",
            self.policy.user,
            "--mount",
            mount,
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",  # noqa: S108 - path is inside the container
            "--workdir",
            "/workspace",
            self.policy.image,
            *tool_command,
        ]
        try:
            # Every executable and option is selected by the typed registry; no shell is used.
            completed: subprocess.CompletedProcess[bytes] = subprocess.run(  # noqa: S603
                command,
                check=False,
                capture_output=True,
                timeout=self.policy.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            self._terminate(container_name)
            raise SandboxError("sandbox exceeded its execution timeout") from exc
        stdout = self._bounded(completed.stdout)
        stderr = self._bounded(completed.stderr)
        return SandboxResult(
            status="SUCCEEDED" if completed.returncode == 0 else "FAILED",
            tool=request.name,
            exit_code=completed.returncode,
            stdout=stdout,
            stderr=stderr,
            container_name=container_name,
            network=self.policy.network,
            read_only_root=True,
            commands_executed=(tuple(tool_command),),
        )

    def _command(self, request: ToolRequest) -> tuple[tuple[str, ...], bool]:
        if request.name == ToolName.LIST_FILES:
            return ("python", "-I", "-c", LIST_SCRIPT), False
        if request.name == ToolName.PYTHON_COMPILE:
            targets = request.targets or (".",)
            return ("python", "-I", "-c", COMPILE_SCRIPT, *targets), False
        if request.name == ToolName.WRITE_TEXT_FILE:
            assert request.path is not None and request.content is not None
            if not self._path_is_writable(request.path):
                raise SandboxError("write path is outside the task grant")
            return ("python", "-I", "-c", WRITE_SCRIPT, request.path, request.content), True
        raise SandboxError("tool is not implemented")

    def _path_is_writable(self, requested: str) -> bool:
        path = validate_relative_path(requested)
        for allowed_value in self.policy.writable_paths:
            allowed = PurePosixPath(allowed_value)
            if path == allowed or allowed in path.parents:
                return True
        return False

    def _bounded(self, value: bytes) -> str:
        if len(value) > self.policy.output_limit_bytes:
            value = value[: self.policy.output_limit_bytes] + b"\n[output truncated]"
        return value.decode("utf-8", errors="replace")

    def _terminate(self, container_name: str) -> None:
        subprocess.run(  # noqa: S603 - name is an internally generated UUID
            [self.docker, "kill", container_name],
            check=False,
            capture_output=True,
            timeout=10,
        )
        subprocess.run(  # noqa: S603 - name is an internally generated UUID
            [self.docker, "rm", "-f", container_name],
            check=False,
            capture_output=True,
            timeout=10,
        )
