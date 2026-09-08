from sqlalchemy import select

from control_plane.audit import verify_audit_chain
from control_plane.domain import ApprovalAction, ApprovalDecision
from control_plane.persistence import Artifact, CapabilityGrant, Task, Workflow
from control_plane.service import ControlPlaneService
from tests.conftest import drive_to_human_gate


def test_full_workflow_reaches_human_gate_with_bound_evidence(
    service: ControlPlaneService,
) -> None:
    workflow = drive_to_human_gate(service)
    assert workflow["state"] == "AWAITING_HUMAN_APPROVAL"
    assert str(workflow["candidate_revision"]).startswith("mock-")
    assert [task["kind"] for task in workflow["tasks"]] == [
        "PLAN",
        "ARCHITECTURE_REVIEW",
        "IMPLEMENT",
        "TEST",
        "SECURITY_REVIEW",
        "CODE_REVIEW",
    ]
    assert all(task["status"] == "SUCCEEDED" for task in workflow["tasks"])
    assert all("lease_token" not in task for task in workflow["tasks"])

    with service.session_factory() as session:
        artifacts = session.scalars(
            select(Artifact).where(Artifact.workflow_id == workflow["id"])
        ).all()
        assert len(artifacts) == 6
        candidate_artifacts = [artifact for artifact in artifacts if artifact.revision]
        assert candidate_artifacts
        assert {artifact.revision for artifact in candidate_artifacts} == {
            workflow["candidate_revision"]
        }
        grants = session.scalars(
            select(CapabilityGrant).where(CapabilityGrant.workflow_id == workflow["id"])
        ).all()
        assert grants and all(not grant.active for grant in grants)
        assert verify_audit_chain(session, str(workflow["id"]))


def test_human_can_approve_exact_revision(service: ControlPlaneService) -> None:
    workflow = drive_to_human_gate(service)
    approval = service.approve(
        workflow_id=str(workflow["id"]),
        approver_id="dev-operator",
        action=ApprovalAction.MERGE,
        target="owner/repo:main",
        revision=str(workflow["candidate_revision"]),
        decision=ApprovalDecision.APPROVED,
        rationale="Reviewed deterministic evidence.",
    )
    final = service.get_workflow(str(workflow["id"]), principal_id="dev-operator")
    assert approval["approver_id"] == "dev-operator"
    assert approval["revision"] == workflow["candidate_revision"]
    assert final["state"] == "APPROVED"


def test_create_is_idempotent(service: ControlPlaneService) -> None:
    request = {
        "requester_id": "dev-operator",
        "title": "Idempotent",
        "description": "Same command twice.",
        "idempotency_key": "same-key-123",
    }
    first = service.create_workflow(**request)
    second = service.create_workflow(**request)
    assert first["id"] == second["id"]
    with service.session_factory() as session:
        assert len(session.scalars(select(Workflow)).all()) == 1
        assert len(session.scalars(select(Task)).all()) == 1


def test_successful_task_clears_lease_secret(service: ControlPlaneService) -> None:
    workflow = service.create_workflow(
        requester_id="dev-operator",
        title="Lease cleanup",
        description="Ensure leases are not retained after completion.",
        idempotency_key="lease-cleanup-key",
    )
    leased = service.lease_next_task(worker_id="orchestrator")
    assert leased is not None and leased["lease_token"]
    service.execute_leased_task(
        task_id=leased["id"],
        lease_token=leased["lease_token"],
        worker_id="orchestrator",
    )
    current = service.get_workflow(str(workflow["id"]), principal_id="dev-operator")
    completed = current["tasks"][0]
    assert completed["lease_owner"] is None
    assert completed["lease_expires_at"] is None


def test_service_restart_preserves_durable_state(service: ControlPlaneService) -> None:
    workflow = service.create_workflow(
        requester_id="dev-operator",
        title="Restart durability",
        description="Create state, replace the service instance, and read it again.",
        idempotency_key="restart-durability-key",
    )
    restarted = ControlPlaneService(service.session_factory)
    restored = restarted.get_workflow(str(workflow["id"]), principal_id="dev-operator")
    assert restored["id"] == workflow["id"]
    assert restored["state"] == "PLANNING"
    assert restored["version"] == workflow["version"]
