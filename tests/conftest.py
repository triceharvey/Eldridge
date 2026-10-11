from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

import control_plane
from control_plane.persistence import (
    initialize_database,
    make_engine,
    make_session_factory,
    seed_principals,
)
from control_plane.service import ControlPlaneService

_checkout_package = Path(__file__).resolve().parents[1] / "src" / "control_plane"
_imported_package = Path(control_plane.__file__).resolve().parent
if _imported_package != _checkout_package:
    raise pytest.UsageError(
        "Tests imported control_plane from a different checkout: "
        f"{_imported_package}; expected {_checkout_package}. "
        "Install this checkout or run with PYTHONPATH=src."
    )


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
