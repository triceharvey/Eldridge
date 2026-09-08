import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from control_plane.persistence import Base, make_engine, make_session_factory, seed_principals
from control_plane.service import ControlPlaneService


@pytest.mark.postgres
def test_postgres_task_lease_is_exclusive() -> None:
    database_url = os.getenv("CONTROL_PLANE_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("CONTROL_PLANE_TEST_DATABASE_URL is not configured")
    schema = f"control_plane_test_{uuid4().hex}"
    admin_engine = make_engine(database_url)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        database_url,
        connect_args={"options": f"-csearch_path={schema}"},
        pool_pre_ping=True,
    )
    try:
        Base.metadata.create_all(engine)
        factory = make_session_factory(engine)
        with factory() as session:
            seed_principals(session)
        service = ControlPlaneService(factory)
        service.create_workflow(
            requester_id="dev-operator",
            title="PostgreSQL lease",
            description="Verify only one worker can claim the ready task.",
            idempotency_key="postgres-lease-key",
        )
        first = service.lease_next_task(worker_id="orchestrator")
        second = service.lease_next_task(worker_id="orchestrator")
        assert first is not None
        assert second is None
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()
