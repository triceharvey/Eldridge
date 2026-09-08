from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session, sessionmaker

from control_plane.persistence import (
    initialize_database,
    make_engine,
    make_session_factory,
    seed_principals,
)
from control_plane.service import ControlPlaneService


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = make_engine("sqlite:///:memory:")
    initialize_database(engine)
    factory = make_session_factory(engine)
    with factory() as session:
        seed_principals(session)
    yield factory
    engine.dispose()


@pytest.fixture
def service(session_factory: sessionmaker[Session]) -> ControlPlaneService:
    return ControlPlaneService(session_factory)


def drive_to_human_gate(service: ControlPlaneService) -> dict[str, object]:
    workflow = service.create_workflow(
        requester_id="dev-operator",
        title="Test workflow",
        description="Exercise the deterministic workflow.",
        idempotency_key="test-workflow-key",
    )
    while workflow["state"] != "AWAITING_HUMAN_APPROVAL":
        task = service.lease_next_task(worker_id="orchestrator")
        assert task is not None
        service.execute_leased_task(
            task_id=task["id"],
            lease_token=task["lease_token"],
            worker_id="orchestrator",
        )
        workflow = service.get_workflow(workflow["id"], principal_id="dev-operator")
    return workflow
