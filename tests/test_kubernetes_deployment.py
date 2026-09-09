from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from control_plane.api import create_app
from control_plane.audit import verify_audit_chain
from control_plane.credentials import (
    CredentialOperation,
    CredentialUseResult,
    WorkloadCredentialHandle,
    WorkloadCredentialRequest,
)
from control_plane.deployment import (
    CredentialedDeploymentRun,
    DeploymentPlan,
    DryRunDeploymentAdapter,
    EnvironmentClassification,
)
from control_plane.domain import (
    ApprovalAction,
    ApprovalDecision,
    ConflictError,
    DeploymentOutcomeUnknownError,
    ValidationError,
    WorkflowState,
)
from control_plane.kubernetes_deployment import LocalK3dConfigMapAdapter
from control_plane.persistence import DeploymentAttemptRecord, Workflow
from control_plane.service import ControlPlaneService

ARTIFACT_DIGEST = "sha256:" + "a" * 64
MERGED_REVISION = "b" * 40
PLAN_DIGEST = "c" * 64
TOKEN = "phase-4-3-secret-token-canary"  # noqa: S105 - redaction-test canary


class ReleaseMarkerTransport:
    def __init__(self, *, fail_put: bool = False) -> None:
        self.data: dict[str, str] = {}
        self.resource_version = 1
        self.fail_put = fail_put
        self.put_count = 0
        self.authorization_headers: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.authorization_headers.append(request.headers.get("authorization", ""))
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "apiVersion": "v1",
                    "kind": "ConfigMap",
                    "metadata": {
                        "name": "eldridge-release",
                        "namespace": "eldridge-validation",
                        "resourceVersion": str(self.resource_version),
                    },
                    "data": self.data,
                },
            )
        if request.method == "PUT":
            self.put_count += 1
            if self.fail_put:
                raise httpx.ReadTimeout("ambiguous update", request=request)
            payload = json.loads(request.content)
            self.data = dict(payload["data"])
            self.resource_version += 1
            return httpx.Response(200, json=payload)
        return httpx.Response(405)


def _plan(**overrides: object) -> DeploymentPlan:
    values: dict[str, object] = {
        "plan_id": "plan-1",
        "workflow_id": "workflow-1",
        "environment_id": "eldridge-local-k3d",
        "revision": MERGED_REVISION,
        "artifact_digests": (ARTIFACT_DIGEST,),
        "operations": (
            {
                "kind": "UPDATE_SERVICE",
                "resource_id": "configmap/eldridge-release",
                "artifact_digest": ARTIFACT_DIGEST,
            },
        ),
        "verification_probes": (
            "revision-match",
            "plan-digest-match",
            "artifact-digest-match",
        ),
        "rollback_reference": "restore-previous-release-marker-v1",
        "policy_version": "deployment/local-k3d-v1",
        "digest": PLAN_DIGEST,
    }
    values.update(overrides)
    return DeploymentPlan(**values)  # type: ignore[arg-type]


def _handle(plan: DeploymentPlan) -> WorkloadCredentialHandle:
    now = datetime.now(UTC)
    return WorkloadCredentialHandle(
        reference="kubernetes-token:test-reference",
        broker_id="test-session-broker",
        environment_id=plan.environment_id,
        plan_digest=plan.digest,
        operation=CredentialOperation.APPLY_PLAN,
        audience="https://kubernetes.default.svc.cluster.local",
        subject="system:serviceaccount:eldridge-validation:deployment-worker",
        resource_scope=("namespace/eldridge-validation/configmap/eldridge-release",),
        issued_at=now,
        expires_at=now + timedelta(seconds=600),
        simulated=False,
    )


def _adapter(transport: ReleaseMarkerTransport) -> LocalK3dConfigMapAdapter:
    return LocalK3dConfigMapAdapter(
        client=httpx.Client(
            base_url="https://127.0.0.1:6443",
            transport=httpx.MockTransport(transport),
        ),
        api_server="https://127.0.0.1:6443",
    )


def test_local_k3d_adapter_applies_verifies_and_replays_idempotently() -> None:
    transport = ReleaseMarkerTransport()
    adapter = _adapter(transport)
    plan = _plan()
    handle = _handle(plan)

    first = adapter.run_with_credential(plan, TOKEN, handle, idempotency_key="attempt-1")
    replay = adapter.run_with_credential(plan, TOKEN, handle, idempotency_key="attempt-1")

    assert first.execution.changed is True
    assert first.verification.passed is True
    assert first.observation.observed_revision == MERGED_REVISION
    assert replay.execution.changed is False
    assert replay.verification.passed is True
    assert transport.put_count == 1
    assert transport.authorization_headers == [f"Bearer {TOKEN}"] * 5
    assert TOKEN not in repr(first)


def test_local_k3d_adapter_rejects_scope_widening_before_contact() -> None:
    transport = ReleaseMarkerTransport()
    adapter = _adapter(transport)
    with pytest.raises(ValidationError, match="resource does not match policy"):
        adapter.validate_plan(
            _plan(
                operations=(
                    {
                        "kind": "UPDATE_SERVICE",
                        "resource_id": "configmap/other",
                        "artifact_digest": ARTIFACT_DIGEST,
                    },
                )
            )
        )
    assert transport.authorization_headers == []


def test_local_k3d_adapter_rejects_non_loopback_api() -> None:
    transport = ReleaseMarkerTransport()
    with pytest.raises(ValidationError, match="loopback HTTPS"):
        LocalK3dConfigMapAdapter(
            client=httpx.Client(transport=httpx.MockTransport(transport)),
            api_server="https://cluster.example.com",
        )
    assert transport.authorization_headers == []


def test_local_k3d_adapter_contains_ambiguous_update() -> None:
    transport = ReleaseMarkerTransport(fail_put=True)
    adapter = _adapter(transport)
    plan = _plan()
    with pytest.raises(DeploymentOutcomeUnknownError, match="requires reconciliation"):
        adapter.run_with_credential(plan, TOKEN, _handle(plan), idempotency_key="attempt-unknown")
    assert transport.put_count == 1


class StubSessionBroker:
    broker_id = "test-session-broker"

    def __init__(self) -> None:
        self.requests: list[WorkloadCredentialRequest] = []

    def issue(
        self,
        request: WorkloadCredentialRequest,
        *,
        now: datetime | None = None,
    ) -> WorkloadCredentialHandle:
        del now
        self.requests.append(request)
        return _handle_for_request(request)

    def run(
        self,
        request: WorkloadCredentialRequest,
        consumer: Callable[[str, WorkloadCredentialHandle], CredentialedDeploymentRun],
        *,
        now: datetime | None = None,
    ) -> CredentialUseResult[CredentialedDeploymentRun]:
        del now
        self.requests.append(request)
        handle = _handle_for_request(request)
        result = consumer(TOKEN, handle)
        return CredentialUseResult(handle=handle, result=result)


class StubBrokerFactory:
    broker_id = "test-session-broker"

    def __init__(self, broker: StubSessionBroker) -> None:
        self.broker = broker
        self.plan_digests: list[str] = []

    def for_plan(self, plan_digest: str) -> StubSessionBroker:
        self.plan_digests.append(plan_digest)
        return self.broker


def _handle_for_request(request: WorkloadCredentialRequest) -> WorkloadCredentialHandle:
    now = datetime.now(UTC)
    return WorkloadCredentialHandle(
        reference="kubernetes-token:service-test-reference",
        broker_id="test-session-broker",
        environment_id=request.environment_id,
        plan_digest=request.plan_digest,
        operation=request.operation,
        audience=request.audience,
        subject=request.subject,
        resource_scope=request.resource_scope,
        issued_at=now,
        expires_at=now + timedelta(seconds=request.lifetime_seconds),
        simulated=False,
    )


def _merged_workflow(service: ControlPlaneService) -> dict[str, object]:
    created = service.create_workflow(
        requester_id="dev-operator",
        title="Local k3d release marker",
        description="Validate an exact approved local non-production change.",
        idempotency_key="local-k3d-workflow",
        repository_scope="triceharvey/Eldridge",
    )
    with service.session_factory() as session, session.begin():
        workflow = session.get(Workflow, str(created["id"]))
        assert workflow is not None
        workflow.state = WorkflowState.MERGED.value
        workflow.candidate_revision = "d" * 40
        workflow.merged_revision = MERGED_REVISION
    return service.get_workflow(str(created["id"]), principal_id="dev-operator")


def _approved_local_plan(
    session_factory: sessionmaker[Session],
    *,
    transport: ReleaseMarkerTransport | None = None,
) -> tuple[
    ControlPlaneService,
    dict[str, object],
    dict[str, object],
    StubSessionBroker,
    StubBrokerFactory,
]:
    selected_transport = transport or ReleaseMarkerTransport()
    adapter = _adapter(selected_transport)
    broker = StubSessionBroker()
    broker_factory = StubBrokerFactory(broker)
    service = ControlPlaneService(
        session_factory,
        deployment_adapters=(DryRunDeploymentAdapter(), adapter),
        credential_broker_factory=broker_factory,
        enable_local_deployment=True,
    )
    workflow = _merged_workflow(service)
    environment = service.register_deployment_environment(
        actor_id="dev-operator",
        environment_id="eldridge-local-k3d",
        name="Ephemeral local k3d",
        classification=EnvironmentClassification.DEVELOPMENT,
        repository="triceharvey/Eldridge",
        base_branch="main",
        resource_scope=(adapter.resource_id,),
        required_checks=(),
        required_attestations=(),
        verification_policy=adapter.verification_probes,
        rollback_policy=adapter.rollback_reference,
        policy_version=adapter.policy_version,
        provider="local-k3d",
        account_scope="local",
        region="local",
        adapter_id=adapter.adapter_id,
    )
    plan = service.create_deployment_plan(
        workflow_id=str(workflow["id"]),
        actor_id="dev-operator",
        environment_id=str(environment["id"]),
        artifact_digests=(ARTIFACT_DIGEST,),
        operations=_plan().operations,
        declared_impact="Update one pre-created local release-marker ConfigMap.",
        verification_probes=adapter.verification_probes,
        rollback_reference=adapter.rollback_reference,
        idempotency_key="local-k3d-plan",
    )
    service.approve(
        workflow_id=str(workflow["id"]),
        approver_id="dev-operator",
        action=ApprovalAction.DEPLOY,
        target="eldridge-local-k3d",
        revision=MERGED_REVISION,
        decision=ApprovalDecision.APPROVED,
        rationale="Approve the exact local release-marker update.",
        environment_id="eldridge-local-k3d",
        plan_digest=str(plan["digest"]),
    )
    return service, workflow, plan, broker, broker_factory


def test_service_executes_only_exact_approved_local_plan(
    session_factory: sessionmaker[Session],
) -> None:
    service, workflow, plan, broker, broker_factory = _approved_local_plan(session_factory)

    path = f"/workflows/{workflow['id']}/deployment-plans/{plan['id']}/local-execution"
    with TestClient(create_app(service)) as client:
        response = client.post(path, json={"idempotency_key": "local-k3d-execution"})
        replay_response = client.post(path, json={"idempotency_key": "local-k3d-execution"})
    assert response.status_code == 200
    assert replay_response.status_code == 200
    result = response.json()
    replay = replay_response.json()

    assert replay["id"] == result["id"]
    assert result["status"] == "SUCCEEDED"
    assert result["simulated"] is False
    assert result["result"]["changed"] is True
    assert result["result"]["credential"]["plan_digest"] == plan["digest"]
    assert TOKEN not in repr(result)
    assert len(broker.requests) == 1
    assert broker_factory.plan_digests == [plan["digest"]]
    current = service.get_workflow(str(workflow["id"]), principal_id="dev-operator")
    assert current["state"] == WorkflowState.DEPLOYED.value
    with session_factory() as session:
        attempt = session.scalar(
            select(DeploymentAttemptRecord).where(
                DeploymentAttemptRecord.workflow_id == workflow["id"]
            )
        )
        assert attempt is not None and attempt.status == "SUCCEEDED"
        assert verify_audit_chain(session, str(workflow["id"]))


def test_service_contains_ambiguous_local_update_without_retry(
    session_factory: sessionmaker[Session],
) -> None:
    transport = ReleaseMarkerTransport(fail_put=True)
    service, workflow, plan, broker, broker_factory = _approved_local_plan(
        session_factory, transport=transport
    )

    with pytest.raises(DeploymentOutcomeUnknownError):
        service.execute_local_deployment(
            workflow_id=str(workflow["id"]),
            plan_id=str(plan["id"]),
            actor_id="dev-operator",
            idempotency_key="local-k3d-unknown",
        )
    with pytest.raises(ConflictError, match="not replayable"):
        service.execute_local_deployment(
            workflow_id=str(workflow["id"]),
            plan_id=str(plan["id"]),
            actor_id="dev-operator",
            idempotency_key="local-k3d-unknown",
        )

    assert transport.put_count == 1
    assert len(broker.requests) == 1
    assert broker_factory.plan_digests == [plan["digest"]]
    with session_factory() as session:
        attempt = session.scalar(
            select(DeploymentAttemptRecord).where(
                DeploymentAttemptRecord.workflow_id == workflow["id"]
            )
        )
        assert attempt is not None and attempt.status == "UNKNOWN"
        assert verify_audit_chain(session, str(workflow["id"]))
