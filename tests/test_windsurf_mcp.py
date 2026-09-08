import asyncio
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mcp import Client
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from control_plane.domain import AuthorizationError, ConflictError, TaskStatus, WorkspaceError
from control_plane.mcp_server import build_windsurf_mcp_app, build_windsurf_mcp_server
from control_plane.persistence import Artifact, CapabilityGrant, Task, TaskAttempt
from control_plane.service import ControlPlaneService
from control_plane.workspaces import RepositoryRegistration, RepositoryRegistry

TOKEN = "test-windsurf-token-with-at-least-32-characters"  # noqa: S105


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


@pytest.fixture
def windsurf_service(
    tmp_path: Path, session_factory: sessionmaker[Session]
) -> tuple[ControlPlaneService, Path]:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-q")
    _git(repository, "config", "user.email", "windsurf-test@example.invalid")
    _git(repository, "config", "user.name", "Windsurf Test")
    (repository / "src").mkdir()
    (repository / "src" / "base.py").write_text("base = True\n", encoding="utf-8")
    (repository / "docs").mkdir()
    (repository / "docs" / "protected.md").write_text("protected\n", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-q", "-m", "base")
    registry = RepositoryRegistry(
        (
            RepositoryRegistration(
                scope_id="owner/repository",
                path=repository,
                writable_paths=("src",),
            ),
        )
    )
    return ControlPlaneService(session_factory, repository_registry=registry), repository


def _implementation_task(service: ControlPlaneService, *, key: str) -> dict[str, object]:
    workflow = service.create_workflow(
        requester_id="dev-operator",
        title="Windsurf MCP implementation",
        description="Hand one implementation task to an authenticated IDE integration.",
        idempotency_key=key,
        repository_scope="owner/repository",
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


def _commit_handoff_change(
    repository: Path, *, branch: str, base_revision: str, path: str = "src/windsurf.py"
) -> str:
    _git(repository, "checkout", "-q", "-b", branch, base_revision)
    target = repository / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("implemented_by_windsurf = True\n", encoding="utf-8")
    _git(repository, "add", path)
    _git(repository, "commit", "-q", "-m", "windsurf implementation")
    return _git(repository, "rev-parse", "HEAD")


def test_windsurf_claim_is_explicit_scoped_and_idempotent(
    windsurf_service: tuple[ControlPlaneService, Path],
) -> None:
    service, _repository = windsurf_service
    workflow = service.create_workflow(
        requester_id="dev-operator",
        title="Do not claim planning",
        description="The IDE integration cannot claim arbitrary task kinds.",
        idempotency_key="windsurf-plan-denied",
        repository_scope="owner/repository",
    )
    plan_task = workflow["tasks"][0]
    with pytest.raises(ConflictError, match="only implementation"):
        service.claim_windsurf_task(task_id=str(plan_task["id"]), principal_id="windsurf-cascade")
    task = _implementation_task(service, key="windsurf-explicit-claim")
    with pytest.raises(AuthorizationError, match="lacks capability"):
        service.claim_windsurf_task(task_id=str(task["id"]), principal_id="implementer-agent")

    first = service.claim_windsurf_task(task_id=str(task["id"]), principal_id="windsurf-cascade")
    second = service.claim_windsurf_task(task_id=str(task["id"]), principal_id="windsurf-cascade")

    assert first["lease_token"] == second["lease_token"]
    assert first["handoff"]["digest"] == second["handoff"]["digest"]
    assert first["handoff"]["assigned_principal"] == "windsurf-cascade"
    assert first["handoff"]["repository"] == "owner/repository"
    assert tuple(first["handoff"]["writable_paths"]) == ("src",)
    with service.session_factory() as session:
        grants = session.scalars(
            select(CapabilityGrant).where(CapabilityGrant.task_id == str(task["id"]))
        ).all()
        active = [grant for grant in grants if grant.active]
        assert len(active) == 1
        assert active[0].principal_id == "windsurf-cascade"


def test_windsurf_heartbeat_requires_scoped_identity_and_lease(
    windsurf_service: tuple[ControlPlaneService, Path],
) -> None:
    service, _repository = windsurf_service
    task = _implementation_task(service, key="windsurf-heartbeat")
    claim = service.claim_windsurf_task(task_id=str(task["id"]), principal_id="windsurf-cascade")
    with pytest.raises(AuthorizationError, match="lacks capability"):
        service.heartbeat_windsurf_task(
            task_id=str(task["id"]),
            lease_token=str(claim["lease_token"]),
            principal_id="orchestrator",
        )
    with pytest.raises(AuthorizationError, match="token"):
        service.heartbeat_windsurf_task(
            task_id=str(task["id"]),
            lease_token="wrong-token",  # noqa: S106
            principal_id="windsurf-cascade",
        )
    heartbeat = service.heartbeat_windsurf_task(
        task_id=str(task["id"]),
        lease_token=str(claim["lease_token"]),
        principal_id="windsurf-cascade",
    )
    assert heartbeat["status"] == TaskStatus.RUNNING.value


def test_windsurf_evidence_is_verified_against_git_and_advances_to_testing(
    windsurf_service: tuple[ControlPlaneService, Path],
) -> None:
    service, repository = windsurf_service
    task = _implementation_task(service, key="windsurf-valid-evidence")
    claim = service.claim_windsurf_task(task_id=str(task["id"]), principal_id="windsurf-cascade")
    handoff = claim["handoff"]
    revision = _commit_handoff_change(
        repository,
        branch=str(handoff["branch"]),
        base_revision=str(handoff["base_revision"]),
    )

    with pytest.raises(ConflictError, match="reported files"):
        service.submit_windsurf_evidence(
            task_id=str(task["id"]),
            lease_token=str(claim["lease_token"]),
            principal_id="windsurf-cascade",
            handoff_digest=str(handoff["digest"]),
            result_revision=revision,
            files_changed=("src/not-the-real-file.py",),
            tests_passed=True,
            test_summary="Local checks passed.",
            tool_activity_summary="Edited one assigned source file.",
        )

    result = service.submit_windsurf_evidence(
        task_id=str(task["id"]),
        lease_token=str(claim["lease_token"]),
        principal_id="windsurf-cascade",
        handoff_digest=str(handoff["digest"]),
        result_revision=revision,
        files_changed=("src/windsurf.py",),
        tests_passed=True,
        test_summary="Local checks passed.",
        tool_activity_summary="Edited one assigned source file.",
    )
    replay = service.submit_windsurf_evidence(
        task_id=str(task["id"]),
        lease_token=str(claim["lease_token"]),
        principal_id="windsurf-cascade",
        handoff_digest=str(handoff["digest"]),
        result_revision=revision,
        files_changed=("src/windsurf.py",),
        tests_passed=True,
        test_summary="Local checks passed.",
        tool_activity_summary="Edited one assigned source file.",
    )

    assert result == replay
    assert result["status"] == TaskStatus.SUCCEEDED.value
    workflow = service.get_workflow(str(task["workflow_id"]), principal_id="dev-operator")
    assert workflow["state"] == "TESTING"
    assert workflow["candidate_revision"] == revision
    with service.session_factory() as session:
        attempt = session.scalar(
            select(TaskAttempt).where(
                TaskAttempt.task_id == str(task["id"]), TaskAttempt.provider == "windsurf"
            )
        )
        artifact = session.scalar(select(Artifact).where(Artifact.task_id == str(task["id"])))
        assert attempt is not None
        assert attempt.output["evidence"]["test_claim_authoritative"] is False
        assert artifact is not None and artifact.revision == revision


def test_windsurf_rejects_changes_outside_registered_paths(
    windsurf_service: tuple[ControlPlaneService, Path],
) -> None:
    service, repository = windsurf_service
    task = _implementation_task(service, key="windsurf-path-denied")
    claim = service.claim_windsurf_task(task_id=str(task["id"]), principal_id="windsurf-cascade")
    handoff = claim["handoff"]
    revision = _commit_handoff_change(
        repository,
        branch=str(handoff["branch"]),
        base_revision=str(handoff["base_revision"]),
        path="docs/escaped.md",
    )
    with pytest.raises(WorkspaceError, match="outside registered writable paths"):
        service.submit_windsurf_evidence(
            task_id=str(task["id"]),
            lease_token=str(claim["lease_token"]),
            principal_id="windsurf-cascade",
            handoff_digest=str(handoff["digest"]),
            result_revision=revision,
            files_changed=("docs/escaped.md",),
            tests_passed=False,
            test_summary="Not accepted as authoritative.",
            tool_activity_summary="Changed a disallowed file.",
        )


def test_expired_windsurf_claim_blocks_and_rejects_late_evidence(
    windsurf_service: tuple[ControlPlaneService, Path],
) -> None:
    service, repository = windsurf_service
    task = _implementation_task(service, key="windsurf-expired-claim")
    claim = service.claim_windsurf_task(task_id=str(task["id"]), principal_id="windsurf-cascade")
    handoff = claim["handoff"]
    revision = _commit_handoff_change(
        repository,
        branch=str(handoff["branch"]),
        base_revision=str(handoff["base_revision"]),
    )
    with service.session_factory() as session, session.begin():
        stored = session.get(Task, str(task["id"]))
        assert stored is not None
        stored.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert service.reclaim_expired_tasks(worker_id="orchestrator") == 1

    with pytest.raises(ConflictError, match="not accepting evidence"):
        service.submit_windsurf_evidence(
            task_id=str(task["id"]),
            lease_token=str(claim["lease_token"]),
            principal_id="windsurf-cascade",
            handoff_digest=str(handoff["digest"]),
            result_revision=revision,
            files_changed=("src/windsurf.py",),
            tests_passed=True,
            test_summary="This late claim must not be accepted.",
            tool_activity_summary="Work completed after authority expired.",
        )


def test_mcp_protocol_exposes_only_three_narrow_tools(
    windsurf_service: tuple[ControlPlaneService, Path],
) -> None:
    service, _repository = windsurf_service
    task = _implementation_task(service, key="windsurf-protocol-claim")
    server = build_windsurf_mcp_server(service, principal_id="windsurf-cascade")

    async def inspect_tools() -> tuple[set[str], dict[str, object] | None]:
        async with Client(server) as client:
            result = await client.list_tools()
            claim = await client.call_tool(
                "claim_implementation_task", {"task_id": str(task["id"])}
            )
            return {tool.name for tool in result.tools}, claim.structured_content

    tools, claim = asyncio.run(inspect_tools())
    assert tools == {
        "claim_implementation_task",
        "heartbeat_implementation_task",
        "submit_implementation_evidence",
    }
    assert claim is not None
    assert claim["task_id"] == task["id"]


def test_mcp_http_boundary_requires_bearer_authentication(
    windsurf_service: tuple[ControlPlaneService, Path],
) -> None:
    service, _repository = windsurf_service
    app = build_windsurf_mcp_app(service, bearer_token=TOKEN)
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "windsurf-test", "version": "1"},
        },
    }
    with TestClient(app, base_url="http://localhost:8010") as client:
        missing = client.post("/mcp", json=initialize)
        wrong = client.post("/mcp", json=initialize, headers={"Authorization": f"Bearer {TOKEN}x"})
        duplicate = client.post(
            "/mcp",
            json=initialize,
            headers=[
                ("Authorization", f"Bearer {TOKEN}"),
                ("Authorization", f"Bearer {TOKEN}"),
            ],
        )
        accepted = client.post(
            "/mcp",
            json=initialize,
            headers={
                "Authorization": f"Bearer {TOKEN}",
                "Accept": "application/json, text/event-stream",
            },
        )

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert duplicate.status_code == 401
    assert accepted.status_code == 200


def test_mcp_rejects_short_bearer_tokens(
    windsurf_service: tuple[ControlPlaneService, Path],
) -> None:
    service, _repository = windsurf_service
    with pytest.raises(ValueError, match="at least 32"):
        build_windsurf_mcp_app(service, bearer_token="short")  # noqa: S106
