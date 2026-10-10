import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from control_plane.api import create_app
from control_plane.domain import (
    AuthorizationError,
    ConflictError,
    IntegrationDisabledError,
    TaskStatus,
    ValidationError,
    WorkspaceError,
)
from control_plane.integrations import (
    DevinRuntime,
    RemoteAgentHandle,
    RemoteAgentRequest,
    RemoteAgentStatus,
    RemoteRunState,
)
from control_plane.persistence import Artifact, TaskAttempt
from control_plane.service import ControlPlaneService
from control_plane.workspaces import RepositoryRegistration, RepositoryRegistry

SLUG = "owner/repository"


def _git(repository: Path, *arguments: str) -> str:
    executable = shutil.which("git")
    assert executable is not None
    result = subprocess.run(  # noqa: S603
        [executable, "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@dataclass
class FakeDevin:
    """A remote runtime double: records requests and returns scripted session states."""

    status: RemoteRunState = RemoteRunState.RUNNING
    output: dict[str, object] = field(default_factory=dict)
    submit_error: Exception | None = None
    requests: list[RemoteAgentRequest] = field(default_factory=list)
    cancelled: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return "devin"

    def submit(self, request: RemoteAgentRequest) -> RemoteAgentHandle:
        self.requests.append(request)
        if self.submit_error is not None:
            raise self.submit_error
        return RemoteAgentHandle(
            provider="devin", remote_id="devin-abc123", url="https://app.devin.ai/sessions/abc123"
        )

    def poll(self, handle: RemoteAgentHandle) -> RemoteAgentStatus:
        return RemoteAgentStatus(
            handle=handle, state=self.status, raw_status=self.status.value, output=self.output
        )

    def cancel(self, handle: RemoteAgentHandle) -> bool:
        self.cancelled.append(handle.remote_id)
        return True


@dataclass
class Fixture:
    service: ControlPlaneService
    devin: FakeDevin
    local: Path
    remote: Path
    agent_clone: Path


@pytest.fixture
def devin_fixture(tmp_path: Path, session_factory: sessionmaker[Session]) -> Fixture:
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    local = tmp_path / "local"
    local.mkdir()
    _git(local, "init", "-q", "-b", "main")
    _git(local, "config", "user.email", "devin-test@example.invalid")
    _git(local, "config", "user.name", "Devin Test")
    (local / "src").mkdir()
    (local / "src" / "base.py").write_text("base = True\n", encoding="utf-8")
    (local / "docs").mkdir()
    (local / "docs" / "protected.md").write_text("protected\n", encoding="utf-8")
    _git(local, "add", ".")
    _git(local, "commit", "-q", "-m", "base")
    # The registered origin is the GitHub URL; insteadOf points it at the local bare repository.
    _git(local, "remote", "add", "origin", f"https://github.com/{SLUG}")
    _git(local, "config", f"url.{remote}.insteadOf", f"https://github.com/{SLUG}")
    _git(local, "push", "-q", "origin", "main")
    agent_clone = tmp_path / "devin-machine"
    _git(tmp_path, "clone", "-q", str(remote), str(agent_clone))
    _git(agent_clone, "config", "user.email", "devin@example.invalid")
    _git(agent_clone, "config", "user.name", "Devin")
    registry = RepositoryRegistry(
        (
            RepositoryRegistration(
                scope_id=SLUG,
                path=local,
                writable_paths=("src",),
                remote_agent_repository=SLUG,
            ),
        )
    )
    devin = FakeDevin()
    service = ControlPlaneService(
        session_factory, repository_registry=registry, remote_agent_runtime=devin
    )
    return Fixture(service, devin, local, remote, agent_clone)


def _implementation_task(service: ControlPlaneService, *, key: str) -> dict[str, object]:
    workflow = service.create_workflow(
        requester_id="dev-operator",
        title="Devin implementation",
        description="Send one implementation task to a remote Devin session.",
        idempotency_key=key,
        repository_scope=SLUG,
    )
    while workflow["state"] != "IMPLEMENTING":
        task = service.lease_next_task(worker_id="orchestrator")
        assert task is not None
        service.execute_leased_task(
            task_id=str(task["id"]),
            lease_token=str(task["lease_token"]),
            worker_id="orchestrator",
        )
        workflow = service.get_workflow(str(workflow["id"]), principal_id="dev-operator")
    task = next(item for item in workflow["tasks"] if item["kind"] == "IMPLEMENT")
    assert isinstance(task, dict)
    return task


def _devin_pushes(
    fixture: Fixture,
    *,
    base_revision: str,
    branch: str = "devin/1760140000-implement-task",
    path: str = "src/devin.py",
) -> str:
    clone = fixture.agent_clone
    _git(clone, "fetch", "-q", "origin")
    _git(clone, "checkout", "-q", "-b", branch, base_revision)
    target = clone / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("implemented_by_devin = True\n", encoding="utf-8")
    _git(clone, "add", path)
    _git(clone, "commit", "-q", "-m", "devin implementation")
    _git(clone, "push", "-q", "origin", branch)
    return _git(clone, "rev-parse", "HEAD")


def _finish(
    fixture: Fixture, *, branch: str, revision: str, overrides: dict[str, object] | None = None
) -> None:
    structured: dict[str, object] = {
        "branch": branch,
        "result_revision": revision,
        "tests_passed": True,
        "test_summary": "pytest: 12 passed",
        "summary": "Implemented the scoped change.",
    }
    structured.update(overrides or {})
    fixture.devin.status = RemoteRunState.SUCCEEDED
    fixture.devin.output = {
        "status": "running",
        "status_detail": "finished",
        "structured_output": structured,
        "pull_requests": [
            {"pr_url": "https://github.com/owner/repository/pull/7", "pr_state": "open"}
        ],
    }


def test_devin_dispatch_sync_and_git_verified_evidence(devin_fixture: Fixture) -> None:
    service = devin_fixture.service
    task = _implementation_task(service, key="devin-happy-path")
    dispatched = service.dispatch_devin_task(
        task_id=str(task["id"]), principal_id="dev-operator", max_cost_units=3
    )
    assert dispatched["task_status"] == TaskStatus.RUNNING.value
    assert dispatched["remote"]["session_id"] == "devin-abc123"
    request = devin_fixture.devin.requests[0]
    assert request.repositories == (SLUG,)
    assert request.max_cost_units == 3
    assert request.idempotency_key == dispatched["attempt_id"]
    assert request.structured_output_schema is not None
    assert "Change files only under: src" in request.objective

    devin_fixture.devin.status = RemoteRunState.AWAITING_INPUT
    waiting = service.sync_devin_task(task_id=str(task["id"]), principal_id="dev-operator")
    assert waiting["remote"]["state"] == "AWAITING_INPUT"
    assert waiting["task_status"] == TaskStatus.RUNNING.value

    with service.session_factory() as session:
        attempt = session.get(TaskAttempt, str(dispatched["attempt_id"]))
        assert attempt is not None
        base_revision = str(attempt.output["handoff"]["base_revision"])
    revision = _devin_pushes(devin_fixture, base_revision=base_revision)
    _finish(devin_fixture, branch="devin/1760140000-implement-task", revision=revision)
    result = service.sync_devin_task(task_id=str(task["id"]), principal_id="dev-operator")

    assert result["task_status"] == TaskStatus.SUCCEEDED.value
    assert result["evidence"]["files_changed"] == ["src/devin.py"]
    assert result["evidence"]["test_claim_authoritative"] is False
    assert result["evidence"]["pull_requests_reported"] == [
        "https://github.com/owner/repository/pull/7"
    ]
    workflow = service.get_workflow(str(task["workflow_id"]), principal_id="dev-operator")
    assert workflow["state"] == "TESTING"
    assert workflow["candidate_revision"] == revision
    with service.session_factory() as session:
        artifact = session.scalar(select(Artifact).where(Artifact.task_id == str(task["id"])))
        assert artifact is not None
        assert artifact.artifact_type == "DEVIN_IMPLEMENTATION_EVIDENCE"
        assert artifact.revision == revision


def test_only_humans_dispatch_devin(devin_fixture: Fixture) -> None:
    task = _implementation_task(devin_fixture.service, key="devin-agent-denied")
    for principal in ("implementer-agent", "devin-remote", "orchestrator"):
        with pytest.raises(AuthorizationError):
            devin_fixture.service.dispatch_devin_task(
                task_id=str(task["id"]), principal_id=principal, max_cost_units=1
            )
    assert devin_fixture.devin.requests == []


def test_devin_requires_activation_and_remote_registration(
    tmp_path: Path, session_factory: sessionmaker[Session], devin_fixture: Fixture
) -> None:
    disabled = ControlPlaneService(
        session_factory, repository_registry=devin_fixture.service.repository_registry
    )
    task = _implementation_task(disabled, key="devin-disabled")
    with pytest.raises(IntegrationDisabledError, match="not activated"):
        disabled.dispatch_devin_task(
            task_id=str(task["id"]), principal_id="dev-operator", max_cost_units=1
        )

    plain = RepositoryRegistry(
        (RepositoryRegistration(scope_id=SLUG, path=devin_fixture.local, writable_paths=("src",)),)
    )
    unregistered = ControlPlaneService(
        session_factory, repository_registry=plain, remote_agent_runtime=FakeDevin()
    )
    with pytest.raises(ConflictError, match="not registered for remote agents"):
        unregistered.dispatch_devin_task(
            task_id=str(task["id"]), principal_id="dev-operator", max_cost_units=1
        )


def test_changes_outside_writable_paths_are_rejected_and_retry_uses_new_branch(
    devin_fixture: Fixture,
) -> None:
    service = devin_fixture.service
    task = _implementation_task(service, key="devin-path-denied")
    dispatched = service.dispatch_devin_task(
        task_id=str(task["id"]), principal_id="dev-operator", max_cost_units=1
    )
    with service.session_factory() as session:
        attempt = session.get(TaskAttempt, str(dispatched["attempt_id"]))
        assert attempt is not None
        base_revision = str(attempt.output["handoff"]["base_revision"])
    revision = _devin_pushes(devin_fixture, base_revision=base_revision, path="docs/escaped.md")
    _finish(devin_fixture, branch="devin/1760140000-implement-task", revision=revision)

    rejected = service.sync_devin_task(task_id=str(task["id"]), principal_id="dev-operator")
    assert rejected["attempt_status"] == TaskStatus.FAILED.value
    assert rejected["task_status"] == TaskStatus.READY.value
    workflow = service.get_workflow(str(task["workflow_id"]), principal_id="dev-operator")
    assert workflow["state"] == "IMPLEMENTING"
    assert workflow["candidate_revision"] != revision

    devin_fixture.devin.status = RemoteRunState.RUNNING
    retry = service.dispatch_devin_task(
        task_id=str(task["id"]), principal_id="dev-operator", max_cost_units=1
    )
    with service.session_factory() as session:
        attempt = session.get(TaskAttempt, str(retry["attempt_id"]))
        assert attempt is not None
        assert str(attempt.output["handoff"]["requested_branch"]).endswith("-a2")


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"result_revision": "0" * 40}, "not the head"),
        ({"tests_passed": "yes"}, "invalid values"),
        ({"branch": "../main"}, "invalid values"),
        ({"summary": ""}, "invalid values"),
    ],
)
def test_untrustworthy_devin_evidence_is_rejected(
    devin_fixture: Fixture, overrides: dict[str, object], message: str
) -> None:
    service = devin_fixture.service
    task = _implementation_task(service, key=f"devin-bad-evidence-{message}-{len(str(overrides))}")
    dispatched = service.dispatch_devin_task(
        task_id=str(task["id"]), principal_id="dev-operator", max_cost_units=1
    )
    with service.session_factory() as session:
        attempt = session.get(TaskAttempt, str(dispatched["attempt_id"]))
        assert attempt is not None
        base_revision = str(attempt.output["handoff"]["base_revision"])
    revision = _devin_pushes(devin_fixture, base_revision=base_revision)
    _finish(
        devin_fixture,
        branch="devin/1760140000-implement-task",
        revision=revision,
        overrides=overrides,
    )
    result = service.sync_devin_task(task_id=str(task["id"]), principal_id="dev-operator")
    assert result["attempt_status"] == TaskStatus.FAILED.value
    with service.session_factory() as session:
        attempt = session.get(TaskAttempt, str(dispatched["attempt_id"]))
        assert attempt is not None
        assert message in str(attempt.output["error"])
        assert attempt.output["handoff"]["digest"]  # handoff survives the failure


def test_missing_structured_output_and_failed_session(devin_fixture: Fixture) -> None:
    service = devin_fixture.service
    task = _implementation_task(service, key="devin-missing-output")
    service.dispatch_devin_task(
        task_id=str(task["id"]), principal_id="dev-operator", max_cost_units=1
    )
    devin_fixture.devin.status = RemoteRunState.SUCCEEDED
    devin_fixture.devin.output = {"status": "exit"}
    first = service.sync_devin_task(task_id=str(task["id"]), principal_id="dev-operator")
    assert first["task_status"] == TaskStatus.READY.value

    devin_fixture.devin.status = RemoteRunState.RUNNING
    service.dispatch_devin_task(
        task_id=str(task["id"]), principal_id="dev-operator", max_cost_units=1
    )
    devin_fixture.devin.status = RemoteRunState.FAILED
    second = service.sync_devin_task(task_id=str(task["id"]), principal_id="dev-operator")
    assert second["task_status"] == TaskStatus.FAILED.value
    workflow = service.get_workflow(str(task["workflow_id"]), principal_id="dev-operator")
    assert workflow["state"] == "FAILED"


def test_ambiguous_dispatch_routes_to_human_reconciliation(devin_fixture: Fixture) -> None:
    service = devin_fixture.service
    task = _implementation_task(service, key="devin-ambiguous")
    devin_fixture.devin.submit_error = httpx.ReadTimeout("timed out")
    with pytest.raises(httpx.ReadTimeout):
        service.dispatch_devin_task(
            task_id=str(task["id"]), principal_id="dev-operator", max_cost_units=1
        )
    with pytest.raises(ConflictError, match="lease has expired|not confirmed"):
        service.sync_devin_task(task_id=str(task["id"]), principal_id="dev-operator")
    assert service.reclaim_expired_tasks(worker_id="orchestrator") == 1
    workflow = service.get_workflow(str(task["workflow_id"]), principal_id="dev-operator")
    assert workflow["state"] == "BLOCKED"
    assert workflow["block_reason"] == "EXECUTION_RECONCILIATION_REQUIRED"


def test_dispatch_rejected_before_contact_releases_task(devin_fixture: Fixture) -> None:
    service = devin_fixture.service
    task = _implementation_task(service, key="devin-precontact")
    devin_fixture.devin.submit_error = ValidationError("exceeds the configured cost-unit limit")
    with pytest.raises(ValidationError):
        service.dispatch_devin_task(
            task_id=str(task["id"]), principal_id="dev-operator", max_cost_units=50
        )
    workflow = service.get_workflow(str(task["workflow_id"]), principal_id="dev-operator")
    implement = next(item for item in workflow["tasks"] if item["kind"] == "IMPLEMENT")
    assert implement["status"] == TaskStatus.READY.value


def test_operator_cancel_closes_the_session(devin_fixture: Fixture) -> None:
    service = devin_fixture.service
    task = _implementation_task(service, key="devin-cancel")
    service.dispatch_devin_task(
        task_id=str(task["id"]), principal_id="dev-operator", max_cost_units=1
    )
    result = service.cancel_devin_task(task_id=str(task["id"]), principal_id="dev-operator")
    assert devin_fixture.devin.cancelled == ["devin-abc123"]
    assert result["remote"]["state"] == "CANCELLED"
    assert result["task_status"] == TaskStatus.READY.value
    with pytest.raises(ConflictError, match="running Devin session"):
        service.cancel_devin_task(task_id=str(task["id"]), principal_id="dev-operator")


def test_origin_mismatch_fails_without_changing_state(devin_fixture: Fixture) -> None:
    service = devin_fixture.service
    task = _implementation_task(service, key="devin-origin-mismatch")
    dispatched = service.dispatch_devin_task(
        task_id=str(task["id"]), principal_id="dev-operator", max_cost_units=1
    )
    with service.session_factory() as session:
        attempt = session.get(TaskAttempt, str(dispatched["attempt_id"]))
        assert attempt is not None
        base_revision = str(attempt.output["handoff"]["base_revision"])
    revision = _devin_pushes(devin_fixture, base_revision=base_revision)
    _finish(devin_fixture, branch="devin/1760140000-implement-task", revision=revision)
    _git(devin_fixture.local, "remote", "set-url", "origin", "https://github.com/attacker/fork")
    with pytest.raises(WorkspaceError, match="origin does not match"):
        service.sync_devin_task(task_id=str(task["id"]), principal_id="dev-operator")
    _git(devin_fixture.local, "remote", "set-url", "origin", f"https://github.com/{SLUG}")
    result = service.sync_devin_task(task_id=str(task["id"]), principal_id="dev-operator")
    assert result["task_status"] == TaskStatus.SUCCEEDED.value


def test_api_exposes_devin_operations(devin_fixture: Fixture) -> None:
    service = devin_fixture.service
    task = _implementation_task(service, key="devin-api")
    with TestClient(create_app(service)) as client:
        assert (
            client.post(f"/tasks/{task['id']}/devin/dispatch", json={"max_cost_units": 0})
        ).status_code == 422
        dispatched = client.post(f"/tasks/{task['id']}/devin/dispatch", json={"max_cost_units": 2})
        assert dispatched.status_code == 201
        synced = client.post(f"/tasks/{task['id']}/devin/sync")
        assert synced.json()["remote"]["state"] == "RUNNING"
        cancelled = client.post(f"/tasks/{task['id']}/devin/cancel")
        assert cancelled.json()["remote"]["state"] == "CANCELLED"


@pytest.mark.parametrize(
    ("status", "detail", "expected"),
    [
        ("running", "finished", RemoteRunState.SUCCEEDED),
        ("running", "waiting_for_user", RemoteRunState.AWAITING_INPUT),
        ("running", "waiting_for_approval", RemoteRunState.AWAITING_INPUT),
        ("running", "working", RemoteRunState.RUNNING),
        ("suspended", "out_of_credits", RemoteRunState.SUSPENDED),
        ("exit", None, RemoteRunState.SUCCEEDED),
        ("error", None, RemoteRunState.FAILED),
        ("mystery", None, RemoteRunState.UNKNOWN),
    ],
)
def test_devin_v3_status_detail_mapping(
    status: str, detail: str | None, expected: RemoteRunState
) -> None:
    assert DevinRuntime._map_state(status, detail) is expected


def test_registry_validates_remote_agent_repository(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    _git(repository, "init", "-q")
    _git(repository, "config", "user.email", "x@example.invalid")
    _git(repository, "config", "user.name", "X")
    (repository / "a.txt").write_text("a\n", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-q", "-m", "a")
    with pytest.raises(WorkspaceError, match="GitHub owner/name"):
        RepositoryRegistry(
            (
                RepositoryRegistration(
                    scope_id="s",
                    path=repository,
                    writable_paths=("src",),
                    remote_agent_repository="https://evil.example/x",
                ),
            )
        )
    with pytest.raises(WorkspaceError, match="require writable paths"):
        RepositoryRegistry(
            (RepositoryRegistration(scope_id="s", path=repository, remote_agent_repository=SLUG),)
        )
    config = tmp_path / "registry.json"
    config.write_text(
        f'[{{"scope_id": "s", "path": "{repository}", "writable_paths": ["src"],'
        f' "remote_agent_repository": "{SLUG}"}}]',
        encoding="utf-8",
    )
    assert RepositoryRegistry.from_file(config).resolve("s").remote_agent_repository == SLUG
    registry = RepositoryRegistry.from_file(config)
    with pytest.raises(WorkspaceError, match="branch name is invalid"):
        registry.fetch_remote_agent_branch("s", remote_branch="-x", local_branch="devin/task-1")
    with pytest.raises(WorkspaceError, match="local handoff branch is invalid"):
        registry.fetch_remote_agent_branch("s", remote_branch="ok", local_branch="main")
