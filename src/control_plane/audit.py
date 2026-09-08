from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from control_plane.persistence import AuditEvent

GENESIS_HASH = "0" * 64


def append_audit_event(
    session: Session,
    *,
    workflow_id: str,
    event_type: str,
    actor_id: str,
    resource_type: str,
    resource_id: str,
    outcome: str,
    payload: dict[str, Any],
) -> AuditEvent:
    latest = session.scalar(
        select(AuditEvent)
        .where(AuditEvent.workflow_id == workflow_id)
        .order_by(AuditEvent.sequence.desc())
        .limit(1)
    )
    sequence = 1 if latest is None else latest.sequence + 1
    previous_hash = GENESIS_HASH if latest is None else latest.event_hash
    canonical = json.dumps(
        {
            "workflow_id": workflow_id,
            "sequence": sequence,
            "event_type": event_type,
            "actor_id": actor_id,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "outcome": outcome,
            "payload": payload,
            "previous_hash": previous_hash,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    event = AuditEvent(
        workflow_id=workflow_id,
        sequence=sequence,
        event_type=event_type,
        actor_id=actor_id,
        resource_type=resource_type,
        resource_id=resource_id,
        outcome=outcome,
        payload=payload,
        previous_hash=previous_hash,
        event_hash=sha256(canonical.encode()).hexdigest(),
    )
    session.add(event)
    return event


def verify_audit_chain(session: Session, workflow_id: str) -> bool:
    events = session.scalars(
        select(AuditEvent)
        .where(AuditEvent.workflow_id == workflow_id)
        .order_by(AuditEvent.sequence)
    ).all()
    previous = GENESIS_HASH
    for expected_sequence, event in enumerate(events, start=1):
        if event.sequence != expected_sequence or event.previous_hash != previous:
            return False
        canonical = json.dumps(
            {
                "workflow_id": event.workflow_id,
                "sequence": event.sequence,
                "event_type": event.event_type,
                "actor_id": event.actor_id,
                "resource_type": event.resource_type,
                "resource_id": event.resource_id,
                "outcome": event.outcome,
                "payload": event.payload,
                "previous_hash": event.previous_hash,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        if sha256(canonical.encode()).hexdigest() != event.event_hash:
            return False
        previous = event.event_hash
    return bool(events)


def audit_count(session: Session, workflow_id: str) -> int:
    return (
        session.scalar(
            select(func.count(AuditEvent.id)).where(AuditEvent.workflow_id == workflow_id)
        )
        or 0
    )
