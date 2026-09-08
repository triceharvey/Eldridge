from datetime import UTC, datetime, timedelta
from time import sleep

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from control_plane.api import create_app
from control_plane.domain import (
    AuthorizationError,
    ConflictError,
    ProviderRequest,
    ProviderResult,
    ReconciliationDecision,
    TaskStatus,
)
from control_plane.persistence import (
    AuditEvent,
    ExecutionReconciliation,
    Task,
    TaskAttempt,
)
from control_plane.providers import MockProvider
from control_plane.service import ControlPlaneService, PreparedExecution


class InspectingProvider:
    """Records the committed task state visible when provider work starts."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory
        self.delegate = MockProvider()
        self.observed_status: str | None = None

    @property
    def name(self) -> str:
        return self.delegate.name

    def capabilities(self) -> frozenset[str]:
        return self.delegate.capabilities()

    def submit(self, request: ProviderRequest) -> ProviderResult:
        with self.session_factory() as session:
            task = session.get(Task, request.task_id)
            assert task is not None
            self.observed_status = task.status
        return self.delegate.submit(request)

    def cancel(self, run_id: str) -> bool:
        return self.delegate.cancel(run_id)

    def health(self) -> bool:
        return self.delegate.health()


class SlowProvider(MockProvider):
    def submit(self, request: ProviderRequest) -> ProviderResult:
        sleep(0.06)
        return super().submit(request)


def _leased_execution(
    service: ControlPlaneService, *, key: str
) -> tuple[dict[str, object], dict[str, object], PreparedExecution]:
    workflow = service.create_workflow(
        requester_id="dev-operator",
        title="Execution reconciliation",
        description="Exercise durable running leases and human reconciliation.",
        idempotency_key=key,
    )
    leased = service.lease_next_task(worker_id="orchestrator")
    assert leased is not None
    prepared = service._prepare_execution(
        str(leased["id"]), str(leased["lease_token"]), "orchestrator"
    )
    assert isinstance(prepared, PreparedExecution)
    return workflow, leased, prepared


def _expire_running_task(service: ControlPlaneService, task_id: str) -> None:
    with service.session_factory() as session, session.begin():
        task = session.get(Task, task_id)
        assert task is not None
        task.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)


def test_provider_call_observes_committed_running_state(
    session_factory: sessionmaker[Session],
) -> None:
    provider = InspectingProvider(session_factory)
    service = ControlPlaneService(session_factory, provider=provider)
    service.create_workflow(
        requester_id="dev-operator",
        title="Committed execution",
        description="Prove provider I/O begins after the running state commits.",
        idempotency_key="committed-running-state",
    )
    leased = service.lease_next_task(worker_id="orchestrator")
    assert leased is not None

    result = service.execute_leased_task(
        task_id=str(leased["id"]),
        lease_token=str(leased["lease_token"]),
        worker_id="orchestrator",
    )

    assert provider.observed_status == TaskStatus.RUNNING.value
    assert result["status"] == TaskStatus.SUCCEEDED.value


def test_running_lease_heartbeat_extends_expiry(service: ControlPlaneService) -> None:
    workflow, leased, _prepared = _leased_execution(service, key="heartbeat-extension")
    with service.session_factory() as session:
        task = session.get(Task, str(leased["id"]))
        assert task is not None and task.lease_expires_at is not None
        prior_expiry = task.lease_expires_at

    service.heartbeat_task(
        task_id=str(leased["id"]),
        lease_token=str(leased["lease_token"]),
        worker_id="orchestrator",
    )

    with service.session_factory() as session:
        task = session.get(Task, str(leased["id"]))
        assert task is not None and task.lease_expires_at is not None
        assert task.lease_expires_at > prior_expiry
        heartbeat = session.scalar(
            select(AuditEvent).where(
                AuditEvent.workflow_id == str(workflow["id"]),
                AuditEvent.event_type == "task.lease_heartbeat",
            )
        )
        assert heartbeat is not None


def test_execution_heartbeats_while_provider_work_is_in_flight(
    session_factory: sessionmaker[Session],
) -> None:
    service = ControlPlaneService(
        session_factory,
        provider=SlowProvider(),
        heartbeat_interval_seconds=0.01,
    )
    workflow = service.create_workflow(
        requester_id="dev-operator",
        title="Automatic heartbeat",
        description="Keep the execution lease alive during slow provider work.",
        idempotency_key="automatic-heartbeat",
    )
    leased = service.lease_next_task(worker_id="orchestrator")
    assert leased is not None

    result = service.execute_leased_task(
        task_id=str(leased["id"]),
        lease_token=str(leased["lease_token"]),
        worker_id="orchestrator",
    )

    assert result["status"] == TaskStatus.SUCCEEDED.value
    events = service.list_events(str(workflow["id"]), principal_id="dev-operator")
    assert any(event["event_type"] == "task.lease_heartbeat" for event in events)


def test_expired_running_task_requires_human_retry_reconciliation(
    service: ControlPlaneService,
) -> None:
    workflow, leased, prepared = _leased_execution(service, key="reconcile-retry")
    _expire_running_task(service, prepared.task_id)

    assert service.reclaim_expired_tasks(worker_id="orchestrator") == 1
    blocked = service.get_workflow(str(workflow["id"]), principal_id="dev-operator")
    assert blocked["state"] == "BLOCKED"
    assert blocked["block_reason"] == "EXECUTION_RECONCILIATION_REQUIRED"
    with pytest.raises(AuthorizationError, match="human"):
        service.reconcile_execution(
            workflow_id=prepared.workflow_id,
            task_id=prepared.task_id,
            actor_id="orchestrator",
            decision=ReconciliationDecision.RETRY,
            rationale="Service identities cannot reconcile unknown effects.",
        )

    reconciled = service.reconcile_execution(
        workflow_id=prepared.workflow_id,
        task_id=prepared.task_id,
        actor_id="dev-operator",
        decision=ReconciliationDecision.RETRY,
        rationale="The remote operation was verified absent; a fresh attempt is safe.",
    )

    assert reconciled["state"] == "PLANNING"
    assert reconciled["block_reason"] is None
    with service.session_factory() as session:
        old_task = session.get(Task, prepared.task_id)
        assert old_task is not None
        assert old_task.status == TaskStatus.RECONCILED.value
        new_task = session.scalar(
            select(Task).where(
                Task.workflow_id == prepared.workflow_id,
                Task.id != prepared.task_id,
                Task.status == TaskStatus.READY.value,
            )
        )
        attempt = session.get(TaskAttempt, prepared.attempt_id)
        record = session.scalar(
            select(ExecutionReconciliation).where(
                ExecutionReconciliation.attempt_id == prepared.attempt_id
            )
        )
        assert new_task is not None
        assert attempt is not None and attempt.status == TaskStatus.TIMED_OUT.value
        assert attempt.error_code == "LeaseExpired"
        assert record is not None and record.decision == ReconciliationDecision.RETRY.value

    provider_result = MockProvider().submit(prepared.request)
    execution = service.executor.execute(prepared.task_kind, provider_result, prepared.context)
    with pytest.raises(ConflictError, match="active running lease"):
        service._finalize_execution_success(
            prepared,
            provider_result,
            execution,
            candidate_revision=None,
            latency_ms=1,
        )


def test_human_can_reconcile_unknown_execution_as_failed(
    service: ControlPlaneService,
) -> None:
    _workflow, _leased, prepared = _leased_execution(service, key="reconcile-fail")
    _expire_running_task(service, prepared.task_id)
    service.reclaim_expired_tasks(worker_id="orchestrator")

    failed = service.reconcile_execution(
        workflow_id=prepared.workflow_id,
        task_id=prepared.task_id,
        actor_id="dev-operator",
        decision=ReconciliationDecision.FAIL,
        rationale="The external effect cannot be proven safe to retry.",
    )

    assert failed["state"] == "FAILED"
    assert failed["block_reason"] == "EXECUTION_RECONCILED_FAILED"


def test_reconciliation_api_records_human_decision(service: ControlPlaneService) -> None:
    _workflow, _leased, prepared = _leased_execution(service, key="reconcile-api")
    _expire_running_task(service, prepared.task_id)
    service.reclaim_expired_tasks(worker_id="orchestrator")

    with TestClient(create_app(service)) as client:
        response = client.post(
            f"/workflows/{prepared.workflow_id}/reconcile",
            json={
                "task_id": prepared.task_id,
                "decision": "FAIL",
                "rationale": "Operator verified that retry would risk a duplicate effect.",
            },
        )

    assert response.status_code == 200
    assert response.json()["state"] == "FAILED"


def test_service_rejects_invalid_heartbeat_intervals(
    session_factory: sessionmaker[Session],
) -> None:
    with pytest.raises(ValueError, match="positive"):
        ControlPlaneService(session_factory, heartbeat_interval_seconds=0)
    with pytest.raises(ValueError, match="shorter"):
        ControlPlaneService(
            session_factory,
            lease_seconds=5,
            heartbeat_interval_seconds=5,
        )
