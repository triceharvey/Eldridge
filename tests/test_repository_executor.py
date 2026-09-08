import json
import shutil
import subprocess
from pathlib import Path

import pytest

from control_plane.domain import (
    ExecutionContext,
    ProviderResult,
    SandboxError,
    TaskKind,
    WorkspaceError,
)
from control_plane.executors import FakeExecutor, IsolatedRepositoryExecutor
from control_plane.executors.docker import DockerSandboxExecutor, DockerSandboxPolicy, SandboxResult
from control_plane.service import ControlPlaneService
from control_plane.tools import ToolName, ToolRequest
from control_plane.workspaces import (
    GitWorktreeManager,
    RepositoryRegistration,
    RepositoryRegistry,
)


def git(repository: Path, *args: str) -> str:
    executable = shutil.which("git")
    assert executable is not None
    result = subprocess.run(  # noqa: S603
        [executable, "-C", str(repository), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@pytest.fixture
def registered_repository(tmp_path: Path) -> tuple[Path, RepositoryRegistry]:
    repository = tmp_path / "repository"
    repository.mkdir()
    git(repository, "init", "-q")
    git(repository, "config", "user.email", "test@example.invalid")
    git(repository, "config", "user.name", "Test")
    (repository / "src").mkdir()
    (repository / "src" / "base.py").write_text("base = True\n", encoding="utf-8")
    git(repository, "add", ".")
    git(repository, "commit", "-q", "-m", "base")
    registry = RepositoryRegistry(
        (
            RepositoryRegistration(
                scope_id="owner/repository",
                path=repository,
                writable_paths=("src",),
            ),
        )
    )
    return repository, registry


class RecordingSandbox(DockerSandboxExecutor):
    def __init__(self, policy: DockerSandboxPolicy) -> None:
        self.policy = policy

    def execute(self, *, workspace: Path, request: ToolRequest) -> SandboxResult:
        if request.name is ToolName.WRITE_TEXT_FILE:
            assert request.path is not None and request.content is not None
            if not self._path_is_writable(request.path):
                raise SandboxError("write path is outside the task grant")
            target = workspace / request.path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(request.content, encoding="utf-8")
        return SandboxResult(
            status="SUCCEEDED",
            tool=request.name,
            exit_code=0,
            stdout="ok",
            stderr="",
            container_name="test-container",
            network="none",
            read_only_root=True,
            commands_executed=((request.name.value,),),
        )


def implementation_result() -> ProviderResult:
    return ProviderResult(
        status="SUCCEEDED",
        provider="fixture",
        model="fixture-v1",
        usage={},
        output={
            "candidate_revision": "provider-claim-is-not-authoritative",
            "commands_requested": [],
            "tool_requests": [
                {
                    "name": "WRITE_TEXT_FILE",
                    "input": {
                        "name": "WRITE_TEXT_FILE",
                        "path": "src/generated.py",
                        "content": "generated = True\n",
                    },
                }
            ],
        },
    )


def test_executor_commits_typed_change_and_returns_real_revision(
    registered_repository: tuple[Path, RepositoryRegistry], tmp_path: Path
) -> None:
    repository, registry = registered_repository
    executor = IsolatedRepositoryExecutor(
        registry=registry,
        worktrees=GitWorktreeManager(tmp_path / "worktrees"),
        fallback=FakeExecutor(),
        sandbox_factory=RecordingSandbox,
    )

    result = executor.execute(
        TaskKind.IMPLEMENT,
        implementation_result(),
        ExecutionContext(
            workflow_id="workflow",
            task_id="implementation-task",
            repository_scope="owner/repository",
            candidate_revision=None,
            containment_required=False,
        ),
    )

    revision = str(result.evidence["result_revision"])
    assert revision != "provider-claim-is-not-authoritative"
    assert git(repository, "show", f"{revision}:src/generated.py") == "generated = True"
    assert result.evidence["changed_files"] == ["src/generated.py"]
    assert result.commands_executed == ("WRITE_TEXT_FILE",)
    assert not (tmp_path / "worktrees" / "implementation-task").exists()
    assert git(repository, "branch", "--list", "codex/task-implementation-task")


def test_executor_rejects_ambiguous_tool_and_removes_failed_branch(
    registered_repository: tuple[Path, RepositoryRegistry], tmp_path: Path
) -> None:
    repository, registry = registered_repository
    executor = IsolatedRepositoryExecutor(
        registry=registry,
        worktrees=GitWorktreeManager(tmp_path / "worktrees"),
        fallback=FakeExecutor(),
        sandbox_factory=RecordingSandbox,
    )
    malformed = implementation_result()
    malformed.output["tool_requests"][0]["input"]["name"] = "LIST_FILES"

    with pytest.raises(SandboxError, match="names do not match"):
        executor.execute(
            TaskKind.IMPLEMENT,
            malformed,
            ExecutionContext(
                workflow_id="workflow",
                task_id="failed-task",
                repository_scope="owner/repository",
                candidate_revision=None,
                containment_required=False,
            ),
        )

    assert not git(repository, "branch", "--list", "codex/task-failed-task")
    assert not (tmp_path / "worktrees" / "failed-task").exists()


def test_registry_file_is_operator_controlled_and_rejects_unknown_scope(
    registered_repository: tuple[Path, RepositoryRegistry], tmp_path: Path
) -> None:
    repository, _ = registered_repository
    config = tmp_path / "repositories.json"
    config.write_text(
        json.dumps(
            [
                {
                    "scope_id": "approved/repository",
                    "path": str(repository),
                    "base_revision": "HEAD",
                    "writable_paths": ["src"],
                }
            ]
        ),
        encoding="utf-8",
    )

    registry = RepositoryRegistry.from_file(config)

    assert registry.resolve("approved/repository").path == repository
    with pytest.raises(WorkspaceError, match="not registered"):
        registry.resolve(str(repository))


def test_service_binds_candidate_to_git_instead_of_provider_claim(
    registered_repository: tuple[Path, RepositoryRegistry], tmp_path: Path, session_factory
) -> None:
    repository, registry = registered_repository
    executor = IsolatedRepositoryExecutor(
        registry=registry,
        worktrees=GitWorktreeManager(tmp_path / "worktrees"),
        fallback=FakeExecutor(),
        sandbox_factory=RecordingSandbox,
    )
    service = ControlPlaneService(session_factory, executor=executor)
    workflow = service.create_workflow(
        requester_id="dev-operator",
        title="Git-bound workflow",
        description="Candidate identity comes from the registered Git repository.",
        idempotency_key="git-bound-workflow-key",
        repository_scope="owner/repository",
    )
    while workflow["state"] != "AWAITING_HUMAN_APPROVAL":
        task = service.lease_next_task(worker_id="orchestrator")
        assert task is not None
        service.execute_leased_task(
            task_id=task["id"],
            lease_token=task["lease_token"],
            worker_id="orchestrator",
        )
        workflow = service.get_workflow(str(workflow["id"]), principal_id="dev-operator")

    assert workflow["candidate_revision"] == git(repository, "rev-parse", "HEAD")
    assert not str(workflow["candidate_revision"]).startswith("mock-")


@pytest.mark.docker
def test_repository_change_runs_through_real_hardened_container(
    registered_repository: tuple[Path, RepositoryRegistry], tmp_path: Path
) -> None:
    repository, registry = registered_repository
    executor = IsolatedRepositoryExecutor(
        registry=registry,
        worktrees=GitWorktreeManager(tmp_path / "worktrees"),
        fallback=FakeExecutor(),
    )

    result = executor.execute(
        TaskKind.IMPLEMENT,
        implementation_result(),
        ExecutionContext(
            workflow_id="workflow",
            task_id="docker-implementation",
            repository_scope="owner/repository",
            candidate_revision=None,
            containment_required=False,
        ),
    )

    revision = str(result.evidence["result_revision"])
    sandbox = result.evidence["sandbox"]
    assert isinstance(sandbox, list)
    assert sandbox[0]["network"] == "none"
    assert sandbox[0]["read_only_root"] is True
    assert git(repository, "show", f"{revision}:src/generated.py") == "generated = True"
