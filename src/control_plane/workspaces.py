from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from control_plane.domain import WorkspaceError
from control_plane.tools import validate_relative_path

SAFE_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,63}$")
SAFE_SCOPE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")
SAFE_GIT_OPTIONS = (
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "commit.gpgsign=false",
)


def safe_git_environment() -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
    }


@dataclass(frozen=True)
class IsolatedWorkspace:
    repository: Path
    path: Path
    branch: str
    base_revision: str
    task_id: str


@dataclass(frozen=True)
class RepositoryRegistration:
    scope_id: str
    path: Path
    base_revision: str = "HEAD"
    writable_paths: tuple[str, ...] = ()


class RepositoryRegistry:
    """Maps opaque workflow scope IDs to operator-controlled local repositories."""

    def __init__(self, registrations: tuple[RepositoryRegistration, ...] = ()) -> None:
        git = shutil.which("git")
        if git is None:
            raise WorkspaceError("Git CLI is not available")
        self.git = git
        self._registrations: dict[str, RepositoryRegistration] = {}
        for registration in registrations:
            self._register(registration)

    def resolve(self, scope_id: str) -> RepositoryRegistration:
        registration = self._registrations.get(scope_id)
        if registration is None:
            raise WorkspaceError("repository scope is not registered")
        return registration

    def resolve_commit(self, scope_id: str, revision: str) -> str:
        if not revision or revision.startswith("-"):
            raise WorkspaceError("revision is invalid")
        registration = self.resolve(scope_id)
        return self._git(registration.path, "rev-parse", "--verify", f"{revision}^{{commit}}")

    def branch_exists(self, scope_id: str, branch: str) -> bool:
        registration = self.resolve(scope_id)
        result = self._run_git(
            registration.path,
            "show-ref",
            "--verify",
            "--quiet",
            f"refs/heads/{branch}",
        )
        return result.returncode == 0

    def verify_revision_evidence(
        self,
        *,
        scope_id: str,
        branch: str,
        base_revision: str,
        result_revision: str,
    ) -> tuple[str, ...]:
        registration = self.resolve(scope_id)
        if not SAFE_TASK_ID.fullmatch(branch.removeprefix("codex/task-")) or not branch.startswith(
            "codex/task-"
        ):
            raise WorkspaceError("handoff branch is invalid")
        resolved_base = self.resolve_commit(scope_id, base_revision)
        if resolved_base != base_revision:
            raise WorkspaceError("handoff base revision must be an immutable commit digest")
        resolved_result = self.resolve_commit(scope_id, result_revision)
        if resolved_result != result_revision:
            raise WorkspaceError("result revision must be an immutable commit digest")
        branch_head = self._git(
            registration.path,
            "rev-parse",
            "--verify",
            f"refs/heads/{branch}^{{commit}}",
        )
        if branch_head != resolved_result:
            raise WorkspaceError("result revision is not the handoff branch head")
        ancestor = self._run_git(
            registration.path,
            "merge-base",
            "--is-ancestor",
            resolved_base,
            resolved_result,
        )
        if ancestor.returncode != 0:
            raise WorkspaceError("result revision does not descend from the handoff base")
        output = self._git(
            registration.path,
            "diff",
            "--name-only",
            resolved_base,
            resolved_result,
            "--",
        )
        changed_files = tuple(line for line in output.splitlines() if line)
        if not changed_files:
            raise WorkspaceError("handoff branch contains no implementation changes")
        for path in changed_files:
            validate_relative_path(path)
            if not any(
                path == allowed or path.startswith(f"{allowed.rstrip('/')}/")
                for allowed in registration.writable_paths
            ):
                raise WorkspaceError("handoff changed a file outside registered writable paths")
        return changed_files

    @classmethod
    def from_file(cls, config_path: Path) -> RepositoryRegistry:
        path = config_path.resolve()
        if not path.is_file() or path.stat().st_size > 65_536:
            raise WorkspaceError("repository registry file is missing or too large")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise WorkspaceError("repository registry file is invalid") from exc
        if not isinstance(payload, list):
            raise WorkspaceError("repository registry must be a list")
        registrations: list[RepositoryRegistration] = []
        allowed_fields = {"scope_id", "path", "base_revision", "writable_paths"}
        for item in payload:
            if not isinstance(item, dict) or set(item) - allowed_fields:
                raise WorkspaceError("repository registry entry has invalid fields")
            scope_id = item.get("scope_id")
            repository_path = item.get("path")
            base_revision = item.get("base_revision", "HEAD")
            writable_paths = item.get("writable_paths", [])
            if (
                not isinstance(scope_id, str)
                or not isinstance(repository_path, str)
                or not isinstance(base_revision, str)
                or not isinstance(writable_paths, list)
                or not all(isinstance(value, str) for value in writable_paths)
            ):
                raise WorkspaceError("repository registry entry has invalid values")
            registrations.append(
                RepositoryRegistration(
                    scope_id=scope_id,
                    path=Path(repository_path),
                    base_revision=base_revision,
                    writable_paths=tuple(writable_paths),
                )
            )
        return cls(tuple(registrations))

    def _register(self, registration: RepositoryRegistration) -> None:
        if not SAFE_SCOPE_ID.fullmatch(
            registration.scope_id
        ) or ".." in registration.scope_id.split("/"):
            raise WorkspaceError("repository scope ID is invalid")
        if registration.scope_id in self._registrations:
            raise WorkspaceError("repository scope ID is duplicated")
        path = registration.path.resolve()
        if not path.is_dir():
            raise WorkspaceError("registered repository does not exist")
        root = self._git(path, "rev-parse", "--show-toplevel")
        if Path(root).resolve() != path:
            raise WorkspaceError("registered path must be a Git repository root")
        if not registration.base_revision or registration.base_revision.startswith("-"):
            raise WorkspaceError("registered base revision is invalid")
        self._git(path, "rev-parse", "--verify", f"{registration.base_revision}^{{commit}}")
        executable_config = {
            "core.fsmonitor",
            "diff.external",
        }
        local_keys = self._git(path, "config", "--local", "--name-only", "--list")
        if any(
            key in executable_config or key.startswith("filter.")
            for key in local_keys.lower().splitlines()
        ):
            raise WorkspaceError("registered repository has executable Git configuration")
        for writable_path in registration.writable_paths:
            validate_relative_path(writable_path)
        self._registrations[registration.scope_id] = RepositoryRegistration(
            scope_id=registration.scope_id,
            path=path,
            base_revision=registration.base_revision,
            writable_paths=registration.writable_paths,
        )

    def _git(self, repository: Path, *arguments: str) -> str:
        result = self._run_git(repository, *arguments)
        if result.returncode != 0:
            raise WorkspaceError((result.stderr.strip() or "Git operation failed")[:1000])
        return result.stdout.strip()

    def _run_git(self, repository: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603
            [self.git, *SAFE_GIT_OPTIONS, "-C", str(repository), *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            env=safe_git_environment(),
        )


class GitWorktreeManager:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        git = shutil.which("git")
        if git is None:
            raise WorkspaceError("Git CLI is not available")
        self.git: str = git

    def create(self, *, repository: Path, base_revision: str, task_id: str) -> IsolatedWorkspace:
        if not SAFE_TASK_ID.fullmatch(task_id):
            raise WorkspaceError("task ID contains unsafe characters")
        if not base_revision or base_revision.startswith("-"):
            raise WorkspaceError("base revision is invalid")
        repository = repository.resolve()
        if not repository.is_dir():
            raise WorkspaceError("repository does not exist")
        destination = (self.root / task_id).resolve()
        if self.root not in destination.parents or destination.exists():
            raise WorkspaceError("worktree destination is unsafe or already exists")
        base = self._git(repository, "rev-parse", "--verify", f"{base_revision}^{{commit}}")
        branch = f"codex/task-{task_id}"
        self._git(repository, "worktree", "add", "-b", branch, str(destination), base)
        return IsolatedWorkspace(repository, destination, branch, base, task_id)

    def remove(self, workspace: IsolatedWorkspace, *, delete_branch: bool = False) -> None:
        destination = workspace.path.resolve()
        if self.root not in destination.parents or destination.name != workspace.task_id:
            raise WorkspaceError("refusing to remove an unscoped worktree")
        self._git(workspace.repository, "worktree", "remove", "--force", str(destination))
        if delete_branch:
            self._git(workspace.repository, "branch", "-D", workspace.branch)

    def commit(self, workspace: IsolatedWorkspace) -> str:
        status = self._git(workspace.path, "status", "--porcelain")
        if status:
            self._git(workspace.path, "add", "--all")
            self._git(
                workspace.path,
                "-c",
                "user.name=AI Control Plane",
                "-c",
                "user.email=control-plane@localhost.invalid",
                "commit",
                "-m",
                f"task {workspace.task_id}",
            )
        return self._git(workspace.path, "rev-parse", "HEAD")

    def changed_files(self, workspace: IsolatedWorkspace, revision: str) -> tuple[str, ...]:
        output = self._git(
            workspace.path,
            "diff",
            "--name-only",
            workspace.base_revision,
            revision,
            "--",
        )
        return tuple(line for line in output.splitlines() if line)

    def _git(self, repository: Path, *arguments: str) -> str:
        # Arguments remain distinct and are validated by the caller; no shell is involved.
        result: subprocess.CompletedProcess[str] = subprocess.run(  # noqa: S603
            [self.git, *SAFE_GIT_OPTIONS, "-C", str(repository), *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            env=safe_git_environment(),
        )
        if result.returncode != 0:
            message = result.stderr.strip() or "Git operation failed"
            raise WorkspaceError(message[:1000])
        return result.stdout.strip()
