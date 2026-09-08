import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from control_plane.domain import WorkspaceError
from control_plane.workspaces import (
    GitWorktreeManager,
    RepositoryRegistration,
    RepositoryRegistry,
)


def _git(repository: Path, *args: str) -> str:
    git = shutil.which("git")
    assert git is not None
    result = subprocess.run(  # noqa: S603 - fixed test helper without a shell
        [git, "-C", str(repository), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    repo = tmp_path / "repository"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "control-plane-test@example.invalid")
    _git(repo, "config", "user.name", "Control Plane Test")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


def test_worktree_isolation_prevents_agents_overwriting_each_other(
    repository: Path, tmp_path: Path
) -> None:
    manager = GitWorktreeManager(tmp_path / "worktrees")
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(
                manager.create,
                repository=repository,
                base_revision="HEAD",
                task_id=task_id,
            )
            for task_id in ("agent-a", "agent-b")
        ]
        first, second = [future.result() for future in futures]
    assert first.path != second.path
    assert first.branch != second.branch
    (first.path / "agent.txt").write_text("agent-a\n", encoding="utf-8")
    assert not (second.path / "agent.txt").exists()
    manager.remove(first, delete_branch=True)
    manager.remove(second, delete_branch=True)
    assert not first.path.exists()
    assert not second.path.exists()


def test_worktree_rejects_unsafe_task_id(repository: Path, tmp_path: Path) -> None:
    manager = GitWorktreeManager(tmp_path / "worktrees")
    with pytest.raises(WorkspaceError, match="unsafe"):
        manager.create(repository=repository, base_revision="HEAD", task_id="../escape")


def test_worktree_rejects_unknown_revision(repository: Path, tmp_path: Path) -> None:
    manager = GitWorktreeManager(tmp_path / "worktrees")
    with pytest.raises(WorkspaceError, match="revision"):
        manager.create(
            repository=repository,
            base_revision="not-a-revision",
            task_id="safe-task",
        )


def test_worktree_disables_repository_hooks(repository: Path, tmp_path: Path) -> None:
    marker = tmp_path / "hook-executed"
    hook = repository / ".git" / "hooks" / "post-checkout"
    hook.write_text(f"#!/bin/sh\ntouch '{marker}'\n", encoding="utf-8")
    hook.chmod(0o755)
    manager = GitWorktreeManager(tmp_path / "worktrees")

    workspace = manager.create(repository=repository, base_revision="HEAD", task_id="safe-hook")

    assert not marker.exists()
    manager.remove(workspace, delete_branch=True)


def test_registry_rejects_executable_git_filter_configuration(
    repository: Path, tmp_path: Path
) -> None:
    _git(repository, "config", "filter.host-command.smudge", "touch should-not-run")

    with pytest.raises(WorkspaceError, match="executable Git configuration"):
        RepositoryRegistry((RepositoryRegistration(scope_id="unsafe/repository", path=repository),))
