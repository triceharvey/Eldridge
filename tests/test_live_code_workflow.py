import json
import os
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from control_plane.activation import ProviderActivationPolicy, activate_integrations
from control_plane.domain import ApprovalAction, ApprovalDecision, TaskKind
from control_plane.executors import FakeExecutor, IsolatedRepositoryExecutor
from control_plane.persistence import Task, TaskAttempt
from control_plane.routing import (
    DataClassification,
    EgressBoundary,
    RiskLevel,
    RoutingObjective,
    WorkCapability,
)
from control_plane.service import ControlPlaneService, ProviderBinding
from control_plane.workspaces import (
    GitWorktreeManager,
    RepositoryRegistration,
    RepositoryRegistry,
)

LOCAL_MODEL = "qwen3.5:9b-q4_K_M"
LOCAL_MODEL_DIGEST = "sha256:6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7"
SOURCE_PATH = "src/control_plane/change_scope.py"
TEST_PATH = "tests/test_change_scope_generated.py"
TARGET_PATHS = (SOURCE_PATH, TEST_PATH)
OPERATION_KEY = "phase-5-4d-code-workflow-v6"
GIT = shutil.which("git") or "/usr/bin/git"


def _git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(  # noqa: S603
        [GIT, "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.stdout.strip()


@pytest.mark.live_provider
def test_live_code_workflow_creates_tested_exact_approved_revision(
    session_factory: sessionmaker[Session], tmp_path: Path
) -> None:
    if os.getenv("CONTROL_PLANE_RUN_LIVE_CODE_WORKFLOW") != "true":
        pytest.skip("explicit live code-workflow opt-in is not enabled")
    repository = Path(os.getenv("CONTROL_PLANE_LIVE_REPOSITORY", str(Path.cwd()))).resolve()
    base_revision = _git(repository, "rev-parse", "HEAD")
    assert all(not (repository / target).exists() for target in TARGET_PATHS)

    policy = ProviderActivationPolicy.model_validate(
        {
            "policy_version": "provider-activation/live-code-workflow-v1",
            "allow_external_egress": True,
            "include_mock_providers": False,
            "secret_environment": {},
            "claude_code": {
                "enabled": True,
                "model": "sonnet",
                "timeout_seconds": 180,
                "maximum_data_classification": "PUBLIC",
                "maximum_risk": "LOW",
                "max_invocations_per_execution": 1,
                "max_invocations_per_workflow": 2,
            },
            "local_model": {
                "enabled": True,
                "endpoint": "http://127.0.0.1:11434/v1/chat/completions",
                "model": LOCAL_MODEL,
                "artifact_digest": LOCAL_MODEL_DIGEST,
                "max_tokens": 2048,
                "timeout_seconds": 180,
                "reasoning_effort": "none",
                "maximum_data_classification": "PUBLIC",
                "maximum_risk": "LOW",
            },
        }
    )
    activated = activate_integrations(policy)
    bindings: list[ProviderBinding] = []
    for binding in activated.bindings:
        profile = binding.profile
        if profile.provider_id == "claude-code-subscription":
            profile = replace(
                profile,
                capabilities=frozenset({WorkCapability.PLANNING, WorkCapability.CODE_GENERATION}),
            )
        bindings.append(ProviderBinding(profile, binding.provider))
    registration = RepositoryRegistration(
        scope_id="triceharvey/Eldridge",
        path=repository,
        base_revision=base_revision,
        writable_paths=TARGET_PATHS,
    )
    registry = RepositoryRegistry((registration,))
    executor = IsolatedRepositoryExecutor(
        registry=registry,
        worktrees=GitWorktreeManager(tmp_path / "worktrees"),
        fallback=FakeExecutor(),
        require_tool_evidence=True,
    )
    service = ControlPlaneService(
        session_factory,
        executor=executor,
        repository_registry=registry,
        provider_bindings=tuple(bindings),
        allowed_egress=frozenset({EgressBoundary.LOCAL, EgressBoundary.APPROVED_EXTERNAL}),
        provider_policy_version=activated.policy_version,
        routing_objective=RoutingObjective.QUALITY,
    )
    workflow = service.create_workflow(
        requester_id="dev-operator",
        title="Phase 5.4D bounded Python change",
        description=(
            f"Create exactly {SOURCE_PATH} and {TEST_PATH}; modify no other path. Implement "
            "classify_changed_paths(paths), accepting an iterable of strings and returning a dict "
            "with exactly source, tests, docs, and other keys whose values are sorted tuples of "
            "unique validated POSIX-relative paths. Classify by the top-level src, tests, and docs "
            "directory; everything else is other. Reject with ValueError any empty path, absolute "
            "path, backslash, NUL, parent traversal, dot component, or .git component. Use only "
            "the Python standard library. Write unittest-based deterministic coverage for normal "
            "classification, sorting and deduplication, and every rejection family. During "
            "IMPLEMENT follow the repository's Ruff and mypy conventions: use built-in generic "
            "types, import Iterable from collections.abc, fully parameterize collections, and "
            "keep lines at or below 100 characters. Match Ruff formatter output: when an entire "
            "function call and list argument fit below 100 characters, keep that call on one line. "
            "The isolated unittest runner places src/ on sys.path, so the generated test must "
            "import exactly `from control_plane.change_scope import classify_changed_paths`; do "
            "not import through a top-level src package. During "
            "IMPLEMENT propose exactly two WRITE_TEXT_FILE requests for the authorized paths, no "
            "commands, and PENDING_CONTROLLER_COMMIT as candidate_revision. During TEST propose "
            f"PYTHON_COMPILE for both paths and PYTHON_UNITTEST for {TEST_PATH}."
        ),
        idempotency_key=OPERATION_KEY,
        risk=RiskLevel.LOW,
        data_classification=DataClassification.PUBLIC,
        repository_scope=registration.scope_id,
    )

    leases = 0
    while workflow["state"] != "AWAITING_HUMAN_APPROVAL":
        leases += 1
        assert leases <= 12, "workflow exceeded its bounded task-attempt allowance"
        task = service.lease_next_task(worker_id="orchestrator")
        assert task is not None
        result = service.execute_leased_task(
            task_id=str(task["id"]),
            lease_token=str(task["lease_token"]),
            worker_id="orchestrator",
        )
        assert result["status"] in {"READY", "SUCCEEDED"}, result
        workflow = service.get_workflow(str(workflow["id"]), principal_id="dev-operator")

    revision = str(workflow["candidate_revision"])
    with session_factory() as session:
        attempts = session.scalars(
            select(TaskAttempt)
            .join(Task, TaskAttempt.task_id == Task.id)
            .where(
                Task.workflow_id == workflow["id"],
                TaskAttempt.status == "SUCCEEDED",
            )
        ).all()
        implementation = next(
            item for item in attempts if item.task.kind == TaskKind.IMPLEMENT.value
        )
        testing = next(item for item in attempts if item.task.kind == TaskKind.TEST.value)
    implementation_evidence = implementation.output["execution_evidence"]
    test_evidence = testing.output["execution_evidence"]
    branch = str(implementation_evidence["branch"])
    assert (
        registry.verify_revision_evidence(
            scope_id=registration.scope_id,
            branch=branch,
            base_revision=base_revision,
            result_revision=revision,
        )
        == TARGET_PATHS
    )
    assert test_evidence["result_revision"] == revision
    assert [item["tool"] for item in test_evidence["sandbox"]] == [
        "PYTHON_COMPILE",
        "PYTHON_UNITTEST",
    ]
    assert all(item["exit_code"] == 0 for item in test_evidence["sandbox"])

    approval = service.approve(
        workflow_id=str(workflow["id"]),
        approver_id="dev-operator",
        action=ApprovalAction.MERGE,
        target="triceharvey/Eldridge:main",
        revision=revision,
        decision=ApprovalDecision.APPROVED,
        rationale="Approved the exact isolated Python revision after deterministic tests passed.",
    )
    report_path = os.getenv("CONTROL_PLANE_LIVE_CODE_REPORT")
    if report_path:
        Path(report_path).write_text(
            json.dumps(
                {
                    "approval_id": approval["id"],
                    "base_revision": base_revision,
                    "branch": branch,
                    "changed_files": list(TARGET_PATHS),
                    "policy_version": activated.policy_version,
                    "result": "PASSED",
                    "revision": revision,
                    "test_tools": [item["tool"] for item in test_evidence["sandbox"]],
                    "workflow_id": workflow["id"],
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
