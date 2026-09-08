from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from control_plane.domain import AuthorizationError, ConflictError, TaskKind
from control_plane.persistence import Task, TaskAttempt
from control_plane.providers import MockProvider
from control_plane.service import ControlPlaneService


def _workflow(service: ControlPlaneService, key: str = "lease-failure-key") -> dict[str, object]:
    return service.create_workflow(
        requester_id="dev-operator",
        title="Lease and failure",
        description="Test lease and bounded retry behavior.",
        idempotency_key=key,
    )


def test_wrong_lease_token_is_denied(service: ControlPlaneService) -> None:
    _workflow(service)
    task = service.lease_next_task(worker_id="orchestrator")
    assert task is not None
    with pytest.raises(AuthorizationError, match="token"):
        service.execute_leased_task(
            task_id=task["id"], lease_token=str(uuid4()), worker_id="orchestrator"
        )


def test_task_cannot_be_leased_twice(service: ControlPlaneService) -> None:
    _workflow(service)
    first = service.lease_next_task(worker_id="orchestrator")
    second = service.lease_next_task(worker_id="orchestrator")
    assert first is not None
    assert second is None


def test_provider_failure_retries_then_fails_workflow(session_factory) -> None:
    service = ControlPlaneService(
        session_factory,
        provider=MockProvider(fail_for=frozenset({TaskKind.PLAN})),
    )
    workflow = _workflow(service, "provider-failure-key")
    first = service.lease_next_task(worker_id="orchestrator")
    assert first is not None
    result = service.execute_leased_task(
        task_id=first["id"],
        lease_token=first["lease_token"],
        worker_id="orchestrator",
    )
    assert result["status"] == "READY"
    second = service.lease_next_task(worker_id="orchestrator")
    assert second is not None
    result = service.execute_leased_task(
        task_id=second["id"],
        lease_token=second["lease_token"],
        worker_id="orchestrator",
    )
    assert result["status"] == "FAILED"
    current = service.get_workflow(str(workflow["id"]), principal_id="dev-operator")
    assert current["state"] == "FAILED"
    with service.session_factory() as session:
        attempts = session.scalars(select(TaskAttempt)).all()
        assert [attempt.attempt_number for attempt in attempts] == [1, 2]
        assert all(attempt.error_code == "TimeoutError" for attempt in attempts)


def test_completed_task_rejects_replay(service: ControlPlaneService) -> None:
    _workflow(service)
    task = service.lease_next_task(worker_id="orchestrator")
    assert task is not None
    service.execute_leased_task(
        task_id=task["id"],
        lease_token=task["lease_token"],
        worker_id="orchestrator",
    )
    with pytest.raises(ConflictError, match="active lease"):
        service.execute_leased_task(
            task_id=task["id"],
            lease_token=task["lease_token"],
            worker_id="orchestrator",
        )


def test_expired_mock_lease_can_be_reclaimed(service: ControlPlaneService) -> None:
    _workflow(service, "expired-lease-key")
    leased = service.lease_next_task(worker_id="orchestrator")
    assert leased is not None
    with service.session_factory() as session, session.begin():
        task = session.get(Task, leased["id"])
        assert task is not None
        task.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert service.reclaim_expired_tasks(worker_id="orchestrator") == 1
    reclaimed = service.lease_next_task(worker_id="orchestrator")
    assert reclaimed is not None
    assert reclaimed["id"] == leased["id"]
    assert reclaimed["lease_token"] != leased["lease_token"]
