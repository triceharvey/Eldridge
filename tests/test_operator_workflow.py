import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import func, select

from control_plane.cli import main
from control_plane.domain import TaskKind, TaskStatus
from control_plane.operator_workflow import (
    OperatorWorkflowManifest,
    drive_operator_workflow,
    preflight_operator_workflow,
)
from control_plane.persistence import Approval, Task
from control_plane.service import ControlPlaneService

GIT = shutil.which("git") or "/usr/bin/git"


def _git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(  # noqa: S603
        [GIT, "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _repository_files(tmp_path: Path) -> tuple[Path, Path, Path, str]:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-q")
    _git(repository, "config", "user.email", "test@example.invalid")
    _git(repository, "config", "user.name", "Test")
    (repository / "README.md").write_text("test\n", encoding="utf-8")
    _git(repository, "add", "README.md")
    _git(repository, "commit", "-q", "-m", "base")
    revision = _git(repository, "rev-parse", "HEAD")
    registry = tmp_path / "repositories.json"
    registry.write_text(
        json.dumps(
            [
                {
                    "scope_id": "owner/repository",
                    "path": str(repository),
                    "base_revision": revision,
                    "writable_paths": ["src", "tests"],
                }
            ]
        ),
        encoding="utf-8",
    )
    policy = tmp_path / "providers.json"
    policy.write_text(
        json.dumps(
            {
                "policy_version": "test/operator-v1",
                "allow_external_egress": False,
                "include_mock_providers": True,
                "secret_environment": {},
            }
        ),
        encoding="utf-8",
    )
    return repository, registry, policy, revision


def _manifest(revision: str, **overrides: object) -> OperatorWorkflowManifest:
    payload: dict[str, object] = {
        "schema_version": "1",
        "title": "Bounded operator workflow",
        "objective": "Create the two explicitly authorized files and stop at human approval.",
        "idempotency_key": "operator-workflow-test",
        "repository_scope": "owner/repository",
        "base_revision": revision,
        "writable_paths": ["src/generated.py", "tests/test_generated.py"],
        "providers": {
            "PLAN": "mock-producer",
            "ARCHITECTURE_REVIEW": "mock-reviewer",
            "IMPLEMENT": "mock-producer",
            "TEST": "mock-reviewer",
            "SECURITY_REVIEW": "mock-reviewer",
            "CODE_REVIEW": "mock-reviewer",
        },
        "complexity": "STANDARD",
        "risk": "LOW",
        "data_classification": "PUBLIC",
    }
    payload.update(overrides)
    return OperatorWorkflowManifest.model_validate(payload)


def _mixed_provider_policy(policy: Path, **claude_overrides: object) -> None:
    claude: dict[str, object] = {
        "enabled": True,
        "model": "sonnet",
        "maximum_data_classification": "PUBLIC",
        "maximum_risk": "LOW",
        "max_invocations_per_execution": 1,
        "max_invocations_per_workflow": 2,
    }
    claude.update(claude_overrides)
    policy.write_text(
        json.dumps(
            {
                "policy_version": "test/operator-live-v1",
                "allow_external_egress": True,
                "include_mock_providers": False,
                "secret_environment": {},
                "claude_code": claude,
                "local_model": {
                    "enabled": True,
                    "model": "test-model",
                    "artifact_digest": f"sha256:{'a' * 64}",
                    "maximum_data_classification": "RESTRICTED",
                    "maximum_risk": "MEDIUM",
                },
            }
        ),
        encoding="utf-8",
    )


def _mixed_providers() -> dict[str, str]:
    return {
        "PLAN": "claude-code-subscription",
        "ARCHITECTURE_REVIEW": "local-openai-compatible",
        "IMPLEMENT": "claude-code-subscription",
        "TEST": "local-openai-compatible",
        "SECURITY_REVIEW": "local-openai-compatible",
        "CODE_REVIEW": "local-openai-compatible",
    }


def test_preflight_binds_exact_revision_paths_roles_and_human_stop(tmp_path: Path) -> None:
    repository, registry, policy, revision = _repository_files(tmp_path)

    result = preflight_operator_workflow(
        _manifest(revision),
        repository_registry_file=registry,
        provider_policy_file=policy,
    )

    assert result["result"] == "READY"
    assert result["repository_path"] == str(repository)
    assert result["base_revision"] == revision
    assert result["writable_paths"] == ["src/generated.py", "tests/test_generated.py"]
    assert result["providers"][TaskKind.IMPLEMENT.value] == "mock-producer"
    assert result["external_providers"] == []
    assert result["stops_at"] == "AWAITING_HUMAN_APPROVAL"


def test_preflight_rejects_path_beyond_registry_grant(tmp_path: Path) -> None:
    _, registry, policy, revision = _repository_files(tmp_path)

    with pytest.raises(ValueError, match="exceeds"):
        preflight_operator_workflow(
            _manifest(revision, writable_paths=["deployment/production.yaml"]),
            repository_registry_file=registry,
            provider_policy_file=policy,
        )


def test_preflight_rejects_revision_other_than_registry_base(tmp_path: Path) -> None:
    repository, registry, policy, _ = _repository_files(tmp_path)
    (repository / "README.md").write_text("changed\n", encoding="utf-8")
    _git(repository, "add", "README.md")
    _git(repository, "commit", "-q", "-m", "unregistered revision")
    unregistered_revision = _git(repository, "rev-parse", "HEAD")

    with pytest.raises(ValueError, match="does not match the repository registry base"):
        preflight_operator_workflow(
            _manifest(unregistered_revision),
            repository_registry_file=registry,
            provider_policy_file=policy,
        )


def test_preflight_rejects_nonindependent_reviewer(tmp_path: Path) -> None:
    _, registry, policy, revision = _repository_files(tmp_path)
    providers = _manifest(revision).providers | {
        TaskKind.CODE_REVIEW: "mock-producer",
    }

    with pytest.raises(ValueError, match="independent provider identity"):
        preflight_operator_workflow(
            _manifest(revision, providers=providers),
            repository_registry_file=registry,
            provider_policy_file=policy,
        )


def test_preflight_enforces_configured_provider_risk_ceiling(tmp_path: Path) -> None:
    _, registry, policy, revision = _repository_files(tmp_path)
    _mixed_provider_policy(policy)

    with pytest.raises(ValueError, match="risk ceiling"):
        preflight_operator_workflow(
            _manifest(revision, providers=_mixed_providers(), risk="MEDIUM"),
            repository_registry_file=registry,
            provider_policy_file=policy,
        )


def test_preflight_enforces_subscription_workflow_quota(tmp_path: Path) -> None:
    _, registry, policy, revision = _repository_files(tmp_path)
    _mixed_provider_policy(policy, max_invocations_per_workflow=1)

    with pytest.raises(ValueError, match="lacks quota"):
        preflight_operator_workflow(
            _manifest(revision, providers=_mixed_providers()),
            repository_registry_file=registry,
            provider_policy_file=policy,
        )


def test_manifest_requires_exact_revision_and_complete_roles() -> None:
    with pytest.raises(PydanticValidationError):
        _manifest("HEAD")
    with pytest.raises(PydanticValidationError, match="every model task kind"):
        _manifest("a" * 40, providers={"PLAN": "mock-producer"})


def test_driver_stops_at_human_gate_without_recording_approval(
    service: ControlPlaneService,
) -> None:
    manifest = _manifest("a" * 40, repository_scope="owner/not-executed")
    events: list[dict[str, object]] = []

    workflow = drive_operator_workflow(service, manifest, emit=events.append)

    assert workflow["state"] == "AWAITING_HUMAN_APPROVAL"
    assert events[-1]["event"] == "human_gate_reached"
    with service.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Approval)) == 0


def test_driver_only_leases_tasks_from_its_created_workflow(
    service: ControlPlaneService,
) -> None:
    unrelated = service.create_workflow(
        requester_id="dev-operator",
        title="Unrelated workflow",
        description="This ready task must remain untouched.",
        idempotency_key="unrelated-operator-workflow",
    )
    manifest = _manifest(
        "a" * 40,
        repository_scope="owner/not-executed",
        idempotency_key="scoped-operator-workflow",
    )

    completed = drive_operator_workflow(service, manifest)

    assert completed["id"] != unrelated["id"]
    assert completed["state"] == "AWAITING_HUMAN_APPROVAL"
    with service.session_factory() as session:
        unrelated_tasks = session.scalars(
            select(Task).where(Task.workflow_id == unrelated["id"])
        ).all()
        assert len(unrelated_tasks) == 1
        assert unrelated_tasks[0].status == TaskStatus.READY.value
        assert unrelated_tasks[0].lease_owner is None


def test_cli_preflight_is_read_only_and_emits_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, registry, policy, revision = _repository_files(tmp_path)
    manifest_path = tmp_path / "workflow.json"
    manifest_path.write_text(
        _manifest(revision).model_dump_json(indent=2),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "control-plane",
            "workflow",
            "preflight",
            "--manifest",
            str(manifest_path),
            "--repository-registry",
            str(registry),
            "--provider-policy",
            str(policy),
        ],
    )

    with pytest.raises(SystemExit) as stopped:
        main()

    assert stopped.value.code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["result"] == "READY"
    assert output["base_revision"] == revision


def test_cli_run_requires_explicit_execution_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, registry, policy, revision = _repository_files(tmp_path)
    manifest_path = tmp_path / "workflow.json"
    manifest_path.write_text(_manifest(revision).model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "control-plane",
            "workflow",
            "run",
            "--manifest",
            str(manifest_path),
            "--repository-registry",
            str(registry),
            "--provider-policy",
            str(policy),
            "--database-url",
            "sqlite:///:memory:",
        ],
    )

    with pytest.raises(SystemExit, match="--confirm-execution"):
        main()
