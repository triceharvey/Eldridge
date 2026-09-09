from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from control_plane.api import create_app
from control_plane.audit import verify_audit_chain
from control_plane.deployment import DryRunDeploymentAdapter, EnvironmentClassification
from control_plane.domain import (
    ApprovalAction,
    ApprovalDecision,
    AuthorizationError,
    ConflictError,
    ValidationError,
    WorkflowState,
)
from control_plane.persistence import (
    Approval,
    DeploymentAttemptRecord,
    DeploymentVerificationRecord,
    Workflow,
)
from control_plane.service import ControlPlaneService

ARTIFACT_DIGEST = "sha256:" + "a" * 64
MERGED_REVISION = "b" * 40


def _merged_workflow(service: ControlPlaneService) -> dict[str, object]:
    created = service.create_workflow(
        requester_id="dev-operator",
        title="Deployment dry run",
        description="Exercise exact deployment approval without an external target.",
        idempotency_key="deployment-workflow-key",
        repository_scope="triceharvey/Eldridge",
    )
    with service.session_factory() as session, session.begin():
        workflow = session.get(Workflow, str(created["id"]))
        assert workflow is not None
        workflow.state = WorkflowState.MERGED.value
        workflow.candidate_revision = "c" * 40
        workflow.merged_revision = MERGED_REVISION
    return service.get_workflow(str(created["id"]), principal_id="dev-operator")


def _environment(service: ControlPlaneService) -> dict[str, object]:
    return service.register_deployment_environment(
        actor_id="dev-operator",
        environment_id="eldridge-dry-run",
        name="Eldridge deterministic dry run",
        classification=EnvironmentClassification.DEVELOPMENT,
        repository="triceharvey/Eldridge",
        base_branch="main",
        resource_scope=("control-plane-api",),
        required_checks=("test", "gitleaks", "package"),
        required_attestations=("artifact-digest",),
        verification_policy=("revision-match", "plan-digest-match"),
        rollback_policy="dry-run-no-change",
        policy_version="deployment/dry-run-v1",
    )


def _plan(service: ControlPlaneService, workflow_id: str) -> dict[str, object]:
    return service.create_deployment_plan(
        workflow_id=workflow_id,
        actor_id="dev-operator",
        environment_id="eldridge-dry-run",
        artifact_digests=(ARTIFACT_DIGEST,),
        operations=(
            {
                "kind": "VERIFY_ARTIFACT",
                "resource_id": "control-plane-api",
                "artifact_digest": ARTIFACT_DIGEST,
            },
        ),
        declared_impact="Simulation only; no target or credential exists.",
        verification_probes=("revision-match", "plan-digest-match"),
        rollback_reference="dry-run-no-change",
        idempotency_key="deployment-plan-key",
    )


def test_exact_approval_drives_no_credential_dry_run(service: ControlPlaneService) -> None:
    workflow = _merged_workflow(service)
    environment = _environment(service)
    plan = _plan(service, str(workflow["id"]))

    assert environment["provider"] == "dry-run"
    assert plan["revision"] == MERGED_REVISION
    assert len(str(plan["digest"])) == 64
    assert (
        service.get_workflow(str(workflow["id"]), principal_id="dev-operator")["state"]
        == WorkflowState.AWAITING_DEPLOYMENT_APPROVAL.value
    )

    approval = service.approve(
        workflow_id=str(workflow["id"]),
        approver_id="dev-operator",
        action=ApprovalAction.DEPLOY,
        target="eldridge-dry-run",
        revision=MERGED_REVISION,
        decision=ApprovalDecision.APPROVED,
        rationale="Approve the exact no-change simulation.",
        environment_id="eldridge-dry-run",
        plan_digest=str(plan["digest"]),
    )
    assert approval["consumed_at"] is None

    result = service.execute_deployment_dry_run(
        workflow_id=str(workflow["id"]),
        plan_id=str(plan["id"]),
        actor_id="dev-operator",
        idempotency_key="deployment-execution-key",
    )
    replay = service.execute_deployment_dry_run(
        workflow_id=str(workflow["id"]),
        plan_id=str(plan["id"]),
        actor_id="dev-operator",
        idempotency_key="deployment-execution-key",
    )

    assert replay["id"] == result["id"]
    assert result["status"] == "SUCCEEDED"
    assert result["simulated"] is True
    assert result["result"]["changed"] is False
    assert result["result"]["execution"]["credential_requested"] is False
    assert result["result"]["execution"]["external_target_contacted"] is False
    current = service.get_workflow(str(workflow["id"]), principal_id="dev-operator")
    assert current["state"] == WorkflowState.AWAITING_DEPLOYMENT_APPROVAL.value
    with pytest.raises(ConflictError, match="idempotency key was reused"):
        service.execute_deployment_dry_run(
            workflow_id=str(workflow["id"]),
            plan_id="another-plan",
            actor_id="dev-operator",
            idempotency_key="deployment-execution-key",
        )

    with service.session_factory() as session:
        stored_approval = session.get(Approval, str(approval["id"]))
        assert stored_approval is not None and stored_approval.consumed_at is not None
        attempts = session.scalars(
            select(DeploymentAttemptRecord).where(
                DeploymentAttemptRecord.workflow_id == workflow["id"]
            )
        ).all()
        verification = session.scalar(
            select(DeploymentVerificationRecord).where(
                DeploymentVerificationRecord.attempt_id == result["id"]
            )
        )
        assert len(attempts) == 1
        assert verification is not None and verification.passed
        assert verification.observed_revision == MERGED_REVISION
        assert verify_audit_chain(session, str(workflow["id"]))


def test_deployment_approval_rejects_wrong_plan_digest(service: ControlPlaneService) -> None:
    workflow = _merged_workflow(service)
    _environment(service)
    _plan(service, str(workflow["id"]))

    with pytest.raises(ConflictError, match="immutable plan"):
        service.approve(
            workflow_id=str(workflow["id"]),
            approver_id="dev-operator",
            action=ApprovalAction.DEPLOY,
            target="eldridge-dry-run",
            revision=MERGED_REVISION,
            decision=ApprovalDecision.APPROVED,
            rationale="This must not bind to a different plan.",
            environment_id="eldridge-dry-run",
            plan_digest="0" * 64,
        )


def test_expired_or_consumed_approval_cannot_execute(service: ControlPlaneService) -> None:
    workflow = _merged_workflow(service)
    _environment(service)
    plan = _plan(service, str(workflow["id"]))
    approval = service.approve(
        workflow_id=str(workflow["id"]),
        approver_id="dev-operator",
        action=ApprovalAction.DEPLOY,
        target="eldridge-dry-run",
        revision=MERGED_REVISION,
        decision=ApprovalDecision.APPROVED,
        rationale="Expire before execution.",
        environment_id="eldridge-dry-run",
        plan_digest=str(plan["digest"]),
    )
    with service.session_factory() as session, session.begin():
        stored = session.get(Approval, str(approval["id"]))
        assert stored is not None
        stored.expires_at = datetime.now(UTC) - timedelta(minutes=1)

    with pytest.raises(AuthorizationError, match="expired"):
        service.execute_deployment_dry_run(
            workflow_id=str(workflow["id"]),
            plan_id=str(plan["id"]),
            actor_id="dev-operator",
            idempotency_key="expired-deployment-key",
        )


def test_phase_4_1_rejects_production_and_untyped_operations(
    service: ControlPlaneService,
) -> None:
    with pytest.raises(AuthorizationError, match="production"):
        service.register_deployment_environment(
            actor_id="dev-operator",
            environment_id="production",
            name="Production",
            classification=EnvironmentClassification.PRODUCTION,
            repository="triceharvey/Eldridge",
            base_branch="main",
            resource_scope=("api",),
            required_checks=(),
            required_attestations=(),
            verification_policy=("health",),
            rollback_policy="rollback-v1",
            policy_version="production-v1",
        )

    workflow = _merged_workflow(service)
    _environment(service)
    with pytest.raises(ValidationError, match="kind is not allowlisted"):
        service.create_deployment_plan(
            workflow_id=str(workflow["id"]),
            actor_id="dev-operator",
            environment_id="eldridge-dry-run",
            artifact_digests=(ARTIFACT_DIGEST,),
            operations=(
                {
                    "kind": "RUN_SHELL",
                    "resource_id": "control-plane-api",
                    "artifact_digest": ARTIFACT_DIGEST,
                },
            ),
            declared_impact="Must be rejected.",
            verification_probes=("revision-match",),
            rollback_reference="dry-run-no-change",
            idempotency_key="bad-operation-key",
        )


def test_plan_cannot_widen_environment_resource_scope(service: ControlPlaneService) -> None:
    workflow = _merged_workflow(service)
    _environment(service)
    with pytest.raises(AuthorizationError, match="resource scope"):
        service.create_deployment_plan(
            workflow_id=str(workflow["id"]),
            actor_id="dev-operator",
            environment_id="eldridge-dry-run",
            artifact_digests=(ARTIFACT_DIGEST,),
            operations=(
                {
                    "kind": "VERIFY_ARTIFACT",
                    "resource_id": "unapproved-service",
                    "artifact_digest": ARTIFACT_DIGEST,
                },
            ),
            declared_impact="Must remain inside the environment scope.",
            verification_probes=("revision-match",),
            rollback_reference="dry-run-no-change",
            idempotency_key="widened-resource-key",
        )


def test_agent_cannot_register_environment(service: ControlPlaneService) -> None:
    with pytest.raises(AuthorizationError):
        service.register_deployment_environment(
            actor_id="implementer-agent",
            environment_id="agent-environment",
            name="Unauthorized",
            classification=EnvironmentClassification.DEVELOPMENT,
            repository="triceharvey/Eldridge",
            base_branch="main",
            resource_scope=("api",),
            required_checks=(),
            required_attestations=(),
            verification_policy=("health",),
            rollback_policy="dry-run-no-change",
            policy_version="deployment/dry-run-v1",
        )


def test_default_service_refuses_credential_requiring_adapter(service: ControlPlaneService) -> None:
    class CredentialAdapter(DryRunDeploymentAdapter):
        adapter_id = "credential-adapter"
        requires_credentials = True

    with pytest.raises(ValueError, match="explicit local activation"):
        ControlPlaneService(
            service.session_factory,
            deployment_adapters=(CredentialAdapter(),),
        )


def test_deployment_command_api_preserves_exact_bindings(service: ControlPlaneService) -> None:
    workflow = _merged_workflow(service)
    digest = ARTIFACT_DIGEST
    with TestClient(create_app(service)) as client:
        environment_response = client.post(
            "/deployment-environments",
            json={
                "environment_id": "api-dry-run",
                "name": "API dry run",
                "repository": "triceharvey/Eldridge",
                "base_branch": "main",
                "resource_scope": ["control-plane-api"],
                "verification_policy": ["revision-match"],
                "rollback_policy": "dry-run-no-change",
                "policy_version": "deployment/dry-run-v1",
            },
        )
        plan_response = client.post(
            f"/workflows/{workflow['id']}/deployment-plans",
            json={
                "environment_id": "api-dry-run",
                "artifact_digests": [digest],
                "operations": [
                    {
                        "kind": "VERIFY_ARTIFACT",
                        "resource_id": "control-plane-api",
                        "artifact_digest": digest,
                    }
                ],
                "declared_impact": "No-change API simulation.",
                "verification_probes": ["revision-match"],
                "rollback_reference": "dry-run-no-change",
                "idempotency_key": "api-deployment-plan",
            },
        )
        assert environment_response.status_code == 201
        assert plan_response.status_code == 201
        plan = plan_response.json()
        approval_response = client.post(
            "/approvals",
            json={
                "workflow_id": workflow["id"],
                "action": "DEPLOY",
                "target": "api-dry-run",
                "revision": MERGED_REVISION,
                "decision": "APPROVED",
                "rationale": "Approve exact API dry run.",
                "environment_id": "api-dry-run",
                "plan_digest": plan["digest"],
            },
        )
        execution_response = client.post(
            f"/workflows/{workflow['id']}/deployment-plans/{plan['id']}/dry-run",
            json={"idempotency_key": "api-deployment-execution"},
        )
        attempts_response = client.get(f"/workflows/{workflow['id']}/deployment-attempts")
        rejected_operation = client.post(
            f"/workflows/{workflow['id']}/deployment-plans",
            json={
                "environment_id": "api-dry-run",
                "artifact_digests": [digest],
                "operations": [
                    {
                        "kind": "VERIFY_ARTIFACT",
                        "resource_id": "control-plane-api",
                        "artifact_digest": digest,
                        "command": "curl external.example",
                    }
                ],
                "declared_impact": "Must be rejected at the API boundary.",
                "verification_probes": ["revision-match"],
                "rollback_reference": "dry-run-no-change",
                "idempotency_key": "api-rejected-plan",
            },
        )

    assert approval_response.status_code == 201
    assert execution_response.status_code == 200
    assert execution_response.json()["status"] == "SUCCEEDED"
    assert attempts_response.status_code == 200
    assert len(attempts_response.json()) == 1
    assert rejected_operation.status_code == 422
