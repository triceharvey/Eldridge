from sqlalchemy import select

from control_plane.audit import verify_audit_chain
from control_plane.persistence import AuditEvent
from control_plane.service import ControlPlaneService


def test_audit_chain_detects_tampering(service: ControlPlaneService) -> None:
    workflow = service.create_workflow(
        requester_id="dev-operator",
        title="Audit integrity",
        description="Test the Phase 1 tamper-evident chain.",
        idempotency_key="audit-chain-key",
    )
    with service.session_factory() as session:
        assert verify_audit_chain(session, str(workflow["id"]))
    with service.session_factory() as session, session.begin():
        event = session.scalar(
            select(AuditEvent)
            .where(AuditEvent.workflow_id == workflow["id"])
            .order_by(AuditEvent.sequence)
        )
        assert event is not None
        event.payload = {"tampered": True}
    with service.session_factory() as session:
        assert not verify_audit_chain(session, str(workflow["id"]))
